"""TAK/ATAK CoT output sink — pytak-backed transport (outbound only).

Drop-in replacement for :class:`hydra_detect.tak.tak_output.TAKOutput`.
Same public method signatures, same behaviour, same CoT XML on the wire —
the only thing that changes is the network plumbing: instead of a hand-
rolled blocking ``socket.sendto`` per destination, we run ``pytak``'s
asyncio UDP writers in a daemon background thread.

Why route XML construction through the legacy ``cot_builder`` module?
    pytak's ``gen_cot_xml`` only knows the minimal CoT schema (event +
    point + a generic detail/contact). Hydra emits richer details:
    ``track`` (heading/speed), ``precisionlocation``, ``__video``
    /``ConnectionEntry`` (RTSP feed announcement), and per-detection
    ``remarks``. The cot_builder helpers already produce those exactly
    the way ATAK/WinTAK consume them — keeping them keeps byte-level
    parity with the legacy emitter, which is what the migration test
    suite asserts.

Backend selection lives in :mod:`hydra_detect.tak` via the
``HYDRA_COT_BACKEND`` environment variable (``pytak`` default,
``legacy`` fallback). See ``hydra_detect/tak/__init__.py``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from urllib.parse import urlparse

import pytak

from ..mavlink_io import MAVLinkIO
from ..tracker import TrackingResult
from .cot_builder import build_detection_marker, build_self_sa, build_video_feed
from .type_mapping import get_cot_type

logger = logging.getLogger(__name__)


def _parse_unicast_targets(raw: str) -> list[tuple[str, int]]:
    """Parse ``"host:port, host:port"`` into a list of (host, port) tuples.

    Identical semantics to the legacy emitter so a config drop-in works.
    """
    targets: list[tuple[str, int]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            host, port_str = entry.rsplit(":", 1)
            targets.append((host.strip(), int(port_str)))
        except (ValueError, TypeError):
            logger.warning("TAK(pytak): ignoring invalid unicast target: %r", entry)
    return targets


class _UdpWriter:
    """Thin holder for one pytak UDP writer + its destination string."""

    __slots__ = ("dest", "writer")

    def __init__(self, dest: str, writer) -> None:
        self.dest = dest
        self.writer = writer


class PyTAKOutput:
    """Pytak-backed CoT output, public API parity with :class:`TAKOutput`.

    The asyncio event loop runs in a dedicated daemon thread. Two threads
    interact with it:
      * the pipeline thread (``push``) — only mutates ``_data_lock`` state,
        never touches the loop directly;
      * the emitter thread (``_sender_loop``) — schedules send coroutines
        on the loop via ``run_coroutine_threadsafe``.
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

        # asyncio loop + thread + writers
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._writers: list[_UdpWriter] = []

        # Sender (emit-cadence) thread
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Data shared with pipeline thread
        self._data_lock = threading.Lock()
        self._latest_tracks: TrackingResult = TrackingResult()
        self._alert_classes: set[str] | None = None
        self._locked_track_id: int | None = None

        # Sender state
        self._last_emit: dict[int, float] = {}
        self._last_sa = 0.0
        self._last_video = 0.0
        self._events_sent = 0
        self._last_send_failures: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Spin up the asyncio loop + open every UDP writer + start emit thread."""
        # 1. asyncio loop in its own thread
        self._loop_ready.clear()
        self._loop_thread = threading.Thread(
            target=self._loop_runner, name="tak-pytak-loop", daemon=True,
        )
        self._loop_thread.start()
        if not self._loop_ready.wait(timeout=3.0):
            logger.error("TAK(pytak): asyncio loop failed to start within 3s")
            return False

        # 2. open one writer per destination
        if not self._open_writers():
            logger.error("TAK(pytak): no UDP writers opened — aborting start")
            self._shutdown_loop()
            return False

        # 3. emit cadence thread (legacy timer logic, unchanged)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sender_loop, name="tak-pytak-emit", daemon=True,
        )
        self._thread.start()
        logger.info(
            "TAK(pytak) started: mcast=%s:%d unicast=%s callsign=%s",
            self._mcast_group, self._mcast_port,
            self._unicast_targets or "(none)", self._callsign,
        )
        return True

    def stop(self) -> None:
        """Stop the emit thread, close every writer, tear down the loop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._close_writers()
        self._shutdown_loop()
        logger.info("TAK(pytak) stopped (%d events sent)", self._events_sent)

    # ------------------------------------------------------------------
    # Loop helpers
    # ------------------------------------------------------------------
    def _loop_runner(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            self._loop_ready.set()
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:  # pragma: no cover — defensive
                pass

    def _shutdown_loop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=3.0)
        self._loop = None
        self._loop_thread = None

    def _run_coro(self, coro):
        """Run a coroutine on the loop and wait briefly for completion.

        Returns the coroutine's result, or raises whatever it raised. Used
        for short open/close work — never call from inside the loop.
        """
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("TAK(pytak) loop not running")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=5.0)

    # ------------------------------------------------------------------
    # Writer management
    # ------------------------------------------------------------------
    def _open_writers(self) -> bool:
        """Open a pytak UDP writer for the multicast group + each unicast target."""
        urls: list[tuple[str, str]] = []
        if self._mcast_group:
            urls.append((
                f"udp+wo+multicast://{self._mcast_group}:{self._mcast_port}",
                f"mcast://{self._mcast_group}:{self._mcast_port}",
            ))
        for host, port in self._unicast_targets:
            urls.append((
                f"udp+wo://{host}:{port}",
                f"udp://{host}:{port}",
            ))

        opened: list[_UdpWriter] = []
        for cot_url, dest_label in urls:
            try:
                writer = self._run_coro(self._open_one(cot_url))
            except Exception as exc:
                logger.warning("TAK(pytak) failed to open %s: %s", dest_label, exc)
                continue
            opened.append(_UdpWriter(dest=dest_label, writer=writer))

        self._writers = opened
        return bool(opened)

    async def _open_one(self, cot_url: str):
        """Build a pytak UDP write-only writer from a COT URL."""
        url = urlparse(cot_url)
        # local_addr param matches pytak's protocol_factory default
        local_addr = (pytak.DEFAULT_PYTAK_MULTICAST_LOCAL_ADDR, 0)
        # multicast_ttl=32 matches the legacy emitter (drone -> ground GCS hops).
        _reader, writer = await pytak.create_udp_client(url, local_addr, 32)
        return writer

    def _close_writers(self) -> None:
        for w in self._writers:
            try:
                self._run_coro(self._close_one(w.writer))
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("TAK(pytak) writer close failed for %s: %s", w.dest, exc)
        self._writers = []

    async def _close_one(self, writer) -> None:
        try:
            close = getattr(writer, "close", None)
            if close is not None:
                close()
        except Exception:  # pragma: no cover — defensive
            pass

    # ------------------------------------------------------------------
    # Pipeline interface (parity with TAKOutput)
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

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        return {
            "enabled": self.is_running(),
            "callsign": self._callsign,
            "multicast": f"{self._mcast_group}:{self._mcast_port}",
            "unicast_targets": len(self._unicast_targets),
            "events_sent": self._events_sent,
            "backend": "pytak",
        }

    # ------------------------------------------------------------------
    # Runtime unicast target management
    # ------------------------------------------------------------------
    def add_unicast_target(self, host: str, port: int) -> None:
        target = (host, port)
        with self._data_lock:
            if target in self._unicast_targets:
                return
            self._unicast_targets.append(target)
        # Open the writer outside the lock — open_one schedules onto the loop.
        try:
            writer = self._run_coro(self._open_one(f"udp+wo://{host}:{port}"))
            self._writers.append(_UdpWriter(dest=f"udp://{host}:{port}", writer=writer))
            logger.info("TAK(pytak) unicast target added: %s:%d", host, port)
        except Exception as exc:
            logger.warning(
                "TAK(pytak) failed to open new unicast %s:%d: %s", host, port, exc,
            )

    def remove_unicast_target(self, host: str, port: int) -> None:
        target = (host, port)
        dest_label = f"udp://{host}:{port}"
        with self._data_lock:
            if target in self._unicast_targets:
                self._unicast_targets.remove(target)
        # Close + drop matching writer
        kept: list[_UdpWriter] = []
        for w in self._writers:
            if w.dest == dest_label:
                try:
                    self._run_coro(self._close_one(w.writer))
                except Exception:  # pragma: no cover — defensive
                    pass
            else:
                kept.append(w)
        self._writers = kept
        logger.info("TAK(pytak) unicast target removed: %s:%d", host, port)

    def get_unicast_targets(self) -> list[dict]:
        with self._data_lock:
            return [{"host": h, "port": p} for h, p in self._unicast_targets]

    def send_test_beacon(self) -> dict:
        """Force-emit a self-SA. Same return shape as TAKOutput."""
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
        destinations = [w.dest for w in self._writers]
        if not sent:
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
        while not self._stop_event.wait(timeout=0.5):
            now = time.monotonic()
            if (now - self._last_sa) >= self._sa_interval:
                self._send_self_sa()
                self._last_sa = now
            if self._rtsp_url and (now - self._last_video) >= 60.0:
                self._send_video_feed()
                self._last_video = now
            self._send_detections(now)
            if len(self._last_emit) > 200:
                cutoff = now - (self._stale_det * 2)
                self._last_emit = {
                    tid: ts for tid, ts in self._last_emit.items()
                    if ts > cutoff
                }

    def _send_self_sa(self) -> bool:
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

            frame_cx = (track.x1 + track.x2) / 2.0
            error_x = (frame_cx - 320.0) / 320.0

            lat, lon, alt = self._mav.get_lat_lon()
            if lat is None:
                continue
            if alt is None or alt <= 0:
                alt = 0.0

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

    # ------------------------------------------------------------------
    # Outbound transport
    # ------------------------------------------------------------------
    def emit_cot(self, data: bytes) -> None:
        """Public hook for external producers (e.g. RfTakEmitter)."""
        self._send_cot(data)

    def _send_cot(self, data: bytes) -> bool:
        """Schedule the same bytes onto every open pytak writer.

        Returns True if at least one writer accepted the bytes. Per-writer
        failures are recorded in ``_last_send_failures`` so the test-beacon
        endpoint surfaces the real reason instead of a generic shrug.
        """
        if self._loop is None or not self._loop.is_running():
            self._last_send_failures = ["asyncio loop not running"]
            return False
        if not self._writers:
            self._last_send_failures = ["no destinations open"]
            return False

        any_ok = False
        failures: list[str] = []
        for w in list(self._writers):
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._send_one(w.writer, data), self._loop,
                )
                fut.result(timeout=2.0)
                self._events_sent += 1
                any_ok = True
            except Exception as exc:
                logger.debug("TAK(pytak) send to %s failed: %s", w.dest, exc)
                failures.append(f"{w.dest} ({exc})")

        self._last_send_failures = failures if failures else []
        return any_ok

    async def _send_one(self, writer, data: bytes) -> None:
        """Write ``data`` to one pytak writer.

        ``asyncio_dgram`` writers expose ``send(bytes)`` (no address — the
        socket is already connected). Some pytak versions wrap the writer
        in an asyncio StreamWriter for TCP, which exposes ``write`` +
        ``drain``; we handle both shapes.
        """
        send = getattr(writer, "send", None)
        if send is not None:
            await send(data)
            return
        write = getattr(writer, "write", None)
        if write is not None:
            write(data)
            drain = getattr(writer, "drain", None)
            if drain is not None:
                await drain()
            return
        raise RuntimeError("pytak writer has neither send() nor write()")
