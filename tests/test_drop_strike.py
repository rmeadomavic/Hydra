"""Tests for Drop and Strike approach modes."""

from __future__ import annotations

from unittest.mock import MagicMock

from hydra_detect.approach import ApproachConfig, ApproachController, ApproachMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink():
    mav = MagicMock()
    mav.get_vehicle_mode.return_value = "AUTO"
    mav.get_lat_lon.return_value = (34.05, -118.25, 10.0)
    mav.estimate_target_position.return_value = (34.051, -118.251)
    mav.command_guided_to.return_value = True
    mav.get_rc_channels.return_value = [1500] * 16
    mav.set_mode.return_value = True
    return mav


def _make_track(x1=100, y1=100, x2=200, y2=200):
    t = MagicMock()
    t.x1, t.y1, t.x2, t.y2 = x1, y1, x2, y2
    t.track_id = 1
    return t


def _make_controller(mavlink=None, **kw):
    mav = mavlink or _make_mavlink()
    defaults = dict(
        drop_channel=6,
        drop_pwm_release=1900,
        drop_pwm_hold=1100,
        drop_duration=0.5,
        drop_distance_m=3.0,
        arm_channel=7,
        arm_pwm_armed=1900,
        arm_pwm_safe=1100,
        hw_arm_channel=8,
    )
    defaults.update(kw)
    cfg = ApproachConfig(**defaults)
    return ApproachController(mav, cfg), mav


# ---------------------------------------------------------------------------
# Drop mode
# ---------------------------------------------------------------------------

class TestDropMode:
    def test_start_drop_sets_mode(self):
        ctrl, mav = _make_controller()
        assert ctrl.start_drop(1, 34.05, -118.25) is True
        assert ctrl.mode == ApproachMode.DROP
        mav.command_guided_to.assert_called_once_with(34.05, -118.25)

    def test_start_drop_while_active_fails(self):
        ctrl, _ = _make_controller()
        ctrl.start_follow(1)
        assert ctrl.start_drop(1, 34.0, -118.0) is False

    def test_drop_release_at_distance(self):
        ctrl, mav = _make_controller(drop_distance_m=5.0)
        ctrl.start_drop(1, 34.05, -118.25)

        # Vehicle is at target (0m distance)
        mav.get_lat_lon.return_value = (34.05, -118.25, 10.0)
        ctrl.update(None, 640, 480)

        mav.set_servo.assert_called_with(6, 1900)
        assert ctrl.drop_complete is True

    def test_drop_no_release_when_far(self):
        ctrl, mav = _make_controller(drop_distance_m=5.0)
        ctrl.start_drop(1, 34.06, -118.25)

        # Vehicle far from target (~1.1km)
        mav.get_lat_lon.return_value = (34.05, -118.25, 10.0)
        mav.set_servo.reset_mock()
        ctrl.update(None, 640, 480)

        # Should not have fired drop servo
        drop_calls = [c for c in mav.set_servo.call_args_list
                      if c[0][0] == 6 and c[0][1] == 1900]
        assert len(drop_calls) == 0

    def test_drop_complete_flag(self):
        ctrl, mav = _make_controller()
        ctrl.start_drop(1, 34.05, -118.25)
        assert ctrl.drop_complete is False

        mav.get_lat_lon.return_value = (34.05, -118.25, 10.0)
        ctrl.update(None, 640, 480)
        assert ctrl.drop_complete is True

    def test_drop_status_includes_target(self):
        ctrl, _ = _make_controller()
        ctrl.start_drop(1, 34.05, -118.25)
        status = ctrl.get_status()
        assert status["mode"] == "drop"
        assert status["target_lat"] == 34.05
        assert status["target_lon"] == -118.25
        assert status["drop_complete"] is False

    def test_drop_no_gps_skips_update(self):
        ctrl, mav = _make_controller()
        ctrl.start_drop(1, 34.05, -118.25)
        mav.get_lat_lon.return_value = (None, None, None)
        ctrl.update(None, 640, 480)
        assert ctrl.drop_complete is False


# ---------------------------------------------------------------------------
# Strike mode
# ---------------------------------------------------------------------------

