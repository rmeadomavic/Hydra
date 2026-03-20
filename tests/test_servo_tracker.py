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
