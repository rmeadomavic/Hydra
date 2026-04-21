"""MAVLink-based CoT relay — detection sink for offline-Jetson ops.

When the Jetson is out of WiFi/LAN range from the TAK server (e.g. mounted
on a boat running over telemetry radio), the existing :class:`TAKOutput`
can't reach ATAK. This sink packs each throttled detection into an
``ADSB_VEHICLE`` MAVLink message instead. ArduPilot auto-forwards those
across every MAVLink serial port (see ADS-B receiver docs), so the frames
reach the ground station laptop over the same telemetry radio carrying
normal vehicle telemetry.

On the GCS side, ``tools/hydra_relay.py`` listens for these frames and
republishes them as CoT markers to the local TAK server.

Design notes:
    * Mirrors :class:`TAKOutput` closely (``push`` API, background thread,
      throttle, ``get_status``) so the pipeline fan-out is symmetric.
    * Keeps the UID scheme byte-identical to the direct path — with
      ``mode = both`` the Jetson can publish via UDP *and* via MAVLink
      simultaneously, and ATAK dedupes by UID (no duplicate markers).
    * Throttle defaults to the existing ``[tak] emit_interval`` so the RF
      budget is predictable: at 10 tracks × 0.5 Hz = ~190 B/s, trivial for
      a 57600 bps SiK link.
"""

from __future__ import annotations

import logging
import math
import threading
import time

from ..mavlink_io import MAVLinkIO
from ..tracker import TrackingResult
from . import adsb_codec

logger = logging.getLogger(__name__)


class MAVLinkRelayOutput:
    """Emit throttled Hydra detections as ADSB_VEHICLE frames."""

    def __init__(
        self,
        mavlink_io: MAVLinkIO,
        emit_interval: float = 2.0,
        camera_hfov_deg: float = 60.0,
    ) -> None:
        self._mav = mavlink_io
        self._emit_interval = max(0.1, emit_interval)
        self._hfov = camera_hfov_deg

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._data_lock = threading.Lock()
        self._latest_tracks: TrackingResult = TrackingResult()
        self._alert_classes: set[str] | None = None
        self._locked_track_id: int | None = None

        # Sender-thread-only state.
        self._last_emit: dict[int, float] = {}
        self._first_seen: dict[int, float] = {}
        self._events_sent = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        if self._mav is None:
            logger.warning("MAVLink relay requires MAVLinkIO — skipping")
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sender_loop, name="tak-relay", daemon=True,
        )
        self._thread.start()
        logger.info(
            "MAVLink CoT relay started: emit_interval=%.1fs", self._emit_interval,
        )
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info(
            "MAVLink CoT relay stopped (%d ADSB_VEHICLE frames sent)",
            self._events_sent,
        )

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        return {
            "enabled": self.is_running(),
            "events_sent": self._events_sent,
            "emit_interval": self._emit_interval,
        }

    # ------------------------------------------------------------------
    # Pipeline interface — non-blocking, called from the hot loop
    # ------------------------------------------------------------------
    def push(
        self,
        tracks: TrackingResult,
        alert_classes: set[str] | None,
        locked_track_id: int | None,
    ) -> None:
        with self._data_lock:
            self._latest_tracks = tracks
            self._alert_classes = alert_classes
            self._locked_track_id = locked_track_id

    # ------------------------------------------------------------------
    # Sender thread
    # ------------------------------------------------------------------
    def _sender_loop(self) -> None:
        while not self._stop_event.wait(timeout=0.5):
            now = time.monotonic()
            self._send_detections(now)
            # Prune stale throttle entries every so often.
            if len(self._last_emit) > 200:
                cutoff = now - (self._emit_interval * 30.0)
                self._last_emit = {
                    tid: ts for tid, ts in self._last_emit.items() if ts > cutoff
                }
                self._first_seen = {
                    tid: ts for tid, ts in self._first_seen.items() if ts > cutoff
                }

    def _send_detections(self, now: float) -> None:
        with self._data_lock:
            tracks = self._latest_tracks
            alert_classes = self._alert_classes
            locked_id = self._locked_track_id

        for track in tracks:
            if alert_classes is not None and track.label not in alert_classes:
                continue
            last = self._last_emit.get(track.track_id, 0.0)
            if (now - last) < self._emit_interval:
                continue

            lat, lon, alt = self._mav.get_lat_lon()
            if lat is None:
                # No GPS fix — skip; we don't want to publish bogus 0,0 to ATAK.
                continue
            if alt is None or alt <= 0:
                alt = 0.0

            # Target position projection: identical math to TAKOutput so the
            # direct and relay paths produce the same lat/lon for the same
            # track. Must change both if the projection model changes.
            frame_cx = (track.x1 + track.x2) / 2.0
            error_x = (frame_cx - 320.0) / 320.0
            vfov_rad = math.radians(self._hfov * 0.75) / 2.0
            if vfov_rad > 0 and alt > 0:
                est_distance = alt / math.tan(vfov_rad)
            else:
                est_distance = 20.0
            pos = self._mav.estimate_target_position(
                error_x,
                approach_distance_m=est_distance,
                camera_hfov_deg=self._hfov,
            )
            if pos is None:
                continue
            target_lat, target_lon = pos

            first_seen = self._first_seen.setdefault(track.track_id, now)
            age_sec = int(now - first_seen)
            is_sim = getattr(self._mav, "_is_sim_gps", False)

            kwargs = adsb_codec.build_adsb_kwargs(
                track_id=track.track_id,
                lat=target_lat,
                lon=target_lon,
                hae_m=alt,
                label=track.label,
                confidence=track.confidence,
                age_sec=age_sec,
                detected=True,
                locked=(locked_id == track.track_id),
                sim_gps=bool(is_sim),
            )

            if self._build_and_send(kwargs):
                self._last_emit[track.track_id] = now
                self._events_sent += 1

    def _build_and_send(self, kwargs: dict) -> bool:
        """Construct the ADSB_VEHICLE message and hand it to MAVLinkIO."""
        try:
            from pymavlink.dialects.v20 import common as mavlink2
        except Exception as exc:
            logger.warning("pymavlink unavailable, relay disabled: %s", exc)
            return False
        try:
            msg = mavlink2.MAVLink_adsb_vehicle_message(**kwargs)
        except Exception as exc:
            logger.warning("Failed to build ADSB_VEHICLE: %s", exc)
            return False
        return self._mav.send_raw_message(msg)
