"""TAK/ATAK CoT command listener — receive lock/strike/unlock from ATAK GeoChat."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import socket
import struct
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("hydra.audit")

# Matches command text like "HYDRA LOCK 5", "HYDRA-2-USV LOCK 5", "HYDRA-ALL LOCK 5"
_CMD_RE = re.compile(
    r"^\s*([\w-]+)\s+(LOCK|STRIKE|UNLOCK)(?:\s+(\d+))?\s*$",
    re.IGNORECASE,
)


def _callsign_matches(command_prefix: str, my_callsign: str) -> bool:
    """Check if a command prefix is addressed to this vehicle's callsign.

    Routing rules:
    - Exact match: "HYDRA-2-USV" matches callsign "HYDRA-2-USV"
    - Legacy prefix: "HYDRA" (bare) matches any callsign starting with "HYDRA"
    - Full wildcard: "HYDRA-ALL" matches all vehicles
    - Segment wildcard: "HYDRA-ALL-USV" matches if callsign contains "USV"
    - Segment wildcard: "HYDRA-2-ALL" matches if callsign contains "-2-"
    """
    prefix_upper = command_prefix.upper()
    cs_upper = my_callsign.upper()

    # Exact match
    if prefix_upper == cs_upper:
        return True

    # Bare "HYDRA" — backwards compat for any HYDRA-* callsign
    if prefix_upper == "HYDRA" and cs_upper.startswith("HYDRA"):
        return True

    # Full wildcard: HYDRA-ALL
    if prefix_upper == "HYDRA-ALL":
        return True

    # Segment wildcard: replace ALL segments and check containment
    # Split both into segments on "-"
    prefix_segments = prefix_upper.split("-")
    if "ALL" in prefix_segments:
        # Build a pattern: non-ALL segments must appear in callsign
        for seg in prefix_segments:
            if seg == "ALL":
                continue
            # Check if the segment appears in the callsign
            # For "HYDRA-ALL-USV", non-ALL segments are "HYDRA" and "USV"
            # For "HYDRA-2-ALL", non-ALL segments are "HYDRA" and "2"
            if seg not in cs_upper.split("-"):
                return False
        return True

    return False


class TAKInput:
    """Listen for incoming CoT events and dispatch lock/strike/unlock commands.

    Supports two parsing paths:
    1. **GeoChat** (``type="b-t-f"``) — parse ``detail/remarks`` for
       ``HYDRA LOCK <id>``, ``HYDRA STRIKE <id>``, ``HYDRA UNLOCK``.
    2. **Custom CoT types** — ``a-x-hydra-l``, ``a-x-hydra-s``, ``a-x-hydra-u``
       with track_id in ``detail/hydra/@trackId`` or ``detail/remarks``.

    Security features:
    - Callsign allowlist (fail-closed: empty = all commands disabled)
    - Optional HMAC-SHA256 verification on command text
    - Callsign-based command routing (only process commands for this vehicle)
    - Duplicate callsign detection on SA events
    """

    def __init__(
        self,
        listen_port: int = 6969,
        multicast_group: str = "239.2.3.1",
        on_lock: Callable[[int], bool] | None = None,
        on_strike: Callable[[int], bool] | None = None,
        on_unlock: Callable[[], None] | None = None,
        allowed_callsigns: list[str] | None = None,
        hmac_secret: str | None = None,
        my_callsign: str = "HYDRA-1",
    ):
        self._port = listen_port
        self._mcast_group = multicast_group
        self._on_lock = on_lock
        self._on_strike = on_strike
        self._on_unlock = on_unlock
        self._allowed_callsigns = (
            {cs.strip().upper() for cs in allowed_callsigns if cs.strip()}
            if allowed_callsigns
            else set()
        )
        self._hmac_secret = hmac_secret.strip() if hmac_secret else None
        self._my_callsign = my_callsign

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._events_received = 0
        self._commands_dispatched = 0
        self._commands_rejected = 0
        self._duplicate_callsign = False

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

        if not self._allowed_callsigns:
            logger.warning(
                "TAK commands disabled — no allowed callsigns configured"
            )

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
            "TAK command listener stopped (%d events, %d commands, %d rejected)",
            self._events_received, self._commands_dispatched,
            self._commands_rejected,
        )

    def get_status(self) -> dict:
        """Return status dict for the web API."""
        return {
            "listening": self._thread is not None and self._thread.is_alive(),
            "port": self._port,
            "events_received": self._events_received,
            "commands_dispatched": self._commands_dispatched,
            "commands_rejected": self._commands_rejected,
            "duplicate_callsign": self._duplicate_callsign,
        }

    # ------------------------------------------------------------------
    # Security checks
    # ------------------------------------------------------------------
    def _check_sender_allowed(self, sender_callsign: str) -> bool:
        """Check if a sender callsign is in the allowlist (fail-closed)."""
        if not self._allowed_callsigns:
            logger.warning(
                "TAK commands disabled — no allowed callsigns configured"
            )
            audit_logger.warning(
                "TAK_CMD_REJECTED reason=no_allowlist sender=%s",
                sender_callsign,
            )
            self._commands_rejected += 1
            return False

        if sender_callsign.upper() not in self._allowed_callsigns:
            logger.warning(
                "Rejected TAK command from unauthorized callsign: %s",
                sender_callsign,
            )
            audit_logger.warning(
                "TAK_CMD_REJECTED reason=unauthorized sender=%s",
                sender_callsign,
            )
            self._commands_rejected += 1
            return False

        return True

    def _verify_hmac(self, text: str) -> str | None:
        """Verify HMAC on command text. Returns the command text (sans HMAC suffix).

        If no HMAC secret is configured, returns text unchanged.
        If HMAC is required but missing/invalid, returns None.
        """
        if not self._hmac_secret:
            return text

        # Expect format: "HYDRA LOCK 5|HMAC:xxxx"
        if "|HMAC:" not in text:
            logger.warning("TAK input: HMAC required but missing from command")
            audit_logger.warning(
                "TAK_CMD_REJECTED reason=hmac_missing text=%s", text[:80],
            )
            self._commands_rejected += 1
            return None

        parts = text.rsplit("|HMAC:", 1)
        if len(parts) != 2:
            self._commands_rejected += 1
            return None

        msg_text = parts[0]
        received_hmac = parts[1].strip()

        expected = hmac.new(
            self._hmac_secret.encode("utf-8"),
            msg_text.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, received_hmac):
            logger.warning("TAK input: HMAC verification failed")
            audit_logger.warning(
                "TAK_CMD_REJECTED reason=hmac_invalid text=%s", msg_text[:80],
            )
            self._commands_rejected += 1
            return None

        return msg_text

    def _check_routing(self, command_prefix: str) -> bool:
        """Check if command is addressed to this vehicle."""
        return _callsign_matches(command_prefix, self._my_callsign)

    def _check_duplicate_callsign(self, root: ET.Element) -> None:
        """Check SA events for duplicate callsign on the network."""
        contact = root.find("detail/contact")
        if contact is None:
            return
        sender_cs = contact.get("callsign", "")
        if not sender_cs:
            return

        # If another node uses the same callsign, flag it
        if sender_cs.upper() == self._my_callsign.upper():
            # Check it's not our own SA (compare UID)
            uid = root.get("uid", "")
            if uid and not uid.startswith(self._my_callsign):
                if not self._duplicate_callsign:
                    logger.warning(
                        "DUPLICATE CALLSIGN detected — another %s is on the network",
                        sender_cs,
                    )
                self._duplicate_callsign = True

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

        # Check SA events for duplicate callsign detection
        if cot_type.startswith("a-f-") or cot_type.startswith("a-n-"):
            self._check_duplicate_callsign(root)

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

        raw_text = remarks_el.text.strip()

        # Check if this looks like a HYDRA command BEFORE HMAC verification
        # so non-command chat messages don't trigger HMAC rejection noise
        check_text = raw_text.split("|HMAC:")[0].strip() if "|HMAC:" in raw_text else raw_text
        if not _CMD_RE.match(check_text):
            return  # Not a command — ignore silently

        # Verify HMAC if configured (strips |HMAC: suffix)
        text = self._verify_hmac(raw_text)
        if text is None:
            return

        match = _CMD_RE.match(text)
        if not match:
            return

        command_prefix = match.group(1)
        action = match.group(2).upper()
        track_id_str = match.group(3)

        # Extract sender callsign for logging and auth
        chat_el = root.find("detail/__chat")
        sender = chat_el.get("senderCallsign", "?") if chat_el is not None else "?"
        source = f"GeoChat/{sender}"

        # Callsign allowlist check (fail-closed)
        if not self._check_sender_allowed(sender):
            return

        # Callsign routing check
        if not self._check_routing(command_prefix):
            logger.debug(
                "TAK input: command prefix %s not addressed to %s — ignoring",
                command_prefix, self._my_callsign,
            )
            return

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
        # Extract sender callsign from contact element or UID
        contact = root.find("detail/contact")
        sender = contact.get("callsign", "?") if contact is not None else "?"
        if sender == "?":
            sender = root.get("uid", "?")
        source = f"CoT/{sender}"

        # HMAC verification on custom CoT (same security as GeoChat path)
        if self._hmac_secret:
            remarks_el = root.find("detail/remarks")
            remarks_text = remarks_el.text.strip() if remarks_el is not None and remarks_el.text else ""
            if self._verify_hmac(remarks_text) is None:
                audit_logger.warning(
                    "TAK_CMD_REJECTED reason=hmac_custom_cot sender=%s type=%s",
                    sender, cot_type,
                )
                return

        # Callsign allowlist check (fail-closed)
        if not self._check_sender_allowed(sender):
            return

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
