"""Tests for TAK/ATAK CoT command listener."""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import time
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

from hydra_detect.tak.tak_input import TAKInput, _COMMAND_LOG_MAXLEN


# ── helpers ──────────────────────────────────────────────────────────

def _build_geochat(remarks: str, sender: str = "ALPHA-1") -> bytes:
    """Build a minimal GeoChat CoT event."""
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", f"GeoChat.{sender}.All.{id(remarks)}")
    event.set("type", "b-t-f")
    event.set("time", "2026-03-26T12:00:00Z")
    event.set("start", "2026-03-26T12:00:00Z")
    event.set("stale", "2026-03-26T12:01:00Z")
    event.set("how", "h-g-i-g-o")
    point = ET.SubElement(event, "point")
    point.set("lat", "0")
    point.set("lon", "0")
    point.set("hae", "0")
    point.set("ce", "9999999")
    point.set("le", "9999999")
    detail = ET.SubElement(event, "detail")
    chat = ET.SubElement(detail, "__chat")
    chat.set("chatroom", "All Chat Rooms")
    chat.set("senderCallsign", sender)
    remarks_el = ET.SubElement(detail, "remarks")
    remarks_el.text = remarks
    return ET.tostring(event, encoding="unicode").encode("utf-8")


def _build_custom_cot(cot_type: str, track_id: int | None = None, use_detail: bool = True,
                      sender_callsign: str = "ALPHA-1") -> bytes:
    """Build a custom a-x-hydra-* CoT event."""
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", "HYDRA-CMD-TEST")
    event.set("type", cot_type)
    event.set("time", "2026-03-26T12:00:00Z")
    event.set("start", "2026-03-26T12:00:00Z")
    event.set("stale", "2026-03-26T12:01:00Z")
    event.set("how", "h-e")
    point = ET.SubElement(event, "point")
    point.set("lat", "0")
    point.set("lon", "0")
    point.set("hae", "0")
    point.set("ce", "9999999")
    point.set("le", "9999999")
    detail = ET.SubElement(event, "detail")
    # Add contact element for sender callsign (required by security checks)
    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", sender_callsign)
    if track_id is not None:
        if use_detail:
            hydra = ET.SubElement(detail, "hydra")
            hydra.set("trackId", str(track_id))
        else:
            remarks = ET.SubElement(detail, "remarks")
            remarks.text = str(track_id)
    return ET.tostring(event, encoding="unicode").encode("utf-8")


def _make_input(**kwargs) -> TAKInput:
    """Create a TAKInput with mock callbacks (not started).

    Defaults to allowing the "ALPHA-1" callsign so existing tests pass.
    """
    return TAKInput(
        listen_port=16969,
        multicast_group="",
        on_lock=kwargs.get("on_lock", MagicMock(return_value=True)),
        on_strike=kwargs.get("on_strike", MagicMock(return_value=True)),
        on_unlock=kwargs.get("on_unlock", MagicMock()),
        allowed_callsigns=kwargs.get("allowed_callsigns", ["ALPHA-1"]),
        my_callsign=kwargs.get("my_callsign", "HYDRA-1"),
    )


# =====================================================================
# Group A: GeoChat parsing
# =====================================================================

