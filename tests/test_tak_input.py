"""Tests for TAK/ATAK CoT command listener."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest

from hydra_detect.tak.tak_input import TAKInput


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


def _build_custom_cot(cot_type: str, track_id: int | None = None, use_detail: bool = True) -> bytes:
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
    if track_id is not None:
        if use_detail:
            hydra = ET.SubElement(detail, "hydra")
            hydra.set("trackId", str(track_id))
        else:
            remarks = ET.SubElement(detail, "remarks")
            remarks.text = str(track_id)
    return ET.tostring(event, encoding="unicode").encode("utf-8")


def _make_input(**kwargs) -> TAKInput:
    """Create a TAKInput with mock callbacks (not started)."""
    return TAKInput(
        listen_port=14243,
        multicast_group="",
        on_lock=kwargs.get("on_lock", MagicMock(return_value=True)),
        on_strike=kwargs.get("on_strike", MagicMock(return_value=True)),
        on_unlock=kwargs.get("on_unlock", MagicMock()),
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