class TestStrikeMode:
    def test_start_strike_arms_channel(self):
        ctrl, mav = _make_controller(arm_channel=7)
        ctrl.start_strike(1)
        assert ctrl.mode == ApproachMode.STRIKE
        mav.set_servo.assert_called_with(7, 1900)

    def test_start_strike_no_arm_channel(self):
        ctrl, mav = _make_controller(arm_channel=None)
        assert ctrl.start_strike(1) is True
        mav.set_servo.assert_not_called()

    def test_strike_continuous_waypoints(self):
        ctrl, mav = _make_controller(waypoint_interval=0.0, hw_arm_channel=None)
        ctrl.start_strike(1)
        track = _make_track()

        ctrl.update(track, 640, 480)
        assert mav.command_guided_to.called
        assert mav.command_do_change_speed.called

    def test_strike_target_lost_holds(self):
        ctrl, mav = _make_controller(hw_arm_channel=None)
        ctrl.start_strike(1)
        mav.command_guided_to.reset_mock()

        ctrl.update(None, 640, 480)
        mav.command_guided_to.assert_not_called()

    def test_strike_hardware_arm_not_engaged_aborts(self):
        ctrl, mav = _make_controller(hw_arm_channel=8, waypoint_interval=0.0)
        # RC channel 8 below 1500 = not armed
        mav.get_rc_channels.return_value = [
            1500, 1500, 1500, 1500,
            1500, 1500, 1500, 1000,
            1500, 1500, 1500, 1500,
            1500, 1500, 1500, 1500,
        ]
        ctrl.start_strike(1)
        track = _make_track()
        ctrl.update(track, 640, 480)
        # Should have aborted
        assert ctrl.mode == ApproachMode.IDLE

    def test_strike_hardware_arm_engaged_continues(self):
        ctrl, mav = _make_controller(hw_arm_channel=8, waypoint_interval=0.0)
        # RC channel 8 above 1500 = armed
        mav.get_rc_channels.return_value = [
            1500, 1500, 1500, 1500,
            1500, 1500, 1500, 1900,
            1500, 1500, 1500, 1500,
            1500, 1500, 1500, 1500,
        ]
        ctrl.start_strike(1)
        track = _make_track()
        ctrl.update(track, 640, 480)
        assert ctrl.mode == ApproachMode.STRIKE

    def test_strike_status_includes_arm_info(self):
        ctrl, mav = _make_controller(arm_channel=7, hw_arm_channel=8)
        ctrl.start_strike(1)
        status = ctrl.get_status()
        assert status["mode"] == "strike"
        assert status["software_arm"] is True
        assert "hardware_arm_status" in status


# ---------------------------------------------------------------------------
# Abort
# ---------------------------------------------------------------------------

class TestAbort:
    def test_abort_from_follow(self):
        ctrl, mav = _make_controller()
        # _make_mavlink returns "AUTO" as the vehicle mode captured on start
        ctrl.start_follow(1)
        ctrl.abort()
        assert ctrl.mode == ApproachMode.IDLE
        # Abort restores the pre-approach mode ("AUTO"), not the generic abort_mode
        mav.set_mode.assert_called_with("AUTO")

    def test_abort_from_drop_safes_channel(self):
        ctrl, mav = _make_controller(drop_channel=6)
        ctrl.start_drop(1, 34.0, -118.0)
        mav.set_servo.reset_mock()
        ctrl.abort()
        # Should safe drop channel
        safe_calls = [c for c in mav.set_servo.call_args_list
                      if c[0] == (6, 1100)]
        assert len(safe_calls) == 1

    def test_abort_from_strike_safes_arm(self):
        ctrl, mav = _make_controller(arm_channel=7)
        ctrl.start_strike(1)
        mav.set_servo.reset_mock()
        ctrl.abort()
        # Should safe arm channel
        safe_calls = [c for c in mav.set_servo.call_args_list
                      if c[0] == (7, 1100)]
        assert len(safe_calls) == 1

    def test_abort_when_idle_is_noop(self):
        ctrl, mav = _make_controller()
        ctrl.abort()
        mav.set_mode.assert_not_called()

    def test_abort_clears_track(self):
        ctrl, _ = _make_controller()
        ctrl.start_follow(1)
        assert ctrl.target_track_id == 1
        ctrl.abort()
        assert ctrl.target_track_id is None


# ---------------------------------------------------------------------------
# Hardware arm
# ---------------------------------------------------------------------------

class TestHardwareArm:
    def test_no_hw_channel_returns_none(self):
        ctrl, _ = _make_controller(hw_arm_channel=None)
        assert ctrl.get_hardware_arm_status() is None

    def test_hw_armed_above_1500(self):
        ctrl, mav = _make_controller(hw_arm_channel=8)
        mav.get_rc_channels.return_value = [1500] * 7 + [1800] + [1500] * 8
        assert ctrl.get_hardware_arm_status() is True

    def test_hw_safe_below_1500(self):
        ctrl, mav = _make_controller(hw_arm_channel=8)
        mav.get_rc_channels.return_value = [1500] * 7 + [1200] + [1500] * 8
        assert ctrl.get_hardware_arm_status() is False

    def test_hw_invalid_pwm_returns_none(self):
        ctrl, mav = _make_controller(hw_arm_channel=8)
        mav.get_rc_channels.return_value = [1500] * 7 + [0] + [1500] * 8
        assert ctrl.get_hardware_arm_status() is None

    def test_hw_exception_returns_none(self):
        ctrl, mav = _make_controller(hw_arm_channel=8)
        mav.get_rc_channels.side_effect = Exception("no RC data")
        assert ctrl.get_hardware_arm_status() is None
