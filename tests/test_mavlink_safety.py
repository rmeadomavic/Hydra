"""Tests for MAVLink safety logic: GPS checks, mode setting, auto-loiter guard."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

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
# set_mode confirmation path (PR #240 R1-2 / R3-4 / issue #241)
# ---------------------------------------------------------------------------


class TestSetModeWithAck:
    def test_wait_for_ack_no_connection_returns_result(self):
        from hydra_detect.mavlink_io import SetModeResult
        m = _make_mavlink()
        r = m.set_mode("HOLD", wait_for_ack=True, ack_timeout_sec=0.1)
        assert isinstance(r, SetModeResult)
        assert r.accepted is False
        assert r.realized_mode is None
        assert r.ack_received is False

    def test_wait_for_ack_unknown_mode_returns_result(self):
        from hydra_detect.mavlink_io import SetModeResult
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"AUTO": 3}
        r = m.set_mode("HOLD", wait_for_ack=True, ack_timeout_sec=0.1)
        assert isinstance(r, SetModeResult)
        assert r.accepted is False

    def test_wait_for_ack_heartbeat_confirms(self):
        from hydra_detect.mavlink_io import SetModeResult
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"HOLD": 4}

        # Simulate a HEARTBEAT arriving immediately after set_mode_apm:
        # set the cached mode and signal the event the reader normally
        # would.
        def _send_and_signal(_mode_int):
            m._vehicle_mode = "HOLD"
            m._mode_change_event.set()
        m._mav.set_mode_apm.side_effect = _send_and_signal
        r = m.set_mode("HOLD", wait_for_ack=True, ack_timeout_sec=1.0)
        assert isinstance(r, SetModeResult)
        assert r.accepted is True
        assert r.realized_mode == "HOLD"
        assert r.ack_received is True
        assert r.timeout is False

    def test_wait_for_ack_timeout(self):
        from hydra_detect.mavlink_io import SetModeResult
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"HOLD": 4}
        # No HEARTBEAT signal — should time out.
        r = m.set_mode("HOLD", wait_for_ack=True, ack_timeout_sec=0.15)
        assert isinstance(r, SetModeResult)
        assert r.accepted is False
        assert r.ack_received is False
        assert r.timeout is True

    def test_wait_for_ack_other_mode_observed(self):
        """If the FC ends up in a DIFFERENT mode (e.g. autopilot-driven
        RTL beat us), ack_received is True but accepted is False."""
        from hydra_detect.mavlink_io import SetModeResult
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"HOLD": 4}
        # Simulate the autopilot winning the race — HEARTBEAT carries
        # a DIFFERENT mode than what we asked for.

        def _send_and_signal(_mode_int):
            m._vehicle_mode = "RTL"  # autopilot won the race
            m._mode_change_event.set()
        m._mav.set_mode_apm.side_effect = _send_and_signal
        r = m.set_mode("HOLD", wait_for_ack=True, ack_timeout_sec=1.0)
        assert isinstance(r, SetModeResult)
        assert r.accepted is False
        assert r.realized_mode == "RTL"
        assert r.ack_received is True

    def test_legacy_fire_and_forget_still_returns_bool(self):
        """Existing callers that don't pass wait_for_ack must still
        get bool (back-compat for approach.py / dogleg_rtl.py / web).
        """
        m = _make_mavlink()
        m._mav = MagicMock()
        m._mav.mode_mapping.return_value = {"HOLD": 4}
        assert m.set_mode("HOLD") is True
        assert m.set_mode("HOLD", wait_for_ack=False) is True


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


# ---------------------------------------------------------------------------
# RC channel freshness (#285)
# ---------------------------------------------------------------------------

def _rc_channels_msg(pwm: int = 1500):
    """Fake RC_CHANNELS message with all 18 channels at ``pwm``."""
    msg = MagicMock()
    for i in range(1, 19):
        setattr(msg, f"chan{i}_raw", pwm)
    return msg


class TestRCChannelFreshness:
    """The hardware-arm dead-man gate reads RC through this cache. Values
    alone cannot distinguish a live feed from a frozen cache, so every
    RC_CHANNELS receipt must stamp a monotonic last_update — mirroring the
    GPS/attitude convention. Issue #285."""

    def test_never_received_reads_zero(self):
        m = _make_mavlink()
        assert m.get_rc_channels() == []
        assert m.get_rc_channels_last_update() == 0.0

    def test_receipt_stamps_last_update(self):
        m = _make_mavlink()
        before = time.monotonic()
        m._handle_rc_channels(_rc_channels_msg(1700))
        after = time.monotonic()
        assert m.get_rc_channels() == [1700] * 18
        assert before <= m.get_rc_channels_last_update() <= after

    @pytest.mark.regression
    def test_stamp_advances_on_identical_frames(self):
        """Regression #285: an unchanged PWM frame must still refresh the
        stamp — freshness is about receipt, not value change."""
        m = _make_mavlink()
        m._handle_rc_channels(_rc_channels_msg(1900))
        first = m.get_rc_channels_last_update()
        assert first > 0.0
        m._handle_rc_channels(_rc_channels_msg(1900))
        assert m.get_rc_channels_last_update() >= first