class TestGeoChatParsing:
    def test_lock_command(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5"))
        ti._on_lock.assert_called_once_with(5)

    def test_strike_command(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA STRIKE 12"))
        ti._on_strike.assert_called_once_with(12)

    def test_unlock_command(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA UNLOCK"))
        ti._on_unlock.assert_called_once()

    def test_case_insensitive(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("hydra lock 3"))
        ti._on_lock.assert_called_once_with(3)

    def test_extra_whitespace(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("  HYDRA   LOCK   7  "))
        ti._on_lock.assert_called_once_with(7)

    def test_non_hydra_message_ignored(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("Hello world"))
        ti._on_lock.assert_not_called()
        ti._on_strike.assert_not_called()
        ti._on_unlock.assert_not_called()

    def test_lock_missing_track_id(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA LOCK"))
        ti._on_lock.assert_not_called()

    def test_strike_missing_track_id(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA STRIKE"))
        ti._on_strike.assert_not_called()

    def test_invalid_track_id(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA LOCK abc"))
        ti._on_lock.assert_not_called()

    def test_non_geochat_type_not_parsed(self):
        """A non b-t-f event with HYDRA LOCK in remarks should NOT trigger."""
        event = ET.Element("event")
        event.set("version", "2.0")
        event.set("uid", "test")
        event.set("type", "a-f-G")
        event.set("time", "2026-03-26T12:00:00Z")
        event.set("start", "2026-03-26T12:00:00Z")
        event.set("stale", "2026-03-26T12:01:00Z")
        event.set("how", "m-g")
        ET.SubElement(event, "point").set("lat", "0")
        detail = ET.SubElement(event, "detail")
        remarks = ET.SubElement(detail, "remarks")
        remarks.text = "HYDRA LOCK 5"
        data = ET.tostring(event, encoding="unicode").encode("utf-8")
        ti = _make_input()
        ti._handle_datagram(data)
        ti._on_lock.assert_not_called()

    def test_commands_dispatched_counter(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA LOCK 1"))
        ti._handle_datagram(_build_geochat("HYDRA UNLOCK"))
        assert ti._commands_dispatched == 2


# =====================================================================
# Group B: Custom CoT type parsing
# =====================================================================

class TestCustomTypeParsing:
    def test_lock_with_hydra_detail(self):
        ti = _make_input()
        ti._handle_datagram(_build_custom_cot("a-x-hydra-l", track_id=5))
        ti._on_lock.assert_called_once_with(5)

    def test_strike_with_remarks_fallback(self):
        ti = _make_input()
        ti._handle_datagram(_build_custom_cot("a-x-hydra-s", track_id=7, use_detail=False))
        ti._on_strike.assert_called_once_with(7)

    def test_unlock(self):
        ti = _make_input()
        ti._handle_datagram(_build_custom_cot("a-x-hydra-u"))
        ti._on_unlock.assert_called_once()

    def test_lock_missing_track_id(self):
        ti = _make_input()
        ti._handle_datagram(_build_custom_cot("a-x-hydra-l"))
        ti._on_lock.assert_not_called()

    def test_unknown_suffix_ignored(self):
        ti = _make_input()
        ti._handle_datagram(_build_custom_cot("a-x-hydra-z"))
        ti._on_lock.assert_not_called()
        ti._on_strike.assert_not_called()
        ti._on_unlock.assert_not_called()


# =====================================================================
# Group C: Lifecycle
# =====================================================================

class TestTAKInputLifecycle:
    def test_start_and_stop(self):
        ti = _make_input()
        assert ti.start()
        assert ti._thread is not None
        assert ti._thread.is_alive()
        ti.stop()
        assert ti._thread is None
        assert ti._sock is None

    def test_get_status(self):
        ti = _make_input()
        status = ti.get_status()
        assert "listening" in status
        assert "port" in status
        assert "commands_dispatched" in status

    def test_invalid_xml_doesnt_crash(self):
        ti = _make_input()
        ti._handle_datagram(b"not xml at all")
        # Should not raise

    def test_empty_datagram_doesnt_crash(self):
        ti = _make_input()
        ti._handle_datagram(b"")
        # Should not raise


# =====================================================================
# Group D: Events counter
# =====================================================================

class TestEventsCounter:
    def test_events_received_increments(self):
        ti = _make_input()
        assert ti._events_received == 0
        ti._handle_datagram(_build_geochat("hello"))
        # events_received is incremented in _listener_loop, not _handle_datagram
        # but commands_dispatched IS tracked in _handle_datagram
        ti._handle_datagram(_build_geochat("HYDRA LOCK 1"))
        assert ti._commands_dispatched == 1


# =====================================================================
# Group E: Command event log (B1 — /api/tak/commands feed)
# =====================================================================

def _sign_hmac(text: str, secret: str) -> str:
    sig = hmac_mod.new(
        secret.encode("utf-8"), text.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return f"{text}|HMAC:{sig}"


class TestCommandEventLog:
    """B1 handoff spec: bounded deque of accepted+rejected commands."""

    def test_accept_path_lock_logged(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        events = ti.get_recent_commands(10)
        assert len(events) == 1
        ev = events[0]
        assert ev["accepted"] is True
        assert ev["reject_reason"] is None
        assert ev["sender"] == "ALPHA-1"
        assert ev["action"] == "LOCK"
        assert ev["track_id"] == 5
        assert ev["hmac_state"] == "disabled"
        assert ev["routing"] == "fleet"  # bare HYDRA prefix
        assert ev["addressee"].upper() == "HYDRA"
        assert isinstance(ev["ts"], float) and ev["ts"] > 0
        assert "HYDRA LOCK 5" in ev["raw_text"]

    def test_accept_path_unlock_track_id_none(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("HYDRA UNLOCK", sender="ALPHA-1"))
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is True
        assert ev["action"] == "UNLOCK"
        assert ev["track_id"] is None

    def test_accept_routing_direct(self):
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"], my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-2-USV LOCK 5", sender="ALPHA-1")
        )
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is True
        assert ev["routing"] == "direct"

    def test_accept_routing_segment_wildcard(self):
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"], my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-ALL-USV LOCK 5", sender="ALPHA-1")
        )
        ev = ti.get_recent_commands(10)[-1]
        assert ev["routing"] == "segment_wildcard"

    def test_reject_unauthorized_sender(self):
        ti = _make_input(allowed_callsigns=["ALPHA-1"])
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="HACKER"))
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is False
        assert ev["reject_reason"] == "unauthorized_sender"
        assert ev["sender"] == "HACKER"
        assert ev["action"] == "LOCK"
        assert ev["track_id"] == 5

    def test_reject_no_allowlist(self):
        ti = _make_input(allowed_callsigns=None)
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is False
        assert ev["reject_reason"] == "no_allowlist"
        assert ev["action"] == "LOCK"

    def test_reject_hmac_missing(self):
        ti = TAKInput(
            listen_port=16969, multicast_group="",
            on_lock=MagicMock(return_value=True),
            on_strike=MagicMock(return_value=True),
            on_unlock=MagicMock(),
            allowed_callsigns=["ALPHA-1"], hmac_secret="shhh",
            my_callsign="HYDRA-1",
        )
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is False
        assert ev["reject_reason"] == "hmac_missing"
        assert ev["hmac_state"] == "missing"

    def test_reject_hmac_invalid(self):
        ti = TAKInput(
            listen_port=16969, multicast_group="",
            on_lock=MagicMock(return_value=True),
            on_strike=MagicMock(return_value=True),
            on_unlock=MagicMock(),
            allowed_callsigns=["ALPHA-1"], hmac_secret="shhh",
            my_callsign="HYDRA-1",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA LOCK 5|HMAC:deadbeef", sender="ALPHA-1")
        )
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is False
        assert ev["reject_reason"] == "hmac_invalid"
        assert ev["hmac_state"] == "invalid"

    def test_hmac_verified_state_on_accept(self):
        secret = "s3cret"
        signed = _sign_hmac("HYDRA LOCK 7", secret)
        ti = TAKInput(
            listen_port=16969, multicast_group="",
            on_lock=MagicMock(return_value=True),
            on_strike=MagicMock(return_value=True),
            on_unlock=MagicMock(),
            allowed_callsigns=["ALPHA-1"], hmac_secret=secret,
            my_callsign="HYDRA-1",
        )
        ti._handle_datagram(_build_geochat(signed, sender="ALPHA-1"))
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is True
        assert ev["hmac_state"] == "verified"

    def test_not_addressed_to_us_is_not_logged(self):
        """Commands routed to other vehicles are silently dropped, not logged."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"], my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-3-DRONE LOCK 5", sender="ALPHA-1")
        )
        assert ti.get_recent_commands(10) == []

    def test_non_command_chat_not_logged(self):
        ti = _make_input()
        ti._handle_datagram(_build_geochat("Hello world", sender="ALPHA-1"))
        assert ti.get_recent_commands(10) == []

    def test_bounded_deque_eviction(self):
        """Deque is bounded — oldest entries evicted past the cap."""
        ti = _make_input()
        cap = _COMMAND_LOG_MAXLEN
        # Push cap+10 accepted events via the public helper
        for i in range(cap + 10):
            ti._log_command_event(
                accepted=True, sender="ALPHA-1", addressee="HYDRA",
                action="LOCK", track_id=i, hmac_state="disabled",
                routing="fleet", reject_reason=None,
                raw_text=f"HYDRA LOCK {i}",
            )
        events = ti.get_recent_commands(cap + 20)
        assert len(events) == cap
        # Oldest surviving track_id should be 10 (first 10 evicted)
        assert events[0]["track_id"] == 10
        assert events[-1]["track_id"] == cap + 9

    def test_raw_text_truncated(self):
        ti = _make_input()
        long = "HYDRA LOCK 5 " + ("X" * 500)
        ti._log_command_event(
            accepted=False, sender="ALPHA-1", addressee="HYDRA",
            action="LOCK", track_id=5, hmac_state="disabled",
            routing="fleet", reject_reason="unauthorized_sender",
            raw_text=long,
        )
        ev = ti.get_recent_commands(1)[-1]
        assert len(ev["raw_text"]) == 200

    def test_get_recent_commands_limit(self):
        ti = _make_input()
        for i in range(20):
            ti._log_command_event(
                accepted=True, sender="ALPHA-1", addressee="HYDRA",
                action="LOCK", track_id=i, hmac_state="disabled",
                routing="fleet", reject_reason=None, raw_text="",
            )
        assert len(ti.get_recent_commands(5)) == 5
        # Returns the NEWEST 5
        tail = ti.get_recent_commands(5)
        assert [e["track_id"] for e in tail] == [15, 16, 17, 18, 19]
        # Zero/negative limit returns empty list
        assert ti.get_recent_commands(0) == []
        assert ti.get_recent_commands(-1) == []

    def test_custom_cot_accept_logged(self):
        ti = _make_input(allowed_callsigns=["ALPHA-1"])
        ti._handle_datagram(_build_custom_cot(
            "a-x-hydra-l", track_id=5, sender_callsign="ALPHA-1",
        ))
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is True
        assert ev["action"] == "LOCK"
        assert ev["track_id"] == 5
        assert ev["addressee"] == "a-x-hydra-l"
        assert ev["routing"] == "direct"

    def test_custom_cot_unauthorized_logged(self):
        ti = _make_input(allowed_callsigns=["ALPHA-1"])
        ti._handle_datagram(_build_custom_cot(
            "a-x-hydra-l", track_id=5, sender_callsign="HACKER",
        ))
        ev = ti.get_recent_commands(10)[-1]
        assert ev["accepted"] is False
        assert ev["reject_reason"] == "unauthorized_sender"


# =====================================================================
# Group H: Inbound CoT type histogram (feeds /api/tak/type_counts)
# =====================================================================

def _build_sa_event(
    *, uid: str, callsign: str, cot_type: str = "a-f-G-U-C",
    lat: float = 34.5, lon: float = -118.0,
) -> bytes:
    """Build a minimal SA CoT event (friendly/neutral/hostile prefix)."""
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", uid)
    event.set("type", cot_type)
    event.set("time", "2026-04-19T12:00:00Z")
    event.set("start", "2026-04-19T12:00:00Z")
    event.set("stale", "2026-04-19T12:02:00Z")
    event.set("how", "m-g")
    point = ET.SubElement(event, "point")
    point.set("lat", str(lat))
    point.set("lon", str(lon))
    point.set("hae", "100")
    point.set("ce", "10")
    point.set("le", "10")
    detail = ET.SubElement(event, "detail")
    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", callsign)
    return ET.tostring(event, encoding="unicode").encode("utf-8")


class TestCoTTypeHistogram:
    def test_empty(self):
        ti = _make_input()
        hist = ti.get_type_counts()
        assert hist["counts"] == {}
        assert hist["total"] == 0
        assert hist["window_seconds"] > 0

    def test_accept_and_reject_both_increment(self):
        ti = _make_input(allowed_callsigns=["ALPHA-1"])
        # Accepted LOCK — b-t-f
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        # Rejected (unauthorized) — still b-t-f
        ti._handle_datagram(_build_geochat("HYDRA LOCK 6", sender="MALLORY"))
        # Non-command SA event — a-f-G-U-C
        ti._handle_datagram(_build_sa_event(
            uid="FRIEND-1", callsign="FRIEND-1", cot_type="a-f-G-U-C",
        ))
        hist = ti.get_type_counts()
        assert hist["counts"].get("b-t-f") == 2
        assert hist["counts"].get("a-f-G-U-C") == 1
        assert hist["total"] == 3

    def test_window_evicts_old_samples(self):
        ti = _make_input()
        # Backdate a sample outside the window
        old_ts = time.time() - 10_000
        ti._type_events.append((old_ts, "b-t-f"))
        hist = ti.get_type_counts(window_seconds=60)
        assert hist["counts"] == {}
        assert hist["total"] == 0

    def test_shape_has_required_fields(self):
        ti = _make_input()
        ti._handle_datagram(_build_sa_event(
            uid="FOE-1", callsign="FOE-1", cot_type="a-h-G",
        ))
        hist = ti.get_type_counts(window_seconds=300)
        assert set(hist.keys()) == {"counts", "total", "window_seconds"}
        assert hist["window_seconds"] == 300


# =====================================================================
# Group I: Inbound peer roster (feeds /api/tak/peers)
# =====================================================================

class TestPeerRoster:
    def test_empty(self):
        ti = _make_input()
        assert ti.get_peers() == []

    def test_add_peer(self):
        ti = _make_input()
        ti._handle_datagram(_build_sa_event(
            uid="FRIEND-1", callsign="FRIEND-1",
            cot_type="a-f-G-U-C", lat=34.0, lon=-118.0,
        ))
        peers = ti.get_peers()
        assert len(peers) == 1
        assert peers[0]["uid"] == "FRIEND-1"
        assert peers[0]["callsign"] == "FRIEND-1"
        assert peers[0]["cot_type"] == "a-f-G-U-C"
        assert peers[0]["lat"] == 34.0
        assert peers[0]["lon"] == -118.0

    def test_own_sa_excluded(self):
        ti = _make_input(my_callsign="HYDRA-1")
        ti._handle_datagram(_build_sa_event(
            uid="HYDRA-1-SA", callsign="HYDRA-1",
            cot_type="a-f-G-U-C",
        ))
        assert ti.get_peers() == []

    def test_evict_stale(self):
        ti = _make_input()
        # Inject a peer directly with stale last_seen
        ti._peers["STALE-1"] = {
            "uid": "STALE-1", "callsign": "STALE-1",
            "cot_type": "a-f-G-U-C", "lat": 0.0, "lon": 0.0,
            "last_seen": time.time() - 10_000,
        }
        peers = ti.get_peers()
        assert peers == []

    def test_hostile_tracked(self):
        ti = _make_input()
        ti._handle_datagram(_build_sa_event(
            uid="FOE-1", callsign="FOE-1", cot_type="a-h-G",
        ))
        peers = ti.get_peers()
        assert len(peers) == 1
        assert peers[0]["cot_type"] == "a-h-G"

    def test_duplicate_uid_updates_in_place(self):
        ti = _make_input()
        ti._handle_datagram(_build_sa_event(
            uid="FRIEND-1", callsign="FRIEND-1",
            cot_type="a-f-G-U-C", lat=10.0, lon=10.0,
        ))
        ti._handle_datagram(_build_sa_event(
            uid="FRIEND-1", callsign="FRIEND-1",
            cot_type="a-f-G-U-C", lat=20.0, lon=20.0,
        ))
        peers = ti.get_peers()
        assert len(peers) == 1
        assert peers[0]["lat"] == 20.0
        assert peers[0]["lon"] == 20.0
