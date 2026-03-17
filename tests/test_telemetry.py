"""Tests for vehicle telemetry parsing in MAVLinkIO."""
from __future__ import annotations

from unittest.mock import MagicMock

from hydra_detect.mavlink_io import MAVLinkIO


def _make_mavlink(**kwargs) -> MAVLinkIO:
    defaults = dict(connection_string="tcp:127.0.0.1:5760", baud=115200)
    defaults.update(kwargs)
    return MAVLinkIO(**defaults)


class TestTelemetryInit:
    def test_telemetry_dict_exists(self):
        m = _make_mavlink()
        assert hasattr(m, '_telemetry')
        assert m._telemetry["armed"] is False
        assert m._telemetry["battery_v"] is None
        assert m._telemetry["groundspeed"] is None

    def test_get_telemetry_returns_merged_dict(self):
        m = _make_mavlink(min_gps_fix=3)
        m._gps.update({"fix": 3, "lat": 340000000, "lon": -1180000000, "alt": 100000, "hdg": 9000})
        m._telemetry.update({"armed": True, "battery_v": 12.6, "battery_pct": 87})
        m._vehicle_mode = "AUTO"
        t = m.get_telemetry()
        assert t["lat"] == 340000000
        assert t["fix"] == 3
        assert t["armed"] is True
        assert t["battery_v"] == 12.6
        assert t["vehicle_mode"] == "AUTO"


class TestArmedStateParsing:
    def test_armed_flag_extracted(self):
        m = _make_mavlink()
        hb = MagicMock()
        hb.base_mode = 128 | 1  # armed + custom mode
        m._update_armed_state(hb)
        assert m._telemetry["armed"] is True

    def test_disarmed_flag(self):
        m = _make_mavlink()
        hb = MagicMock()
        hb.base_mode = 1  # no armed flag
        m._update_armed_state(hb)
        assert m._telemetry["armed"] is False
