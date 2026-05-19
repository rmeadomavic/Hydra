"""Tests for the pixel-lock servo tracker."""

from __future__ import annotations

from unittest.mock import MagicMock, call

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


# ---------------------------------------------------------------------------
# Issue #234 R3-1 — disable_pan() for shared-battery graceful-stop
# ---------------------------------------------------------------------------


class TestDisablePan:
    """Shared-battery graceful-stop primitive (issue #234 R3-1).

    safe() centers pan PWM but does not prevent the per-frame update()
    from re-driving the channel. disable_pan() adds a gate so a pan
    sweep cannot be the load draining the pack after LOW transition.
    """

    def test_status_default_is_enabled(self):
        tracker, _ = _make_tracker()
        s = tracker.get_status()
        assert s["pan_disabled"] is False

    def test_disable_pan_centers_pan_pwm(self):
        tracker, mav = _make_tracker()
        # Drive the pan off-center first.
        tracker.update(0.8)
        mav.reset_mock()
        # Disable → center PWM should be emitted exactly once.
        result = tracker.disable_pan()
        assert result is True
        mav.set_servo.assert_called_once_with(1, 1500)

    def test_disable_pan_surfaces_in_status(self):
        tracker, _ = _make_tracker()
        tracker.disable_pan()
        s = tracker.get_status()
        assert s["pan_disabled"] is True

    def test_update_is_noop_when_disabled(self):
        """After disable_pan, update() must NOT emit servo commands."""
        tracker, mav = _make_tracker()
        tracker.disable_pan()
        mav.reset_mock()
        tracker.update(0.9)
        tracker.update(-0.6)
        tracker.update(1.0)
        mav.set_servo.assert_not_called()

    def test_update_records_error_when_disabled(self):
        """Status still reflects the latest error for the operator UI."""
        tracker, _ = _make_tracker()
        tracker.disable_pan()
        tracker.update(0.42)
        s = tracker.get_status()
        assert s["error_x"] == 0.42
        # Tracking flag must read False while disabled — UI can show
        # "pan tracker held" instead of "actively tracking."
        assert s["tracking"] is False

    def test_disable_pan_is_idempotent(self):
        tracker, mav = _make_tracker()
        first = tracker.disable_pan()
        mav.reset_mock()
        second = tracker.disable_pan()
        assert first is True
        assert second is True
        # Second call must NOT re-emit the set_servo (already at center).
        mav.set_servo.assert_not_called()

    def test_enable_pan_restores_update_path(self):
        tracker, mav = _make_tracker()
        tracker.disable_pan()
        tracker.enable_pan()
        mav.reset_mock()
        tracker.update(1.0)
        # Full-right error → PWM 2000 (range 500 + center 1500).
        mav.set_servo.assert_called_with(1, 2000)
        assert tracker.get_status()["pan_disabled"] is False

    def test_enable_pan_is_noop_when_already_enabled(self):
        tracker, mav = _make_tracker()
        mav.reset_mock()
        tracker.enable_pan()  # Already enabled.
        mav.set_servo.assert_not_called()

    def test_disable_pan_after_safe(self):
        """safe() + disable_pan() is the documented graceful-stop ladder."""
        tracker, mav = _make_tracker()
        tracker.update(0.7)
        tracker.safe()
        mav.reset_mock()
        tracker.disable_pan()
        # disable_pan re-centers (idempotent emit) — that's acceptable;
        # the load-bearing assertion is that subsequent update() emits nothing.
        tracker.update(0.5)
        # set_servo must have at most one call (from disable_pan), not
        # one-per-update from the frame-loop call.
        assert mav.set_servo.call_count <= 1

    def test_disable_pan_handles_set_servo_failure(self):
        """If set_servo raises, disable_pan returns False but state stays disabled.

        The pan-disabled gate is more important than the PWM emit — we
        prefer "no further updates" over "pretend everything is fine."
        """
        tracker, mav = _make_tracker()
        mav.set_servo.side_effect = RuntimeError("uart down")
        result = tracker.disable_pan()
        assert result is False
        assert tracker.get_status()["pan_disabled"] is True
        # Frame-loop calls still get gated.
        mav.set_servo.side_effect = None
        mav.reset_mock()
        tracker.update(0.4)
        mav.set_servo.assert_not_called()
