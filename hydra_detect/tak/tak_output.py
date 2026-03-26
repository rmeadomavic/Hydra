"""TAK/ATAK CoT output sink — sends detection and SA data via UDP multicast/unicast."""

from __future__ import annotations

import logging
import math
import socket
import struct
import threading
import time

from ..mavlink_io import MAVLinkIO
from ..tracker import TrackingResult
from .cot_builder import build_detection_marker, build_self_sa, build_video_feed
from .type_mapping import get_cot_type

logger = logging.getLogger(__name__)


def _parse_unicast_targets(raw: str) -> list[tuple[str, int]]:
    """Parse ``"host:port, host:port"`` into a list of (host, port) tuples."""
    targets: list[tuple[str, int]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            host, port_str = entry.rsplit(":", 1)
            targets.append((host.strip(), int(port_str)))
        except (ValueError, TypeError):
            logger.warning("TAK: ignoring invalid unicast target: %r", entry)
    return targets


class TAKOutput:
    """Send detection and SA data as CoT XML events via UDP multicast/unicast.

    Follows the same daemon-thread pattern as :class:`RTSPServer` and
    :class:`GeoTracker` — the pipeline calls :meth:`push` (non-blocking) and a
    background thread handles throttled CoT emission and network I/O.
    """

    def __init__(
        self,
        mavlink_io: MAVLinkIO,
        callsign: str = "HYDRA-1",
        multicast_group: str = "239.2.3.1",
        multicast_port: int = 6969,
        emit_interval: float = 2.0,
        sa_interval: float = 5.0,
        stale_detection: float = 60.0,
        stale_sa: float = 30.0,
        camera_hfov_deg: float = 60.0,
        unicast_targets: str = "",
        rtsp_url: str | None = None,
    ):
        self._mav = mavlink_io
        self._callsign = callsign
        self._mcast_group = multicast_group
        self._mcast_port = multicast_port
        self._emit_interval = emit_interval
        self._sa_interval = sa_interval
        self._stale_det = stale_detection
        self._stale_sa = stale_sa
        self._hfov = camera_hfov_deg
        self._unicast_targets = _parse_unicast_targets(unicast_targets)
        self._rtsp_url = rtsp_url

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Data shared with the pipeline thread
        self._data_lock = threading.Lock()
        self._latest_tracks: TrackingResult = TrackingResult()
        self._alert_classes: set[str] | None = None
        self._locked_track_id: int | None = None

        # Sender state (only accessed by the sender thread)
        self._last_emit: dict[int, float] = {}
        self._last_sa = 0.0
        self._last_video = 0.0
        self._events_sent = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Create the UDP socket and start the sender daemon thread."""
        try:
            self._sock = socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
            )
            self._sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                struct.pack("b", 32),
            )
        except OSError as exc:
            logger.error("TAK: failed to create UDP socket: %s", exc)
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sender_loop, name="tak-cot", daemon=True,
        )
        self._thread.start()
        logger.info(
            "TAK output started: mcast=%s:%d unicast=%s callsign=%s",
            self._mcast_group, self._mcast_port,
            self._unicast_targets or "(none)",
            self._callsign,
        )
        return True

    def stop(self) -> None:
        """Signal the sender thread to stop, join, and close the socket."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        logger.info("TAK output stopped (%d events sent)", self._events_sent)

    # ------------------------------------------------------------------
    # Pipeline interface (called from hot loop — must be non-blocking)
    # ------------------------------------------------------------------
    def push(
        self,
        tracks: TrackingResult,
        alert_classes: set[str] | None,
        locked_track_id: int | None,
    ) -> None:
        """Store latest tracking data for the sender thread."""
        with self._data_lock:
            self._latest_tracks = tracks
            self._alert_classes = alert_classes
            self._locked_track_id = locked_track_id

    def get_status(self) -> dict:
        """Return status dict for the web API."""
        return {
            "enabled": self._thread is not None and self._thread.is_alive(),
            "callsign": self._callsign,
            "multicast": f"{self._mcast_group}:{self._mcast_port}",
            "unicast_targets": len(self._unicast_targets),
            "events_sent": self._events_sent,
        }

    # ------------------------------------------------------------------
    # Sender thread
    # ------------------------------------------------------------------
    def _sender_loop(self) -> None:
        """Background loop: emit CoT events at throttled rates."""
        while not self._stop_event.wait(timeout=0.5):
            now = time.monotonic()

            # Self-SA beacon
            if (now - self._last_sa) >= self._sa_interval:
                self._send_self_sa()
                self._last_sa = now

            # Video feed announcement (every 60 s)
            if self._rtsp_url and (now - self._last_video) >= 60.0:
                self._send_video_feed()
                self._last_video = now

            # Detection markers
            self._send_detections(now)

            # Prune stale throttle entries every 30 ticks (~15 s)
            if len(self._last_emit) > 200:
                cutoff = now - (self._stale_det * 2)
                self._last_emit = {
                    tid: ts for tid, ts in self._last_emit.items()
                    if ts > cutoff
                }

    def _send_self_sa(self) -> None:
        lat, lon, alt = self._mav.get_lat_lon()
        if lat is None:
            return
        heading = self._mav.get_heading_deg()
        telem = self._mav.get_telemetry()
        speed = telem.get("groundspeed")

        data = build_self_sa(
            uid=f"{self._callsign}-SA",
            callsign=self._callsign,
            lat=lat, lon=lon, hae=alt or 0.0,
            heading=heading,
            speed=speed,
            stale_seconds=self._stale_sa,
        )
        self._send_cot(data)

    def _send_video_feed(self) -> None:
        lat, lon, alt = self._mav.get_lat_lon()
        if lat is None:
            return
        data = build_video_feed(
            uid=f"{self._callsign}-VIDEO",
            callsign=self._callsign,
            rtsp_url=self._rtsp_url,  # type: ignore[arg-type]
            lat=lat, lon=lon, hae=alt or 0.0,
        )
        self._send_cot(data)

    def _send_detections(self, now: float) -> None:
        with self._data_lock:
            tracks = self._latest_tracks
            alert_classes = self._alert_classes
            locked_id = self._locked_track_id

        for track in tracks:
            if alert_classes is not None and track.label not in alert_classes:
                continue
            if (now - self._last_emit.get(track.track_id, 0.0)) < self._emit_interval:
                continue

            # Estimate target GPS from camera geometry
            frame_cx = (track.x1 + track.x2) / 2.0
            error_x = (frame_cx - 320.0) / 320.0  # normalise to -1..+1

            lat, lon, alt = self._mav.get_lat_lon()
            if lat is None:
                return
            if alt is None or alt <= 0:
                alt = 0.0

            # Rough ground distance from altitude + camera FOV
            vfov_rad = math.radians(self._hfov * 0.75) / 2.0
            if vfov_rad > 0 and alt > 0:
                est_distance = alt / math.tan(vfov_rad)
            else:
                est_distance = 20.0

            pos = self._mav.estimate_target_position(
                error_x, approach_distance_m=est_distance,
                camera_hfov_deg=self._hfov,
            )
            if pos is None:
                return

            target_lat, target_lon = pos
            cot_type = get_cot_type(track.label)

            data = build_detection_marker(
                uid=f"{self._callsign}-DET-{track.track_id}",
                callsign=f"{self._callsign}-{track.label}-{track.track_id}",
                cot_type=cot_type,
                lat=target_lat, lon=target_lon, hae=alt,
                confidence=track.confidence,
                label=track.label,
                track_id=track.track_id,
                stale_seconds=self._stale_det,
            )
            self._send_cot(data)
            self._last_emit[track.track_id] = now

    def _send_cot(self, data: bytes) -> None:
        """Send CoT XML to all configured destinations."""
        if self._sock is None:
            return
        # Multicast
        if self._mcast_group:
            try:
                self._sock.sendto(data, (self._mcast_group, self._mcast_port))
                self._events_sent += 1
            except OSError as exc:
                logger.debug("TAK multicast send failed: %s", exc)
        # Unicast targets
        for host, port in self._unicast_targets:
            try:
                self._sock.sendto(data, (host, port))
                self._events_sent += 1
            except OSError as exc:
                logger.debug("TAK unicast send to %s:%d failed: %s", host, port, exc)
