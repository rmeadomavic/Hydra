"""TAK/ATAK CoT command listener — receive lock/strike/unlock from ATAK GeoChat."""

from __future__ import annotations

import logging
import re
import socket
import struct
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable

logger = logging.getLogger(__name__)

_CMD_RE = re.compile(
    r"^\s*HYDRA\s+(LOCK|STRIKE|UNLOCK)(?:\s+(\d+))?\s*$",
    re.IGNORECASE,
)


class TAKInput:
    """Listen for incoming CoT events and dispatch lock/strike/unlock commands.

    Supports two parsing paths:
    1. **GeoChat** (``type="b-t-f"``) — parse ``detail/remarks`` for
       ``HYDRA LOCK <id>``, ``HYDRA STRIKE <id>``, ``HYDRA UNLOCK``.
    2. **Custom CoT types** — ``a-x-hydra-l``, ``a-x-hydra-s``, ``a-x-hydra-u``
       with track_id in ``detail/hydra/@trackId`` or ``detail/remarks``.
    """

    def __init__(
        self,
        listen_port: int = 4243,
        multicast_group: str = "239.2.3.1",
        on_lock: Callable[[int], bool] | None = None,
        on_strike: Callable[[int], bool] | None = None,
        on_unlock: Callable[[], None] | None = None,
    ):
        self._port = listen_port
        self._mcast_group = multicast_group
        self._on_lock = on_lock
        self._on_strike = on_strike
        self._on_unlock = on_unlock

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._events_received = 0
        self._commands_dispatched = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Bind UDP socket and start the listener daemon thread."""
        try:
            self._sock = socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
            )
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self._port))
            self._sock.settimeout(1.0)
            # Join multicast group so we receive GeoChat broadcasts
            if self._mcast_group:
                mreq = struct.pack(
                    "4sL",
                    socket.inet_aton(self._mcast_group),
                    socket.INADDR_ANY,
                )
                self._sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq,
                )
        except OSError as exc:
            logger.error("TAK input: failed to bind UDP port %d: %s", self._port, exc)
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listener_loop, name="tak-cmd-in", daemon=True,
        )
        self._thread.start()
        logger.info("TAK command listener started on port %d", self._port)
        return True

    def stop(self) -> None:
        """Signal the listener thread to stop, join, and close the socket."""
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
        logger.info(
            "TAK command listener stopped (%d events, %d commands)",
            self._events_received, self._commands_dispatched,
        )

    def get_status(self) -> dict:
        """Return status dict for the web API."""
        return {
            "listening": self._thread is not None and self._thread.is_alive(),
            "port": self._port,
            "events_received": self._events_received,
            "commands_dispatched": self._commands_dispatched,
        }

    # ------------------------------------------------------------------
    # Listener thread
    # ------------------------------------------------------------------
    def _listener_loop(self) -> None:
        """Background loop: receive UDP datagrams and parse CoT commands."""
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                logger.warning("TAK input: socket error, retrying")
                time.sleep(0.5)
                continue
            self._events_received += 1
            try:
                self._handle_datagram(data, addr)
            except Exception as exc:
                logger.debug("TAK input: failed to process datagram: %s", exc)

    def _handle_datagram(self, data: bytes, addr: tuple | None = None) -> None:
        """Parse a CoT XML datagram and dispatch any commands."""
        try:
            root = ET.fromstring(data.decode("utf-8", errors="replace"))
        except ET.ParseError:
            return
        if root.tag != "event":
            return

        cot_type = root.get("type", "")

        # Path 1: GeoChat messages
        if cot_type == "b-t-f":
            self._parse_geochat(root, addr)
            return

        # Path 2: Custom Hydra CoT types
        if cot_type.startswith("a-x-hydra-"):
            self._parse_custom_type(root, cot_type, addr)

    # ------------------------------------------------------------------
    # GeoChat parsing
    # ------------------------------------------------------------------
    def _parse_geochat(self, root: ET.Element, addr: tuple | None) -> None:
        """Parse a GeoChat CoT event for HYDRA commands."""
        remarks_el = root.find("detail/remarks")
        if remarks_el is None or not remarks_el.text:
            return

        text = remarks_el.text.strip()
        match = _CMD_RE.match(text)
        if not match:
            return

        action = match.group(1).upper()
        track_id_str = match.group(2)

        # Extract sender callsign for logging
        chat_el = root.find("detail/__chat")
        sender = chat_el.get("senderCallsign", "?") if chat_el is not None else "?"
        source = f"GeoChat/{sender}"

        if action == "LOCK":
            if track_id_str is None:
                logger.warning("TAK input: LOCK command missing track_id from %s", source)
                return
            self._dispatch_lock(int(track_id_str), source)
        elif action == "STRIKE":
            if track_id_str is None:
                logger.warning("TAK input: STRIKE command missing track_id from %s", source)
                return
            self._dispatch_strike(int(track_id_str), source)
        elif action == "UNLOCK":
            self._dispatch_unlock(source)

    # ------------------------------------------------------------------
    # Custom CoT type parsing
    # ------------------------------------------------------------------
    def _parse_custom_type(
        self, root: ET.Element, cot_type: str, addr: tuple | None,
    ) -> None:
        """Parse a custom ``a-x-hydra-*`` CoT event."""
        source = f"CoT/{root.get('uid', '?')}"

        # Extract track_id from detail/hydra/@trackId or detail/remarks
        track_id = None
        hydra_el = root.find("detail/hydra")
        if hydra_el is not None and hydra_el.get("trackId"):
            try:
                track_id = int(hydra_el.get("trackId"))
            except (ValueError, TypeError):
                pass
        if track_id is None:
            remarks_el = root.find("detail/remarks")
            if remarks_el is not None and remarks_el.text:
                try:
                    track_id = int(remarks_el.text.strip())
                except (ValueError, TypeError):
                    pass

        suffix = cot_type[len("a-x-hydra-"):]
        if suffix.startswith("l"):
            if track_id is None:
                logger.warning("TAK input: custom LOCK missing trackId from %s", source)
                return
            self._dispatch_lock(track_id, source)
        elif suffix.startswith("s"):
            if track_id is None:
                logger.warning("TAK input: custom STRIKE missing trackId from %s", source)
                return
            self._dispatch_strike(track_id, source)
        elif suffix.startswith("u"):
            self._dispatch_unlock(source)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _dispatch_lock(self, track_id: int, source: str) -> None:
        logger.info("TAK CMD: LOCK #%d from %s", track_id, source)
        if self._on_lock is not None:
            try:
                result = self._on_lock(track_id)
                self._commands_dispatched += 1
                logger.info("TAK CMD: LOCK #%d → %s", track_id, "OK" if result else "FAILED")
            except Exception as exc:
                logger.warning("TAK CMD: LOCK #%d exception: %s", track_id, exc)

    def _dispatch_strike(self, track_id: int, source: str) -> None:
        logger.info("TAK CMD: STRIKE #%d from %s", track_id, source)
        if self._on_strike is not None:
            try:
                result = self._on_strike(track_id)
                self._commands_dispatched += 1
                logger.info("TAK CMD: STRIKE #%d → %s", track_id, "OK" if result else "FAILED")
            except Exception as exc:
                logger.warning("TAK CMD: STRIKE #%d exception: %s", track_id, exc)

    def _dispatch_unlock(self, source: str) -> None:
        logger.info("TAK CMD: UNLOCK from %s", source)
        if self._on_unlock is not None:
            try:
                self._on_unlock()
                self._commands_dispatched += 1
                logger.info("TAK CMD: UNLOCK → OK")
            except Exception as exc:
                logger.warning("TAK CMD: UNLOCK exception: %s", exc)
