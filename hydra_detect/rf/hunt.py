"""RF hunt controller — state machine for autonomous RF source localization.

State machine::

    IDLE ──▶ SEARCHING ──▶ HOMING ──▶ CONVERGED
                 ▲            │
                 └── LOST ◀───┘

Runs as a background thread alongside (or instead of) the visual pipeline.
Uses the shared MAVLinkIO for vehicle control and GPS.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from collections import deque
from enum import Enum
from typing import Callable

from ..autonomous import haversine_m
from .kismet_client import KismetClient
from .kismet_manager import KismetManager
from .navigator import GradientNavigator
from .search import generate_lawnmower, generate_spiral
from .signal import RSSIFilter

logger = logging.getLogger(__name__)
audit_log = logging.getLogger("hydra.audit")

# Parameter bounds — prevents waypoint explosion or nonsensical config.
_SEARCH_AREA_MIN, _SEARCH_AREA_MAX = 10.0, 2000.0
_SEARCH_SPACING_MIN, _SEARCH_SPACING_MAX = 2.0, 200.0
_SEARCH_ALT_MIN, _SEARCH_ALT_MAX = 3.0, 120.0


class HuntState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    SEARCHING = "searching"
    HOMING = "homing"
    CONVERGED = "converged"
    LOST = "lost"
    ABORTED = "aborted"


class RFHuntController:
    """Orchestrates RF source localization via Kismet + ArduPilot.

    Designed to plug into the Hydra pipeline:
    - Uses the shared MAVLinkIO instance for GPS reads and GUIDED commands
    - Runs in its own daemon thread so it doesn't block the detection loop
    - Reports status via callbacks and MAVLink STATUSTEXT

    All public methods and properties are thread-safe: they can be called
    from the web API thread while the hunt loop runs in the background.

    Args:
        mavlink: Shared MAVLinkIO instance for GPS and vehicle commands.
        mode: ``"wifi"`` (hunt by BSSID) or ``"sdr"`` (hunt by frequency).
        target_bssid: MAC address to locate (WiFi mode).
        target_freq_mhz: Frequency in MHz to locate (SDR mode).
        kismet_host: Kismet REST API base URL.
        kismet_user: Kismet API username.
        kismet_pass: Kismet API password.
        search_pattern: ``"lawnmower"`` or ``"spiral"``.
        search_area_m: Search area size in metres (10-2000).
        search_spacing_m: Grid spacing between search legs (2-200).
        search_alt_m: Search altitude in metres (3-120).
        rssi_threshold_dbm: RSSI level to switch from search to homing.
        rssi_converge_dbm: RSSI level to declare source found.
        rssi_window: Number of samples for RSSI sliding window average.
        gradient_step_m: Step size in metres for gradient ascent probes.
        gradient_rotation_deg: Degrees to rotate after signal drops.
        poll_interval_sec: Seconds between RSSI polls.
        arrival_tolerance_m: Distance to consider a waypoint reached.
        on_state_change: Optional callback invoked on state transitions.
    """

    def __init__(
        self,
        mavlink,  # MAVLinkIO instance (shared with pipeline)
        *,
        # Target specification
        mode: str = "wifi",
        target_bssid: str | None = None,
        target_freq_mhz: float | None = None,
        # Kismet connection
        kismet_host: str = "http://localhost:2501",
        kismet_user: str = "",
        kismet_pass: str = "",
        # Search pattern
        search_pattern: str = "lawnmower",  # "lawnmower" or "spiral"
        search_area_m: float = 100.0,
        search_spacing_m: float = 20.0,
        search_alt_m: float = 15.0,
        # RSSI thresholds
        rssi_threshold_dbm: float = -80.0,
        rssi_converge_dbm: float = -40.0,
        rssi_window: int = 10,
        # Gradient
        gradient_step_m: float = 5.0,
        gradient_rotation_deg: float = 45.0,
        # Timing
        poll_interval_sec: float = 0.5,
        arrival_tolerance_m: float = 3.0,
        # Callbacks
        on_state_change: Callable[[HuntState], None] | None = None,
        kismet_manager: KismetManager | None = None,
        gps_required: bool = True,
        # Geofence callbacks (from autonomous controller)
        geofence_check: Callable[[float, float], bool] | None = None,
        geofence_clip: Callable[[float, float], tuple[float, float]] | None = None,
        # RF data source — inject to replace the default KismetClient.
        # Any object implementing the KismetDataSource protocol works
        # (see hydra_detect.rf.replay_source).
        client: object | None = None,
    ):
        self._mavlink = mavlink
        self._mode = mode
        self._target_bssid = target_bssid
        self._target_freq_mhz = target_freq_mhz
        self._search_pattern = search_pattern
        self._search_area_m = max(_SEARCH_AREA_MIN, min(search_area_m, _SEARCH_AREA_MAX))
        clamped_spacing = min(search_spacing_m, _SEARCH_SPACING_MAX)
        self._search_spacing_m = max(_SEARCH_SPACING_MIN, clamped_spacing)
        self._search_alt_m = max(_SEARCH_ALT_MIN, min(search_alt_m, _SEARCH_ALT_MAX))
        self._rssi_threshold = rssi_threshold_dbm
        self._rssi_converge = rssi_converge_dbm
        self._poll_interval = poll_interval_sec
        self._arrival_tolerance = arrival_tolerance_m
        self._on_state_change = on_state_change
        self._kismet_manager = kismet_manager
        self._gps_required = gps_required
        self._check_geofence = geofence_check
        self._clip_to_geofence = geofence_clip
        self._consecutive_clips = 0
        self._MAX_CONSECUTIVE_CLIPS = 3
        self._consecutive_none = 0  # count of consecutive None RSSI reads in homing
        _CONSECUTIVE_NONE_LOST = 4  # how many Nones before declaring LOST
        self._CONSECUTIVE_NONE_LOST = _CONSECUTIVE_NONE_LOST
        self._kismet_restart_pending = False  # guard against concurrent restarts

        if client is not None:
            self._kismet = client
        else:
            self._kismet = KismetClient(
                host=kismet_host, user=kismet_user, password=kismet_pass,
            )
        self._filter = RSSIFilter(window_size=rssi_window)
        self._navigator = GradientNavigator(
            step_m=gradient_step_m,
            rotation_deg=gradient_rotation_deg,
            converge_dbm=rssi_converge_dbm,
        )

        self._state = HuntState.IDLE
        self._waypoints: list[tuple[float, float, float]] = []
        self._wp_index = 0
        # Effective waypoint coords (may differ from original after clipping)
        self._effective_wp: tuple[float, float] | None = None
        self._last_rssi: float = -100.0
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

        # Lock for state reads from other threads (web UI, etc.)
        self._lock = threading.Lock()
        self._rssi_history: deque[dict] = deque(maxlen=300)
        self._state_events: deque[dict] = deque(maxlen=50)
        self._state_entered_at: float = time.time()
        self._state_entered_samples: int = 0

    # -- Public API --------------------------------------------------------

    @property
    def state(self) -> HuntState:
        """Current hunt state (thread-safe)."""
        with self._lock:
            return self._state

    @property
    def best_rssi(self) -> float:
        """Best RSSI reading seen so far in dBm (thread-safe)."""
        return self._navigator.get_best_rssi()

    @property
    def best_position(self) -> tuple[float, float] | None:
        """(lat, lon) of the best RSSI reading, or None if no samples yet (thread-safe)."""
        return self._navigator.get_best_position()

    def get_rssi_history(self) -> list[dict]:
        """Return RSSI history for visualization (thread-safe)."""
        with self._lock:
            return list(self._rssi_history)

    @property
    def sample_count(self) -> int:
        """Number of RSSI samples recorded (thread-safe)."""
        return self._navigator.get_sample_count()

    def get_status(self) -> dict:
        """Return current hunt status for the web UI (thread-safe)."""
        best_rssi = self._navigator.get_best_rssi()
        best_pos = self._navigator.get_best_position()
        sample_count = self._navigator.get_sample_count()
        with self._lock:
            return {
                "state": self._state.value,
                "mode": self._mode,
                "target": self._target_bssid or f"{self._target_freq_mhz} MHz",
                "best_rssi": round(best_rssi, 1),
                "best_lat": round(best_pos[0], 7) if best_pos is not None else None,
                "best_lon": round(best_pos[1], 7) if best_pos is not None else None,
                "samples": sample_count,
                "wp_progress": f"{self._wp_index}/{len(self._waypoints)}",
                "gps_required": self._gps_required,
                "rssi_threshold": self._rssi_threshold,
                "rssi_converge": self._rssi_converge,
            }

    def start(self) -> bool:
        """Start the RF hunt in a background thread.

        Returns False if prerequisites aren't met (no MAVLink, no Kismet,
        no GPS fix).
        """
        if self._mavlink is None:
            logger.error("RF hunt requires MAVLink — aborting")
            return False

        if not self._kismet.check_connection():
            logger.error("Cannot reach Kismet — aborting RF hunt")
            return False

        if self._gps_required:
            # Get current position for search pattern center
            lat, lon, alt = self._mavlink.get_lat_lon()
            if lat is None or lon is None:
                logger.error("RF hunt requires GPS fix — aborting")
                return False

            # Generate search pattern
            if self._search_pattern == "spiral":
                self._waypoints = generate_spiral(
                    lat, lon,
                    max_radius_m=self._search_area_m / 2,
                    spacing_m=self._search_spacing_m,
                    alt=self._search_alt_m,
                )
            else:
                self._waypoints = generate_lawnmower(
                    lat, lon,
                    width_m=self._search_area_m,
                    height_m=self._search_area_m,
                    spacing_m=self._search_spacing_m,
                    alt=self._search_alt_m,
                )
            self._wp_index = 0

            audit_log.info(
                "RF HUNT START: mode=%s target=%s pattern=%s area=%.0fm "
                "threshold=%.0f dBm waypoints=%d",
                self._mode,
                self._target_bssid or f"{self._target_freq_mhz}MHz",
                self._search_pattern,
                self._search_area_m,
                self._rssi_threshold,
                len(self._waypoints),
            )

        self._stop_evt.clear()
        # Reset per-run counters so re-starts are clean
        self._consecutive_clips = 0
        self._wp_index = 0
        self._consecutive_none = 0
        self._navigator.reset()
        self._filter.reset()
        initial_state = HuntState.SCANNING if not self._gps_required else HuntState.SEARCHING
        self._set_state(initial_state)

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="rf-hunt",
        )
        self._thread.start()

        self._mavlink.send_statustext("RF HUNT: Search started", severity=2)
        return True

    def stop(self) -> None:
        """Stop the hunt and wait for the background thread to finish."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                logger.warning("RF hunt thread did not stop within timeout")
            self._thread = None
        self._kismet.close()
        self._set_state(HuntState.ABORTED)
        audit_log.info("RF HUNT STOPPED by operator")

    # -- Internal state machine --------------------------------------------

    def _set_state(self, new_state: HuntState) -> None:
        now = time.time()
        with self._lock:
            old = self._state
            self._state = new_state
            if old != new_state:
                elapsed = max(0.0, now - self._state_entered_at)
                self._state_events.append({
                    "t": round(now, 3),
                    "from": old.value,
                    "to": new_state.value,
                    "samples": self._navigator.get_sample_count(),
                    "elapsed_prev_sec": round(elapsed, 2),
                })
                self._state_entered_at = now
        if old != new_state:
            logger.info("RF Hunt: %s → %s", old.value, new_state.value)
            if self._on_state_change:
                try:
                    self._on_state_change(new_state)
                except Exception as exc:
                    logger.warning("State change callback error: %s", exc)

    def get_state_events(self) -> list[dict]:
        """Return the state-transition ring (thread-safe, ≤50 entries)."""
        with self._lock:
            return list(self._state_events)

    def _run_loop(self) -> None:
        """Main hunt loop — runs in background thread."""
        try:
            while not self._stop_evt.is_set():
                state = self.state
                if state == HuntState.SCANNING:
                    self._do_scan()
                elif state == HuntState.SEARCHING:
                    self._do_search()
                elif state == HuntState.HOMING:
                    self._do_homing()
                elif state == HuntState.LOST:
                    self._do_lost()
                elif state in (HuntState.CONVERGED, HuntState.ABORTED):
                    break
                self._stop_evt.wait(self._poll_interval)
        except Exception as exc:
            logger.error("RF hunt loop error: %s", exc)
            self._set_state(HuntState.ABORTED)
        finally:
            self._kismet.close()
            self._report_results()

    def _record_rssi(
        self, rssi: float,
        lat: float | None = None, lon: float | None = None,
    ) -> None:
        """Append an RSSI reading to the history ring buffer."""
        if lat is None or lon is None:
            lat, lon, _ = self._mavlink.get_lat_lon()
        with self._lock:
            self._rssi_history.append({
                "t": time.time(),
                "rssi": round(rssi, 1),
                "lat": round(lat, 7) if lat is not None else None,
                "lon": round(lon, 7) if lon is not None else None,
            })

    def _geofence_waypoint(
        self, wp_lat: float, wp_lon: float, wp_alt: float,
    ) -> bool:
        """Validate waypoint against geofence, clip if needed, then send.

        Returns True if the waypoint was sent (possibly clipped).
        Returns False if the waypoint was suppressed (converged due to
        repeated clips, or no clip callback available).

        Stores the actual sent coordinates in ``_effective_wp`` so callers
        can use them for arrival distance checks instead of the original.
        """
        if self._check_geofence is not None and not self._check_geofence(wp_lat, wp_lon):
            if self._clip_to_geofence is not None:
                wp_lat, wp_lon = self._clip_to_geofence(wp_lat, wp_lon)
                self._consecutive_clips += 1
                logger.warning(
                    "RF hunt waypoint clipped to geofence (clip #%d)",
                    self._consecutive_clips,
                )
                if self._consecutive_clips >= self._MAX_CONSECUTIVE_CLIPS:
                    logger.warning("RF: SIGNAL BEYOND GEOFENCE — converging at boundary")
                    self._set_state(HuntState.CONVERGED)
                    if self._mavlink:
                        self._mavlink.send_statustext(
                            "RF: SIGNAL BEYOND GEOFENCE", severity=4,
                        )
                    return False
            else:
                logger.warning("RF hunt waypoint outside geofence — skipping")
                return False
        else:
            self._consecutive_clips = 0

        self._effective_wp = (wp_lat, wp_lon)
        self._mavlink.command_guided_to(wp_lat, wp_lon, wp_alt)
        return True

    def _do_scan(self) -> None:
        """Poll RSSI without navigation — scan-only mode."""
        rssi = self._poll_rssi()
        if rssi is not None:
            smoothed = self._filter.add(rssi)
            self._record_rssi(rssi)
            logger.info("[SCAN] Signal: %.1f dBm (avg %.1f)", rssi, smoothed)

    def _poll_rssi(self) -> float | None:
        """Poll Kismet for current RSSI, restarting Kismet once on failure."""
        rssi = self._kismet.get_rssi(
            mode=self._mode,
            bssid=self._target_bssid,
            freq_mhz=self._target_freq_mhz,
        )
        if rssi is not None:
            return rssi

        # No reading — check if Kismet is still up
        if self._kismet_manager is None:
            return None
        if not self._kismet.check_connection():
            # Restart Kismet in a background thread and wait up to 5 s for it
            # to complete. This avoids blocking the hunt loop for the full 15 s
            # restart, while still returning the retry value when the restart
            # completes promptly (common case on a healthy Jetson).
            if not self._kismet_restart_pending:
                self._kismet_restart_pending = True
                restart_done = threading.Event()
                restart_ok: list[bool] = [False]
                logger.warning("Kismet connection lost — restarting (5 s timeout)")

                def _bg_restart() -> None:
                    try:
                        restart_ok[0] = self._kismet_manager.restart(stop_event=self._stop_evt)
                        if restart_ok[0]:
                            self._kismet.reset_auth()
                            logger.info("Kismet restarted successfully")
                        else:
                            logger.error("Kismet restart failed")
                    finally:
                        self._kismet_restart_pending = False
                        restart_done.set()

                t = threading.Thread(target=_bg_restart, daemon=True, name="kismet-restart")
                t.start()
                restart_done.wait(timeout=5.0)
                if not restart_done.is_set():
                    logger.warning("Kismet restart exceeded 5 s timeout — continuing hunt")
                elif restart_ok[0]:
                    return self._kismet.get_rssi(
                        mode=self._mode,
                        bssid=self._target_bssid,
                        freq_mhz=self._target_freq_mhz,
                    )
        return None

    def _do_search(self) -> None:
        """Fly search pattern while polling for target signal."""
        # Poll for target
        rssi = self._poll_rssi()
        if rssi is not None:
            self._record_rssi(rssi)
            smoothed = self._filter.add(rssi)
            logger.info(
                "[SEARCH] Signal: %.1f dBm (avg %.1f)", rssi, smoothed,
            )
            if smoothed >= self._rssi_threshold:
                logger.info("SIGNAL ACQUIRED — switching to HOMING")
                lat, lon, alt = self._mavlink.get_lat_lon()
                if lat is not None:
                    self._navigator.record(smoothed, lat, lon, alt or 0)
                self._last_rssi = smoothed
                self._set_state(HuntState.HOMING)
                self._mavlink.send_statustext(
                    f"RF HUNT: Signal {smoothed:.0f}dBm", severity=2,
                )
                return

        # Continue search pattern
        if self._wp_index >= len(self._waypoints):
            logger.warning("Search pattern complete — target not found")
            self._mavlink.send_statustext("RF HUNT: No signal found", severity=3)
            # Command loiter so vehicle holds position rather than staying in GUIDED
            # with no active waypoint.
            try:
                self._mavlink.command_loiter()
            except Exception as exc:
                logger.warning("RF hunt loiter command failed: %s", exc)
            self._set_state(HuntState.ABORTED)
            return

        wp = self._waypoints[self._wp_index]
        lat, lon, _ = self._mavlink.get_lat_lon()
        if lat is None:
            return

        # Check arrival against effective (possibly clipped) coordinates
        check_lat, check_lon = wp[0], wp[1]
        if self._effective_wp is not None:
            check_lat, check_lon = self._effective_wp
        dist = haversine_m(lat, lon, check_lat, check_lon)
        if dist < self._arrival_tolerance:
            self._wp_index += 1
            self._effective_wp = None  # reset for next waypoint
            if self._wp_index < len(self._waypoints):
                nwp = self._waypoints[self._wp_index]
                if not self._geofence_waypoint(nwp[0], nwp[1], nwp[2]):
                    return
                logger.debug(
                    "Search WP %d/%d", self._wp_index, len(self._waypoints),
                )
        elif self._wp_index == 0 and self._effective_wp is None:
            # Send first waypoint
            if not self._geofence_waypoint(wp[0], wp[1], wp[2]):
                return

    def _do_homing(self) -> None:
        """Gradient ascent toward signal source."""
        rssi = self._poll_rssi()
        if rssi is None:
            # Might be a multipath null — count consecutive failures rather than
            # injecting -100 dBm which would corrupt the filter average.
            self._consecutive_none += 1
            if self._consecutive_none >= self._CONSECUTIVE_NONE_LOST:
                logger.warning(
                    "Signal lost during homing (%d consecutive None reads)",
                    self._consecutive_none,
                )
                self._set_state(HuntState.LOST)
            return

        self._consecutive_none = 0  # valid reading — reset the none counter
        smoothed = self._filter.add(rssi)
        lat, lon, alt = self._mavlink.get_lat_lon()
        if lat is None:
            return

        self._record_rssi(rssi, lat=lat, lon=lon)
        self._navigator.record(smoothed, lat, lon, alt or 0)

        # Check convergence
        if smoothed >= self._rssi_converge:
            logger.info("TARGET LOCALIZED — RSSI %.1f dBm", smoothed)
            self._mavlink.send_statustext(
                f"RF HUNT: TARGET FOUND {smoothed:.0f}dBm", severity=1,
            )
            audit_log.info(
                "RF TARGET LOCALIZED: rssi=%.1f lat=%.7f lon=%.7f",
                smoothed, lat, lon,
            )
            self._set_state(HuntState.CONVERGED)
            return

        # Fix 12.2: alt can be None from get_lat_lon() — use search altitude as fallback
        safe_alt = alt if alt is not None else self._search_alt_m

        # Gradient step
        nlat, nlon, cont = self._navigator.next_probe(
            lat, lon, smoothed, self._last_rssi,
        )
        self._last_rssi = smoothed

        if not cont:
            best_pos = self._navigator.get_best_position()
            if best_pos is not None:
                blat, blon = best_pos
                self._geofence_waypoint(blat, blon, safe_alt)
            self._mavlink.send_statustext(
                f"RF HUNT: Best {self._navigator.get_best_rssi():.0f}dBm",
                severity=2,
            )
            self._set_state(HuntState.CONVERGED)
            return

        if not self._geofence_waypoint(nlat, nlon, safe_alt):
            return

    def _do_lost(self) -> None:
        """Return to last known good position and re-search."""
        best_pos = self._navigator.get_best_position()

        # Guard: if no samples exist, best_pos is None — sending the vehicle
        # to (0.0, 0.0) would fly it to the Gulf of Guinea.
        if best_pos is None or self._navigator.get_sample_count() == 0:
            logger.error(
                "RF HUNT LOST: no valid samples to return to — aborting"
            )
            if self._mavlink:
                self._mavlink.send_statustext(
                    "RF HUNT: LOST with no samples — ABORTED", severity=4,
                )
                try:
                    self._mavlink.command_loiter()
                except Exception as exc:
                    logger.warning("RF hunt loiter command failed: %s", exc)
            self._set_state(HuntState.ABORTED)
            return

        blat, blon = best_pos
        lat, lon, alt = self._mavlink.get_lat_lon()
        if lat is None:
            return

        # Fix 12.2: alt can be None — fall back to configured search altitude
        safe_alt = alt if alt is not None else self._search_alt_m
        self._geofence_waypoint(blat, blon, safe_alt)
        self._mavlink.send_statustext("RF HUNT: Re-searching", severity=3)

        # Wait briefly then re-search with tighter pattern
        self._stop_evt.wait(3.0)
        if self._stop_evt.is_set():
            return

        self._filter.reset()
        self._navigator.reset()
        self._waypoints = generate_spiral(
            blat, blon,
            max_radius_m=self._search_area_m / 3,
            spacing_m=self._search_spacing_m / 2,
            alt=self._search_alt_m,
        )
        self._wp_index = 0
        self._effective_wp = None
        self._set_state(HuntState.SEARCHING)

    def _report_results(self) -> None:
        """Log final hunt results and dump sample CSV atomically."""
        samples = self._navigator.get_samples_copy()
        best_rssi = self._navigator.get_best_rssi()
        best_pos = self._navigator.get_best_position()
        best_lat = best_pos[0] if best_pos is not None else float("nan")
        best_lon = best_pos[1] if best_pos is not None else float("nan")
        audit_log.info(
            "RF HUNT RESULT: state=%s best_rssi=%.1f best_pos=(%.7f,%.7f) "
            "samples=%d",
            self.state.value, best_rssi,
            best_lat, best_lon, len(samples),
        )
        if not samples:
            return
        # /tmp is intentional — ephemeral hunt data, survives the hunt session
        # but not a reboot. If a persistent log dir is needed, use
        # os.path.join(config_log_dir, "hydra_rf_hunt_samples.csv") instead.
        csv_path = "/tmp/hydra_rf_hunt_samples.csv"
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir="/tmp", prefix="hydra_rf_", suffix=".csv",
            )
            with os.fdopen(fd, "w") as f:
                f.write("timestamp,lat,lon,alt,rssi_dbm\n")
                for s in samples:
                    f.write(
                        f"{s.timestamp:.3f},{s.lat:.7f},{s.lon:.7f},"
                        f"{s.alt:.1f},{s.rssi_dbm:.1f}\n"
                    )
            os.replace(tmp_path, csv_path)
            logger.info("RF hunt samples saved: %s (%d rows)", csv_path, len(samples))
        except OSError as exc:
            logger.warning("Failed to save RF samples: %s", exc)
