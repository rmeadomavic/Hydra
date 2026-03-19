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
from enum import Enum
from typing import Callable

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
        kismet_user: str = "kismet",
        kismet_pass: str = "kismet",
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
    ):
        self._mavlink = mavlink
        self._mode = mode
        self._target_bssid = target_bssid
        self._target_freq_mhz = target_freq_mhz
        self._search_pattern = search_pattern
        self._search_area_m = max(_SEARCH_AREA_MIN, min(search_area_m, _SEARCH_AREA_MAX))
        self._search_spacing_m = max(_SEARCH_SPACING_MIN, min(search_spacing_m, _SEARCH_SPACING_MAX))
        self._search_alt_m = max(_SEARCH_ALT_MIN, min(search_alt_m, _SEARCH_ALT_MAX))
        self._rssi_threshold = rssi_threshold_dbm
        self._rssi_converge = rssi_converge_dbm
        self._poll_interval = poll_interval_sec
        self._arrival_tolerance = arrival_tolerance_m
        self._on_state_change = on_state_change
        self._kismet_manager = kismet_manager

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
        self._last_rssi: float = -100.0
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

        # Lock for state reads from other threads (web UI, etc.)
        self._lock = threading.Lock()

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
    def best_position(self) -> tuple[float, float]:
        """(lat, lon) of the best RSSI reading (thread-safe)."""
        return self._navigator.get_best_position()

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
                "best_lat": round(best_pos[0], 7),
                "best_lon": round(best_pos[1], 7),
                "samples": sample_count,
                "wp_progress": f"{self._wp_index}/{len(self._waypoints)}",
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
        self._set_state(HuntState.SEARCHING)

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
        with self._lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            logger.info("RF Hunt: %s → %s", old.value, new_state.value)
            if self._on_state_change:
                try:
                    self._on_state_change(new_state)
                except (TypeError, ValueError) as exc:
                    logger.warning("State change callback error: %s", exc)

    def _run_loop(self) -> None:
        """Main hunt loop — runs in background thread."""
        try:
            while not self._stop_evt.is_set():
                state = self.state
                if state == HuntState.SEARCHING:
                    self._do_search()
                elif state == HuntState.HOMING:
                    self._do_homing()
                elif state == HuntState.LOST:
                    self._do_lost()
                elif state in (HuntState.CONVERGED, HuntState.ABORTED):
                    break
                self._stop_evt.wait(self._poll_interval)
        except (OSError, RuntimeError) as exc:
            logger.error("RF hunt loop error: %s", exc)
            self._set_state(HuntState.ABORTED)
        finally:
            self._kismet.close()
            self._report_results()

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
            logger.warning("Kismet connection lost — attempting restart")
            if self._kismet_manager.restart(stop_event=self._stop_evt):
                self._kismet.reset_auth()
                return self._kismet.get_rssi(
                    mode=self._mode,
                    bssid=self._target_bssid,
                    freq_mhz=self._target_freq_mhz,
                )
            logger.error("Kismet restart failed")
        return None

    def _do_search(self) -> None:
        """Fly search pattern while polling for target signal."""
        # Poll for target
        rssi = self._poll_rssi()
        if rssi is not None:
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
            self._set_state(HuntState.ABORTED)
            return

        wp = self._waypoints[self._wp_index]
        lat, lon, _ = self._mavlink.get_lat_lon()
        if lat is None:
            return

        # Check if we've arrived at current waypoint
        from ..autonomous import haversine_m
        dist = haversine_m(lat, lon, wp[0], wp[1])
        if dist < self._arrival_tolerance:
            self._wp_index += 1
            if self._wp_index < len(self._waypoints):
                nwp = self._waypoints[self._wp_index]
                self._mavlink.command_guided_to(nwp[0], nwp[1], nwp[2])
                logger.debug(
                    "Search WP %d/%d", self._wp_index, len(self._waypoints),
                )
        elif self._wp_index == 0:
            # Send first waypoint
            self._mavlink.command_guided_to(wp[0], wp[1], wp[2])

    def _do_homing(self) -> None:
        """Gradient ascent toward signal source."""
        rssi = self._poll_rssi()
        if rssi is None:
            # Might be a multipath null — push a weak reading
            self._filter.add(-100.0)
            if self._filter.average < self._rssi_threshold - 10:
                logger.warning("Signal lost during homing")
                self._set_state(HuntState.LOST)
            return

        smoothed = self._filter.add(rssi)
        lat, lon, alt = self._mavlink.get_lat_lon()
        if lat is None:
            return

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

        # Gradient step
        nlat, nlon, cont = self._navigator.next_probe(
            lat, lon, smoothed, self._last_rssi,
        )
        self._last_rssi = smoothed

        if not cont:
            blat, blon = self._navigator.get_best_position()
            self._mavlink.command_guided_to(blat, blon, alt)
            self._mavlink.send_statustext(
                f"RF HUNT: Best {self._navigator.get_best_rssi():.0f}dBm",
                severity=2,
            )
            self._set_state(HuntState.CONVERGED)
            return

        self._mavlink.command_guided_to(nlat, nlon, alt)

    def _do_lost(self) -> None:
        """Return to last known good position and re-search."""
        blat, blon = self._navigator.get_best_position()
        lat, lon, alt = self._mavlink.get_lat_lon()
        if lat is None:
            return

        self._mavlink.command_guided_to(blat, blon, alt)
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
        self._set_state(HuntState.SEARCHING)

    def _report_results(self) -> None:
        """Log final hunt results and dump sample CSV atomically."""
        samples = self._navigator.get_samples_copy()
        best_rssi = self._navigator.get_best_rssi()
        best_pos = self._navigator.get_best_position()
        audit_log.info(
            "RF HUNT RESULT: state=%s best_rssi=%.1f best_pos=(%.7f,%.7f) "
            "samples=%d",
            self.state.value, best_rssi,
            best_pos[0], best_pos[1], len(samples),
        )
        if not samples:
            return
        # Atomic write: write to temp file then rename
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
