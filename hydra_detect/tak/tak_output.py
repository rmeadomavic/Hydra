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
        # Last _send_cot failure list — populated on every send attempt,
        # read by send_test_beacon() so a failed preflight returns the
        # concrete reason (e.g. "mcast 239.2.3.1:6969 (network unreachable)").
        self._last_send_failures: list[str] = []

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

    def is_running(self) -> bool:
        """Return True if the sender thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Return status dict for the web API."""
        return {
            "enabled": self.is_running(),
            "callsign": self._callsign,
            "multicast": f"{self._mcast_group}:{self._mcast_port}",
            "unicast_targets": len(self._unicast_targets),
            "events_sent": self._events_sent,
        }

    # ------------------------------------------------------------------
    # Runtime unicast target management
    # ------------------------------------------------------------------
    def add_unicast_target(self, host: str, port: int) -> None:
        """Add a unicast target at runtime."""
        target = (host, port)
        with self._data_lock:
            if target not in self._unicast_targets:
                self._unicast_targets.append(target)
        logger.info("TAK unicast target added: %s:%d", host, port)

    def remove_unicast_target(self, host: str, port: int) -> None:
        """Remove a unicast target at runtime."""
        target = (host, port)
        with self._data_lock:
            if target in self._unicast_targets:
                self._unicast_targets.remove(target)
        logger.info("TAK unicast target removed: %s:%d", host, port)

    def get_unicast_targets(self) -> list[dict]:
        """Return current unicast targets for API."""
        with self._data_lock:
            return [{"host": h, "port": p} for h, p in self._unicast_targets]

    def send_test_beacon(self) -> dict:
        """Force an immediate self-SA emit and report the result.

        Used by the TAK tab's "Test Broadcast" button so operators can
        verify end-to-end wiring before a field sortie without having to
        wait on the 5 s SA cadence. Returns a dict with:
            success (bool), reason (str), callsign, destinations (list[str]).
        """
        if not self.is_running():
            return {
                "success": False,
                "reason": "TAK output is not running",
                "callsign": self._callsign,
                "destinations": [],
            }
        lat, lon, _ = self._mav.get_lat_lon()
        if lat is None or lon is None:
            return {
                "success": False,
                "reason": "No GPS fix — self-SA needs a position",
                "callsign": self._callsign,
                "destinations": [],
            }
        try:
            sent = self._send_self_sa()
        except OSError as e:
            return {
                "success": False,
                "reason": f"Socket error: {e}",
                "callsign": self._callsign,
                "destinations": [],
            }
        destinations = [f"mcast://{self._mcast_group}:{self._mcast_port}"]
        with self._data_lock:
            for host, port in self._unicast_targets:
                destinations.append(f"udp://{host}:{port}")
        if not sent:
            # _send_cot catches OSError internally — surface whatever it
            # last recorded so the operator sees the real reason instead
            # of a false-positive "Self-SA emitted".
            failures = ", ".join(self._last_send_failures) or "all sends failed"
            return {
                "success": False,
                "reason": f"No destination accepted the packet: {failures}",
                "callsign": self._callsign,
                "destinations": destinations,
            }
        return {
            "success": True,
            "reason": "Self-SA emitted",
            "callsign": self._callsign,
            "destinations": destinations,
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

    def _send_self_sa(self) -> bool:
        """Build + emit a self-SA CoT. Returns True if at least one
        destination accepted it, False otherwise (including the no-GPS
        case so the caller can distinguish)."""
        lat, lon, alt = self._mav.get_lat_lon()
        if lat is None or lon is None:
            return False
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
        return self._send_cot(data)

    def _send_video_feed(self) -> None:
        lat, lon, alt = self._mav.get_lat_lon()
        if lat is None or lon is None:
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
                continue
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
                continue

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

    def emit_cot(self, data: bytes) -> None:
        """Send a pre-built CoT payload through this output (thread-safe).

        Public wrapper around ``_send_cot`` so external components —
        notably ``RfTakEmitter`` — can push CoT events without touching
        protected methods. A no-op when the output is not running.
        """
        self._send_cot(data)

    def _send_cot(self, data: bytes) -> bool:
        """Send CoT XML to all configured destinations.

        Returns True if at least one destination accepted the packet; False
        if every send errored (or there is no socket / no destinations).
        The sender loop does not inspect the return value — callers like
        send_test_beacon() use it to tell the operator whether the force
        emit actually went out.
        """
        if self._sock is None:
            return False
        any_ok = False
        failures: list[str] = []
        # Multicast
        if self._mcast_group:
            try:
                self._sock.sendto(data, (self._mcast_group, self._mcast_port))
                self._events_sent += 1
                any_ok = True
            except OSError as exc:
                logger.debug("TAK multicast send failed: %s", exc)
                failures.append(
                    f"mcast {self._mcast_group}:{self._mcast_port} ({exc})"
                )
        # Unicast targets — copy under lock to avoid concurrent mutation
        with self._data_lock:
            unicast_snapshot = list(self._unicast_targets)
        for host, port in unicast_snapshot:
            try:
                self._sock.sendto(data, (host, port))
                self._events_sent += 1
                any_ok = True
            except OSError as exc:
                logger.debug("TAK unicast send to %s:%d failed: %s", host, port, exc)
                failures.append(f"{host}:{port} ({exc})")
        # Remember last failure detail so send_test_beacon can surface it.
        if failures:
            self._last_send_failures = failures
        else:
            self._last_send_failures = []
        return any_ok
