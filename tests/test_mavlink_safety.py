"""Tests for MAVLink safety logic: GPS checks, mode setting, auto-loiter guard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hydra_detect.mavlink_io import MAVLinkIO


def _make_mavlink(**kwargs) -> MAVLinkIO:
    """Create a MAVLinkIO instance without a real connection."""
    defaults = dict(
        connection_string="tcp:127.0.0.1:5760",
        baud=115200,
        auto_loiter=False,
    )
    defaults.update(kwargs)
    return MAVLinkIO(**defaults)


# ---------------------------------------------------------------------------
# Auto-loiter property
# ---------------------------------------------------------------------------

class TestAutoLoiter:
    def test_default_off(self):
        m = _make_mavlink()
        assert m.auto_loiter is False

    def test_set_on(self):
        m = _make_mavlink()
        m.auto_loiter = True
        assert m.auto_loiter is True

    def test_command_loiter_skipped_when_disabled(self):
        m = _make_mavlink(auto_loiter=False)
        m._mav = MagicMock()
        # Should return without calling mode_mapping
        m.command_loiter()
        m._mav.mode_mapping.assert_not_called()

    def test_command_loiter_called_when_enabled(self):
        m = _make_mavlink(auto_loiter=True)
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"LOITER": 5}
        m.command_loiter()
        m._mav.set_mode_apm.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# GPS fix checks
# ---------------------------------------------------------------------------

class TestGPSFix:
    def test_no_fix(self):
        m = _make_mavlink(min_gps_fix=3)
        assert m.gps_fix_ok is False

    def test_fix_below_threshold(self):
        m = _make_mavlink(min_gps_fix=3)
        m._gps["fix"] = 2
        assert m.gps_fix_ok is False

    def test_fix_at_threshold(self):
        m = _make_mavlink(min_gps_fix=3)
        m._gps["fix"] = 3
        assert m.gps_fix_ok is True

    def test_get_lat_lon_no_fix(self):
        m = _make_mavlink()
        lat, lon, alt = m.get_lat_lon()
        assert lat is None

    def test_get_lat_lon_with_fix(self):
        m = _make_mavlink(min_gps_fix=3)
        m._gps.update({"fix": 3, "lat": 340000000, "lon": -1180000000, "alt": 100000})
        lat, lon, alt = m.get_lat_lon()
        assert abs(lat - 34.0) < 0.001
        assert abs(lon - (-118.0)) < 0.001
        assert abs(alt - 100.0) < 0.1


# ---------------------------------------------------------------------------
# set_mode public method
# ---------------------------------------------------------------------------

class TestSetMode:
    def test_no_connection(self):
        m = _make_mavlink()
        assert m.set_mode("LOITER") is False

    def test_mode_not_found(self):
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"AUTO": 3}
        assert m.set_mode("LOITER") is False

    def test_mode_found(self):
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"GUIDED": 4, "LOITER": 5}
        assert m.set_mode("GUIDED") is True
        m._mav.set_mode_apm.assert_called_once_with(4)


# ---------------------------------------------------------------------------
# Target position estimation
# ---------------------------------------------------------------------------

class TestEstimateTargetPosition:
    def test_no_gps_returns_none(self):
        m = _make_mavlink()
        assert m.estimate_target_position(0.0) is None

    def test_no_heading_returns_none(self):
        m = _make_mavlink(min_gps_fix=3)
        m._gps.update({"fix": 3, "lat": 340000000, "lon": -1180000000, "alt": 100000, "hdg": None})
        assert m.estimate_target_position(0.0) is None

    def test_valid_estimate(self):
        m = _make_mavlink(min_gps_fix=3)
        m._gps.update({
            "fix": 3, "lat": 340000000, "lon": -1180000000,
            "alt": 100000, "hdg": 9000,
        })  # 90 deg
        result = m.estimate_target_position(0.0, approach_distance_m=100.0)
        assert result is not None
        lat, lon = result
        # Heading 90 = east, so lon should increase, lat ~same
        assert lon > -118.0
        assert abs(lat - 34.0) < 0.01


# ---------------------------------------------------------------------------
# GUIDED command safety
# ---------------------------------------------------------------------------

class TestCommandGuided:
    def test_no_connection(self):
        m = _make_mavlink()
        assert m.command_guided_to(34.0, -118.0) is False

    def test_no_guided_mode(self):
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"AUTO": 3}  # No GUIDED
        assert m.command_guided_to(34.0, -118.0) is False

    @patch.dict("sys.modules", {"pymavlink": MagicMock(), "pymavlink.mavutil": MagicMock()})
    def test_success(self):
        m = _make_mavlink(min_gps_fix=3)
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"GUIDED": 4}
        m._gps.update({"fix": 3, "lat": 340000000, "lon": -1180000000, "alt": 100000})
        assert m.command_guided_to(34.001, -118.001) is True
        m._mav.set_mode_apm.assert_called_once_with(4)
        m._mav.mav.set_position_target_global_int_send.assert_called_once()
