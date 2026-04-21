"""Tests for the RF→TAK emitter — mode filtering and CoT emission."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hydra_detect.rf.tak_emitter import RfTakEmitter


def _device(**overrides):
    base = {
        "bssid": "AA:BB:CC:00:00:01",
        "ssid": "CAFE-GUEST",
        "rssi": -65.0,
        "channel": 6,
        "freq_mhz": 2437.0,
        "manuf": "TP-Link",
        "first_seen": 100.0,
        "last_seen": 120.0,
        "lat": None,
        "lon": None,
        "is_target": False,
    }
    base.update(overrides)
    return base


def _make_emitter(mode="off", devices=None, strong_dbm=-60.0):
    tak = MagicMock()
    payload = {"mode": "live", "devices": devices or []}
    emitter = RfTakEmitter(
        tak,
        get_devices=lambda: payload,
        get_self_position=lambda: (34.05, -118.25, 15.0),
        callsign="HYDRA-TEST",
        mode=mode,
        strong_dbm=strong_dbm,
    )
    return emitter, tak


class TestModeFilter:
    def test_off_emits_nothing(self):
        emitter, tak = _make_emitter(
            mode="off",
            devices=[_device(is_target=True), _device(rssi=-40.0)],
        )
        emitter._emit_one_cycle()
        tak.emit_cot.assert_not_called()

    def test_target_emits_only_target(self):
        emitter, tak = _make_emitter(
            mode="target",
            devices=[
                _device(bssid="AA:BB:CC:DE:AD:01", is_target=True),
                _device(bssid="AA:BB:CC:00:00:01", rssi=-40.0),
                _device(bssid="AA:BB:CC:00:00:02"),
            ],
        )
        emitter._emit_one_cycle()
        assert tak.emit_cot.call_count == 1

    def test_strong_respects_threshold(self):
        emitter, tak = _make_emitter(
            mode="strong", strong_dbm=-60.0,
            devices=[
                _device(rssi=-40.0),   # above threshold
                _device(rssi=-55.0),   # above threshold
                _device(rssi=-65.0),   # below threshold
                _device(rssi=-80.0),   # below threshold
            ],
        )
        emitter._emit_one_cycle()
        assert tak.emit_cot.call_count == 2

    def test_all_emits_every_device(self):
        emitter, tak = _make_emitter(
            mode="all",
            devices=[_device(), _device(rssi=-40.0), _device(rssi=-80.0)],
        )
        emitter._emit_one_cycle()
        assert tak.emit_cot.call_count == 3


class TestCoTPayload:
    def test_emitted_payload_is_xml_bytes(self):
        emitter, tak = _make_emitter(
            mode="all",
            devices=[_device(bssid="AA:BB:CC:DE:AD:01", ssid="TARGET-NODE",
                             rssi=-45.0, is_target=True)],
        )
        emitter._emit_one_cycle()
        tak.emit_cot.assert_called_once()
        payload = tak.emit_cot.call_args[0][0]
        assert isinstance(payload, bytes)
        assert b"<event" in payload
        assert b"AA:BB:CC:DE:AD:01" in payload
        assert b"TARGET-NODE" in payload

    def test_target_device_uses_hostile_cot_type(self):
        emitter, tak = _make_emitter(
            mode="all",
            devices=[_device(is_target=True)],
        )
        emitter._emit_one_cycle()
        payload = tak.emit_cot.call_args[0][0]
        # a-h-* is hostile; target upgrade applies.
        assert b'type="a-h-' in payload

    def test_non_target_uses_unknown_cot_type(self):
        emitter, tak = _make_emitter(
            mode="all",
            devices=[_device(is_target=False)],
        )
        emitter._emit_one_cycle()
        payload = tak.emit_cot.call_args[0][0]
        assert b'type="a-u-' in payload


class TestPositionFallback:
    def test_uses_device_gps_when_present(self):
        emitter, tak = _make_emitter(
            mode="all",
            devices=[_device(lat=40.0, lon=-100.0)],
        )
        emitter._emit_one_cycle()
        payload = tak.emit_cot.call_args[0][0]
        assert b'lat="40.0' in payload
        assert b'lon="-100.0' in payload

    def test_falls_back_to_self_position_when_device_gps_missing(self):
        emitter, tak = _make_emitter(
            mode="all",
            devices=[_device(lat=None, lon=None)],
        )
        emitter._emit_one_cycle()
        payload = tak.emit_cot.call_args[0][0]
        assert b'lat="34.05' in payload
        assert b'lon="-118.25' in payload

    def test_skips_cycle_when_no_self_position(self):
        tak = MagicMock()
        emitter = RfTakEmitter(
            tak,
            get_devices=lambda: {"devices": [_device()]},
            get_self_position=lambda: (None, None, None),
            mode="all",
        )
        emitter._emit_one_cycle()
        tak.emit_cot.assert_not_called()


class TestSetMode:
    def test_set_mode_updates_filter(self):
        emitter, tak = _make_emitter(
            mode="off", devices=[_device()],
        )
        emitter._emit_one_cycle()
        tak.emit_cot.assert_not_called()
        emitter.set_mode("all")
        emitter._emit_one_cycle()
        assert tak.emit_cot.call_count == 1

    def test_set_mode_rejects_invalid(self):
        emitter, _ = _make_emitter(mode="off")
        emitter.set_mode("invalid")
        assert emitter.mode == "off"


@pytest.mark.parametrize("mode", ["off", "target", "strong", "all"])
def test_mode_accessor(mode):
    emitter, _ = _make_emitter(mode=mode)
    assert emitter.mode == mode
