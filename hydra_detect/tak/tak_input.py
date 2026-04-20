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
from collections import deque
from typing import Callable

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("hydra.audit")

# Matches command text like "HYDRA LOCK 5", "HYDRA-2-USV LOCK 5", "HYDRA-ALL LOCK 5"
_CMD_RE = re.compile(
    r"^\s*([\w-]+)\s+(LOCK|STRIKE|UNLOCK)(?:\s+(\d+))?\s*$",
    re.IGNORECASE,
)

# Command event log bound and raw_text truncation width
_COMMAND_LOG_MAXLEN = 500
_RAW_TEXT_TRUNC = 200

# Bounded CoT-type histogram window (seconds) and hard cap on the ring.
# Feeds /api/tak/type_counts. Entries older than the window are filtered at
# read time; the hard cap prevents unbounded growth during a flood.
_TYPE_HISTOGRAM_WINDOW_SEC = 900  # 15 minutes
_TYPE_HISTOGRAM_MAXLEN = 4000

# Bounded inbound peer roster (B3). Keyed by uid. Hard cap + stale pruning
# (last_seen older than _PEER_STALE_SEC) guard against unbounded growth.
_PEER_ROSTER_MAXLEN = 200
_PEER_STALE_SEC = 300.0  # 5 minutes


def _classify_routing(command_prefix: str) -> str:
    """Classify how a GeoChat command prefix addresses vehicles.

    Returns one of: "direct" (exact callsign), "fleet" (HYDRA/HYDRA-ALL
    broadcast), or "segment_wildcard" (contains ALL in a segment).
    """
    upper = command_prefix.upper()
    if upper in ("HYDRA", "HYDRA-ALL"):
        return "fleet"
    if "ALL" in upper.split("-"):
        return "segment_wildcard"
    return "direct"


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
        logger.debug("Bare HYDRA prefix match — consider using exact callsign")
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
        self._duplicate_callsign_time: float = 0.0

        # Bounded ring of recent accepted/rejected commands — powers the
        # /api/tak/commands feed. Lock-protected; safe across listener
        # thread and request handlers.
        self._command_log: deque[dict] = deque(maxlen=_COMMAND_LOG_MAXLEN)
        self._command_log_lock = threading.Lock()

        # Bounded (ts, cot_type) ring for the inbound CoT-type histogram —
        # powers /api/tak/type_counts. Entries older than the configured
        # window are filtered at read time.
        self._type_events: deque[tuple[float, str]] = deque(
            maxlen=_TYPE_HISTOGRAM_MAXLEN,
        )
        self._type_events_lock = threading.Lock()

        # Bounded peer roster (uid -> {callsign, uid, last_seen, lat, lon,
        # cot_type, affiliation}) — powers /api/tak/peers. Hard cap +
        # stale pruning guard against unbounded growth.
        self._peers: dict[str, dict] = {}
        self._peers_lock = threading.Lock()

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
            self._sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024,
            )
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

    def is_running(self) -> bool:
        """Return True if the listener thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Return status dict for the web API."""
        # Auto-clear duplicate callsign flag after 60 s without re-detection
        if self._duplicate_callsign and (
            time.monotonic() - self._duplicate_callsign_time
        ) > 60:
            self._duplicate_callsign = False
        return {
            "listening": self.is_running(),
            "port": self._port,
            "events_received": self._events_received,
            "commands_dispatched": self._commands_dispatched,
            "commands_rejected": self._commands_rejected,
            "duplicate_callsign": self._duplicate_callsign,
        }

    # ------------------------------------------------------------------
    # Security checks
    # ------------------------------------------------------------------
    def _check_sender_allowed(
        self, sender_callsign: str,
    ) -> tuple[bool, str | None]:
        """Check if a sender callsign is in the allowlist (fail-closed).

        Returns (allowed, reject_reason). reject_reason is None on accept
        or one of: "no_allowlist", "unauthorized_sender".
        """
        if not self._allowed_callsigns:
            logger.warning(
                "TAK commands disabled — no allowed callsigns configured"
            )
            audit_logger.warning(
                "TAK_CMD_REJECTED reason=no_allowlist sender=%s",
                sender_callsign,
            )
            self._commands_rejected += 1
            return False, "no_allowlist"

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
            return False, "unauthorized_sender"

        return True, None

    def _verify_hmac(self, text: str) -> tuple[str | None, str, str | None]:
        """Verify HMAC on command text.

        Returns (msg_text, hmac_state, reject_reason):
        - msg_text: command text sans HMAC suffix, or None on reject
        - hmac_state: one of "disabled" (no secret), "verified", "missing",
          or "invalid"
        - reject_reason: None on accept, or "hmac_missing" / "hmac_invalid"
        """
        if not self._hmac_secret:
            return text, "disabled", None

        # Expect format: "HYDRA LOCK 5|HMAC:xxxx"
        if "|HMAC:" not in text:
            logger.warning("TAK input: HMAC required but missing from command")
            audit_logger.warning(
                "TAK_CMD_REJECTED reason=hmac_missing text=%s", text[:80],
            )
            self._commands_rejected += 1
            return None, "missing", "hmac_missing"

        parts = text.rsplit("|HMAC:", 1)
        if len(parts) != 2:
            self._commands_rejected += 1
            return None, "invalid", "hmac_invalid"

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
            return None, "invalid", "hmac_invalid"

        return msg_text, "verified", None

    def _check_routing(self, command_prefix: str) -> bool:
        """Check if command is addressed to this vehicle."""
        return _callsign_matches(command_prefix, self._my_callsign)

    # ------------------------------------------------------------------
    # Command event log (feeds /api/tak/commands)
    # ------------------------------------------------------------------
    def _log_command_event(
        self,
        *,
        accepted: bool,
        sender: str,
        addressee: str,
        action: str,
        track_id: int | None,
        hmac_state: str,
        routing: str,
        reject_reason: str | None,
        raw_text: str,
    ) -> None:
        """Push one accepted or rejected command into the bounded ring."""
        entry = {
            "ts": time.time(),
            "accepted": bool(accepted),
            "sender": sender or "",
            "addressee": addressee or "",
            "action": action or "",
            "track_id": track_id,
            "hmac_state": hmac_state,
            "routing": routing,
            "reject_reason": reject_reason,
            "raw_text": (raw_text or "")[:_RAW_TEXT_TRUNC],
        }
        with self._command_log_lock:
            self._command_log.append(entry)
        # Emit a structured audit line so the B9 audit sink tallies
        # accepted commands alongside the existing TAK_CMD_REJECTED lines.
        # Rejected commands already emit their own audit lines at the
        # point of rejection, so we only emit here on accept.
        if accepted:
            audit_logger.info(
                "TAK_CMD_ACCEPTED action=%s track_id=%s sender=%s",
                action or "",
                track_id if track_id is not None else "",
                sender or "",
            )

    def get_recent_commands(self, limit: int = 100) -> list[dict]:
        """Return up to `limit` most recent command events (newest last)."""
        if limit <= 0:
            return []
        with self._command_log_lock:
            items = list(self._command_log)
        return items[-limit:]

    # ------------------------------------------------------------------
    # Inbound CoT type histogram (feeds /api/tak/type_counts)
    # ------------------------------------------------------------------
    def _record_cot_type(self, cot_type: str) -> None:
        """Push one (ts, cot_type) sample into the bounded histogram ring."""
        if not cot_type:
            return
        with self._type_events_lock:
            self._type_events.append((time.time(), cot_type))

    def get_type_counts(
        self, window_seconds: float = _TYPE_HISTOGRAM_WINDOW_SEC,
    ) -> dict:
        """Return a CoT-type histogram over the most recent `window_seconds`.

        Returns a dict of the form::

            {"counts": {cot_type: N, ...}, "total": N, "window_seconds": N}

        Evicts samples older than the window from the backing deque as a
        side effect, so repeated calls keep the ring tight.
        """
        now = time.time()
        cutoff = now - max(0.0, float(window_seconds))
        counts: dict[str, int] = {}
        with self._type_events_lock:
            # Evict oldest samples that fall outside the window.
            while self._type_events and self._type_events[0][0] < cutoff:
                self._type_events.popleft()
            for _ts, cot_type in self._type_events:
                counts[cot_type] = counts.get(cot_type, 0) + 1
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "window_seconds": int(window_seconds),
        }

    # ------------------------------------------------------------------
    # Inbound peer roster (feeds /api/tak/peers)
    # ------------------------------------------------------------------
    def _record_peer(self, root: ET.Element, cot_type: str) -> None:
        """Record a peer SA event in the bounded roster.

        Own SA (UID starting with our callsign) is excluded. Entries older
        than _PEER_STALE_SEC are pruned; at most _PEER_ROSTER_MAXLEN peers
        are retained (evicts oldest when full).
        """
        uid = root.get("uid", "")
        if not uid:
            return
        # Exclude our own SA beacon.
        if uid.startswith(self._my_callsign):
            return
        contact = root.find("detail/contact")
        callsign = contact.get("callsign", "") if contact is not None else ""
        point = root.find("point")
        lat: float | None = None
        lon: float | None = None
        if point is not None:
            try:
                lat = float(point.get("lat", "0"))
                lon = float(point.get("lon", "0"))
            except (ValueError, TypeError):
                lat = lon = None

        entry = {
            "uid": uid,
            "callsign": callsign,
            "cot_type": cot_type,
            "lat": lat,
            "lon": lon,
            "last_seen": time.time(),
        }
        now = entry["last_seen"]
        with self._peers_lock:
            self._peers[uid] = entry
            # Prune stale peers.
            stale_cutoff = now - _PEER_STALE_SEC
            stale = [
                k for k, v in self._peers.items()
                if v["last_seen"] < stale_cutoff
            ]
            for k in stale:
                del self._peers[k]
            # Enforce hard cap — drop oldest-seen peers if over limit.
            if len(self._peers) > _PEER_ROSTER_MAXLEN:
                ordered = sorted(
                    self._peers.items(), key=lambda kv: kv[1]["last_seen"],
                )
                for k, _v in ordered[: len(self._peers) - _PEER_ROSTER_MAXLEN]:
                    del self._peers[k]

    def get_peers(self) -> list[dict]:
        """Return the current peer roster, newest-first.

        Evicts peers whose `last_seen` is older than _PEER_STALE_SEC as a
        side effect so callers don't get stale ghosts.
        """
        now = time.time()
        stale_cutoff = now - _PEER_STALE_SEC
        with self._peers_lock:
            stale = [
                k for k, v in self._peers.items()
                if v["last_seen"] < stale_cutoff
            ]
            for k in stale:
                del self._peers[k]
            peers = list(self._peers.values())
        peers.sort(key=lambda p: p["last_seen"], reverse=True)
        return peers

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
                self._duplicate_callsign_time = time.monotonic()

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
            # Drop oversized packets before parsing (max reasonable CoT = 8 KB)
            if len(data) > 8192:
                logger.debug("TAK input: dropping oversized packet (%d bytes)", len(data))
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

        # Record every inbound CoT type for /api/tak/type_counts.
        self._record_cot_type(cot_type)

        # Check SA events for duplicate callsign detection + peer roster
        if (
            cot_type.startswith("a-f-")
            or cot_type.startswith("a-n-")
            or cot_type.startswith("a-h-")
        ):
            self._check_duplicate_callsign(root)
            self._record_peer(root, cot_type)

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
        pre_match = _CMD_RE.match(check_text)
        if not pre_match:
            return  # Not a command — ignore silently

        # Pre-extract addressee/action/track_id so reject events carry them
        command_prefix = pre_match.group(1)
        action = pre_match.group(2).upper()
        track_id_str = pre_match.group(3)
        try:
            pre_track_id = int(track_id_str) if track_id_str is not None else None
        except ValueError:
            pre_track_id = None
        routing = _classify_routing(command_prefix)

        # Extract sender callsign for logging and auth
        chat_el = root.find("detail/__chat")
        sender = chat_el.get("senderCallsign", "?") if chat_el is not None else "?"
        source = f"GeoChat/{sender}"

        # Verify HMAC if configured (strips |HMAC: suffix)
        text, hmac_state, hmac_reject = self._verify_hmac(raw_text)
        if text is None:
            self._log_command_event(
                accepted=False,
                sender=sender,
                addressee=command_prefix,
                action=action,
                track_id=pre_track_id,
                hmac_state=hmac_state,
                routing=routing,
                reject_reason=hmac_reject,
                raw_text=raw_text,
            )
            return

        match = _CMD_RE.match(text)
        if not match:
            return

        command_prefix = match.group(1)
        action = match.group(2).upper()
        track_id_str = match.group(3)
        routing = _classify_routing(command_prefix)
        try:
            track_id = int(track_id_str) if track_id_str is not None else None
        except ValueError:
            track_id = None

        # Callsign allowlist check (fail-closed)
        allowed, allow_reject = self._check_sender_allowed(sender)
        if not allowed:
            self._log_command_event(
                accepted=False,
                sender=sender,
                addressee=command_prefix,
                action=action,
                track_id=track_id,
                hmac_state=hmac_state,
                routing=routing,
                reject_reason=allow_reject,
                raw_text=raw_text,
            )
            return

        # Callsign routing check — commands addressed to other vehicles are
        # not rejections, just not for us. Do not log an event.
        if not self._check_routing(command_prefix):
            logger.debug(
                "TAK input: command prefix %s not addressed to %s — ignoring",
                command_prefix, self._my_callsign,
            )
            return

        if action == "LOCK":
            if track_id is None:
                logger.warning("TAK input: LOCK command missing track_id from %s", source)
                return
            self._log_command_event(
                accepted=True, sender=sender, addressee=command_prefix,
                action=action, track_id=track_id, hmac_state=hmac_state,
                routing=routing, reject_reason=None, raw_text=raw_text,
            )
            self._dispatch_lock(track_id, source)
        elif action == "STRIKE":
            if track_id is None:
                logger.warning("TAK input: STRIKE command missing track_id from %s", source)
                return
            self._log_command_event(
                accepted=True, sender=sender, addressee=command_prefix,
                action=action, track_id=track_id, hmac_state=hmac_state,
                routing=routing, reject_reason=None, raw_text=raw_text,
            )
            self._dispatch_strike(track_id, source)
        elif action == "UNLOCK":
            self._log_command_event(
                accepted=True, sender=sender, addressee=command_prefix,
                action=action, track_id=None, hmac_state=hmac_state,
                routing=routing, reject_reason=None, raw_text=raw_text,
            )
            self._dispatch_unlock(source)

    # ------------------------------------------------------------------
    # Custom CoT type parsing
    # ------------------------------------------------------------------
    def _parse_custom_type(
        self, root: ET.Element, cot_type: str, addr: tuple | None,
    ) -> None:
        """Parse a custom ``a-x-hydra-*`` CoT event."""
        # Unknown suffix — silently ignore before any logging
        suffix = cot_type[len("a-x-hydra-"):]
        if suffix.startswith("l"):
            action = "LOCK"
        elif suffix.startswith("s"):
            action = "STRIKE"
        elif suffix.startswith("u"):
            action = "UNLOCK"
        else:
            return

        # Extract sender callsign from contact element or UID
        contact = root.find("detail/contact")
        sender = contact.get("callsign", "?") if contact is not None else "?"
        if sender == "?":
            sender = root.get("uid", "?")
        source = f"CoT/{sender}"

        remarks_el = root.find("detail/remarks")
        has_text = remarks_el is not None and remarks_el.text
        raw_text = remarks_el.text.strip() if has_text else ""

        addressee = cot_type
        routing = "direct"

        # HMAC verification on custom CoT (same security as GeoChat path)
        if self._hmac_secret:
            _text, hmac_state, hmac_reject = self._verify_hmac(raw_text)
            if _text is None:
                audit_logger.warning(
                    "TAK_CMD_REJECTED reason=hmac_custom_cot sender=%s type=%s",
                    sender, cot_type,
                )
                self._log_command_event(
                    accepted=False, sender=sender, addressee=addressee,
                    action=action, track_id=None, hmac_state=hmac_state,
                    routing=routing, reject_reason=hmac_reject,
                    raw_text=raw_text,
                )
                return
        else:
            hmac_state = "disabled"

        # Callsign allowlist check (fail-closed)
        allowed, allow_reject = self._check_sender_allowed(sender)
        if not allowed:
            self._log_command_event(
                accepted=False, sender=sender, addressee=addressee,
                action=action, track_id=None, hmac_state=hmac_state,
                routing=routing, reject_reason=allow_reject,
                raw_text=raw_text,
            )
            return

        # Extract track_id from detail/hydra/@trackId or detail/remarks
        track_id: int | None = None
        hydra_el = root.find("detail/hydra")
        if hydra_el is not None and hydra_el.get("trackId"):
            try:
                track_id = int(hydra_el.get("trackId"))
            except (ValueError, TypeError):
                pass
        if track_id is None and raw_text:
            try:
                track_id = int(raw_text)
            except (ValueError, TypeError):
                pass

        if action == "LOCK":
            if track_id is None:
                logger.warning("TAK input: custom LOCK missing trackId from %s", source)
                return
            self._log_command_event(
                accepted=True, sender=sender, addressee=addressee,
                action=action, track_id=track_id, hmac_state=hmac_state,
                routing=routing, reject_reason=None, raw_text=raw_text,
            )
            self._dispatch_lock(track_id, source)
        elif action == "STRIKE":
            if track_id is None:
                logger.warning("TAK input: custom STRIKE missing trackId from %s", source)
                return
            self._log_command_event(
                accepted=True, sender=sender, addressee=addressee,
                action=action, track_id=track_id, hmac_state=hmac_state,
                routing=routing, reject_reason=None, raw_text=raw_text,
            )
            self._dispatch_strike(track_id, source)
        else:  # UNLOCK
            self._log_command_event(
                accepted=True, sender=sender, addressee=addressee,
                action=action, track_id=None, hmac_state=hmac_state,
                routing=routing, reject_reason=None, raw_text=raw_text,
            )
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
