"""Tests for the pixel-lock servo tracker."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from hydra_detect.servo_tracker import ServoTracker


def _make_tracker(**overrides) -> tuple[ServoTracker, MagicMock]:
    """Build a ServoTracker with a mock MAVLinkIO.

    Resets mock call history after construction so tests only see
    calls from the method under test, not __init__ safe-position calls.
    """
    mav = MagicMock()
    defaults = dict(
        pan_channel=1,
        pan_pwm_center=1500,
        pan_pwm_range=500,
        pan_invert=False,
        pan_dead_zone=0.05,
        pan_smoothing=1.0,
        strike_channel=2,
        strike_pwm_fire=1900,
        strike_pwm_safe=1100,
        strike_duration=0.5,
        replaces_yaw=False,
    )
    defaults.update(overrides)
    tracker = ServoTracker(mav, **defaults)
    mav.reset_mock()
    return tracker, mav


class TestPanMapping:
    def test_center_error_gives_center_pwm(self):
        tracker, mav = _make_tracker()
        tracker.update(0.0)
        mav.set_servo.assert_not_called()

    def test_full_right_gives_max_pwm(self):
        tracker, mav = _make_tracker()
        tracker.update(1.0)
        mav.set_servo.assert_called_with(1, 2000)

    def test_full_left_gives_min_pwm(self):
        tracker, mav = _make_tracker()
        tracker.update(-1.0)
        mav.set_servo.assert_called_with(1, 1000)

    def test_half_right(self):
        tracker, mav = _make_tracker()
        tracker.update(0.5)
        mav.set_servo.assert_called_with(1, 1750)

    def test_dead_zone_suppresses_small_errors(self):
        tracker, mav = _make_tracker(pan_dead_zone=0.1)
        tracker.update(0.05)
        mav.set_servo.assert_not_called()

    def test_dead_zone_boundary(self):
        tracker, mav = _make_tracker(pan_dead_zone=0.1)
        tracker.update(0.1)
        mav.set_servo.assert_called_once()

    def test_invert_flips_direction(self):
        tracker, mav = _make_tracker(pan_invert=True)
        tracker.update(0.5)
        mav.set_servo.assert_called_with(1, 1250)

    def test_clamping_extreme_error(self):
        tracker, mav = _make_tracker(pan_pwm_center=1500, pan_pwm_range=2000)
        tracker.update(1.0)
        mav.set_servo.assert_called_with(1, 2500)

    def test_clamping_negative_extreme(self):
        tracker, mav = _make_tracker(pan_pwm_center=1500, pan_pwm_range=2000)
        tracker.update(-1.0)
        mav.set_servo.assert_called_with(1, 500)


class TestPanSmoothing:
    def test_smoothing_dampens_step_change(self):
        tracker, mav = _make_tracker(pan_smoothing=0.3)
        tracker.update(1.0)
        mav.set_servo.assert_called_with(1, 1650)

    def test_smoothing_converges(self):
        tracker, mav = _make_tracker(pan_smoothing=0.5)
        for _ in range(50):
            tracker.update(1.0)
        last_call = mav.set_servo.call_args
        assert last_call == call(1, 2000)


class TestPanRateLimiting:
    def test_skip_if_pwm_unchanged(self):
        tracker, mav = _make_tracker()
        tracker.update(0.5)
        assert mav.set_servo.call_count == 1
        tracker.update(0.5)
        assert mav.set_servo.call_count == 1

    def test_sends_on_pwm_change(self):
        tracker, mav = _make_tracker()
        tracker.update(0.5)
        assert mav.set_servo.call_count == 1
        tracker.update(0.6)
        assert mav.set_servo.call_count == 2


# ---------------------------------------------------------------------------
# Strike servo
# ---------------------------------------------------------------------------

class TestStrikeServo:
    def test_fire_calls_set_servo_with_fire_pwm(self):
        tracker, mav = _make_tracker()
        tracker.fire_strike()
        mav.set_servo.assert_any_call(2, 1900)

    def test_fire_reverts_after_duration(self):
        tracker, mav = _make_tracker(strike_duration=0.05)
        tracker.fire_strike()
        import time
        time.sleep(0.15)
        mav.set_servo.assert_any_call(2, 1100)

    def test_reentrant_fire_ignored(self):
        tracker, mav = _make_tracker(strike_duration=1.0)
        tracker.fire_strike()
        fire_count = sum(1 for c in mav.set_servo.call_args_list if c == call(2, 1900))
        tracker.fire_strike()  # Should be ignored
        fire_count_after = sum(1 for c in mav.set_servo.call_args_list if c == call(2, 1900))
        assert fire_count_after == fire_count


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

class TestSafety:
    def test_safe_centers_pan(self):
        tracker, mav = _make_tracker()
        tracker.update(1.0)
        mav.reset_mock()
        tracker.safe()
        mav.set_servo.assert_any_call(1, 1500)

    def test_safe_sets_strike_to_safe_pwm(self):
        tracker, mav = _make_tracker()
        mav.reset_mock()
        tracker.safe()
        mav.set_servo.assert_any_call(2, 1100)

    def test_safe_resets_ema(self):
        tracker, mav = _make_tracker(pan_smoothing=0.3)
        tracker.update(1.0)
        tracker.safe()
        mav.reset_mock()
        tracker.update(1.0)
        mav.set_servo.assert_called_with(1, 1650)

    def test_init_sets_safe_positions(self):
        mav = MagicMock()
        ServoTracker(mav, pan_channel=1, strike_channel=2)
        mav.set_servo.assert_any_call(2, 1100)
        mav.set_servo.assert_any_call(1, 1500)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_initial_status(self):
        tracker, _ = _make_tracker()
        s = tracker.get_status()
        assert s["enabled"] is True
        assert s["tracking"] is False
        assert s["pan_pwm"] == 1500
        assert s["strike_active"] is False
        assert s["replaces_yaw"] is False

    def test_status_after_tracking(self):
        tracker, _ = _make_tracker()
        tracker.update(0.5)
        s = tracker.get_status()
        assert s["tracking"] is True
        assert s["pan_pwm"] == 1750
        assert s["error_x"] == 0.5

    def test_replaces_yaw_property(self):
        tracker, _ = _make_tracker(replaces_yaw=True)
        assert tracker.replaces_yaw is True
        s = tracker.get_status()
        assert s["replaces_yaw"] is True
