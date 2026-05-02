"""Tests for the velocity-based visual servoing guidance controller."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from hydra_detect.guidance import (
    GuidanceConfig,
    GuidanceController,
    VelocityCommand,
    _clamp,
    _deadzone,
)
from hydra_detect.approach import ApproachConfig, ApproachController, ApproachMode


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------

class TestDeadzone:
    def test_below_threshold_returns_zero(self):
        assert _deadzone(0.03, 0.05) == 0.0
        assert _deadzone(-0.03, 0.05) == 0.0

    def test_at_threshold_passes_through(self):
        # Deadzone uses strict less-than, so exactly at threshold passes
        assert _deadzone(0.05, 0.05) == 0.05

    def test_above_threshold_returns_value(self):
        assert _deadzone(0.1, 0.05) == 0.1
        assert _deadzone(-0.1, 0.05) == -0.1

    def test_zero_threshold_passes_all(self):
        assert _deadzone(0.001, 0.0) == 0.001


class TestClamp:
    def test_within_range(self):
        assert _clamp(5.0, 0.0, 10.0) == 5.0

    def test_below_range(self):
        assert _clamp(-5.0, 0.0, 10.0) == 0.0

    def test_above_range(self):
        assert _clamp(15.0, 0.0, 10.0) == 10.0

    def test_at_bounds(self):
        assert _clamp(0.0, 0.0, 10.0) == 0.0
        assert _clamp(10.0, 0.0, 10.0) == 10.0


# ------------------------------------------------------------------
# VelocityCommand defaults
# ------------------------------------------------------------------

class TestVelocityCommand:
    def test_defaults_are_zero(self):
        cmd = VelocityCommand()
        assert cmd.vx == 0.0
        assert cmd.vy == 0.0
        assert cmd.vz == 0.0
        assert cmd.yaw_rate == 0.0


# ------------------------------------------------------------------
# GuidanceController
# ------------------------------------------------------------------

class TestGuidanceControllerInactive:
    """Controller returns zero velocity when not active."""

    def test_update_before_start_returns_zero(self):
        gc = GuidanceController()
        cmd = gc.update(0.5, 0.3, 0.1)
        assert cmd.vx == 0.0
        assert cmd.vy == 0.0

    def test_update_after_stop_returns_zero(self):
        gc = GuidanceController()
        gc.start()
        gc.stop()
        cmd = gc.update(0.5, 0.3, 0.1)
        assert cmd.vx == 0.0


class TestGuidanceControllerActive:
    """Controller produces correct velocity outputs when active."""

    def setup_method(self):
        self.cfg = GuidanceConfig(
            fwd_gain=2.0,
            lat_gain=1.5,
            vert_gain=1.0,
            yaw_gain=30.0,
            max_fwd_speed=5.0,
            max_lat_speed=2.0,
            max_vert_speed=1.5,
            max_yaw_rate=45.0,
            deadzone=0.05,
            target_bbox_ratio=0.15,
            lost_track_timeout_s=2.0,
            min_altitude_m=5.0,
        )
        self.gc = GuidanceController(self.cfg)
        self.gc.start()

    def test_centred_target_no_lateral(self):
        """Target in centre: zero lateral/yaw, forward approach only."""
        # error_x=0, error_y=0, bbox_ratio small (far away)
        cmd = self.gc.update(0.0, 0.0, 0.01)
        assert cmd.vy == 0.0   # no lateral (within deadzone)
        assert cmd.yaw_rate == 0.0
        assert cmd.vx > 0.0   # should approach

    def test_target_right_produces_right_strafe(self):
        """Target at right of frame: positive lateral velocity."""
        cmd = self.gc.update(0.5, 0.0, 0.05)
        assert cmd.vy > 0.0   # strafe right
        assert cmd.yaw_rate > 0.0  # yaw right

    def test_target_left_produces_left_strafe(self):
        """Target at left of frame: negative lateral velocity."""
        cmd = self.gc.update(-0.5, 0.0, 0.05)
        assert cmd.vy < 0.0   # strafe left
        assert cmd.yaw_rate < 0.0  # yaw left

    def test_target_above_produces_climb(self):
        """Target above centre: negative vz (climb in NED)."""
        cmd = self.gc.update(0.0, -0.5, 0.05)
        assert cmd.vz < 0.0   # climb (negative in NED)

    def test_target_below_produces_descend(self):
        """Target below centre: positive vz (descend in NED)."""
        cmd = self.gc.update(0.0, 0.5, 0.05)
        assert cmd.vz > 0.0   # descend

    def test_large_bbox_slows_approach(self):
        """Target filling frame: vx should be near zero."""
        # bbox_ratio >= target_bbox_ratio → approach_ratio ≈ 0
        cmd = self.gc.update(0.0, 0.0, 0.20)
        assert cmd.vx == 0.0  # clamped to 0 (approach_ratio <= 0)

    def test_small_bbox_full_approach(self):
        """Target far away (small bbox): vx should be max."""
        # bbox_ratio near 0 → approach_ratio ≈ 1.0
        cmd = self.gc.update(0.0, 0.0, 0.001)
        expected_vx = min(self.cfg.fwd_gain * 1.0, self.cfg.max_fwd_speed)
        assert cmd.vx == pytest.approx(expected_vx, abs=0.3)

    def test_velocity_clamped_to_max(self):
        """Extreme errors should be clamped to configured max."""
        cmd = self.gc.update(1.0, 1.0, 0.001)
        assert cmd.vy <= self.cfg.max_lat_speed
        assert cmd.vz <= self.cfg.max_vert_speed
        assert cmd.yaw_rate <= self.cfg.max_yaw_rate
        assert cmd.vx <= self.cfg.max_fwd_speed

    def test_deadzone_filters_small_errors(self):
        """Errors below deadzone should produce zero lateral/yaw."""
        cmd = self.gc.update(0.02, 0.02, 0.05)
        # After EMA smoothing, if error is below deadzone, should be zero
        # First frame: smooth_ex = 0.4 * 0.02 = 0.008, still < 0.05
        assert cmd.vy == 0.0
        assert cmd.yaw_rate == 0.0


class TestGuidanceTrackLoss:
    """Track loss behaviour."""

    def setup_method(self):
        self.cfg = GuidanceConfig(lost_track_timeout_s=1.0)
        self.gc = GuidanceController(self.cfg)
        self.gc.start()

    def test_none_inputs_return_zero(self):
        """Passing None (track lost) returns zero velocity."""
        cmd = self.gc.update(None, None, None)
        assert cmd.vx == 0.0
        assert cmd.vy == 0.0

    def test_track_lost_property_before_timeout(self):
        """track_lost is False immediately after losing track."""
        self.gc.update(0.5, 0.0, 0.05)  # valid track
        self.gc.update(None, None, None)  # just lost
        assert not self.gc.track_lost

    def test_track_lost_property_after_timeout(self):
        """track_lost becomes True after timeout elapses."""
        self.gc.update(0.5, 0.0, 0.05)  # valid track
        # Simulate time passing beyond timeout
        self.gc._last_track_time = time.monotonic() - 2.0
        assert self.gc.track_lost

    def test_track_regained_resets_timer(self):
        """Regaining track after loss resets the timer."""
        self.gc.update(0.5, 0.0, 0.05)  # valid
        self.gc._last_track_time = time.monotonic() - 2.0  # simulate loss
        assert self.gc.track_lost
        self.gc.update(0.5, 0.0, 0.05)  # regained
        assert not self.gc.track_lost


class TestGuidanceEMASmoothing:
    """EMA smoothing reduces jitter."""

    def test_sudden_change_is_smoothed(self):
        # Predictor disabled — this test isolates EMA behaviour.
        cfg = GuidanceConfig(deadzone=0.0, predictor_enabled=False)
        gc = GuidanceController(cfg)
        gc.start()

        # First update: large error
        cmd1 = gc.update(1.0, 0.0, 0.05)
        # Second update: zero error — EMA should still have residual
        cmd2 = gc.update(0.0, 0.0, 0.05)
        # The lateral velocity should be less than first but not zero
        assert abs(cmd2.vy) < abs(cmd1.vy)
        assert abs(cmd2.vy) > 0.0  # EMA residual


class TestGuidanceStartStop:
    """Start/stop lifecycle."""

    def test_start_resets_smoothing(self):
        gc = GuidanceController()
        gc.start()
        gc.update(1.0, 0.0, 0.05)  # build up EMA
        gc.stop()
        gc.start()  # should reset
        # After restart, first frame with 0 error should give near-zero
        cmd = gc.update(0.0, 0.0, 0.05)
        # EMA reset: smooth_ex = 0.4 * 0 + 0.6 * 0 = 0
        assert cmd.vy == 0.0

    def test_active_property(self):
        gc = GuidanceController()
        assert not gc.active
        gc.start()
        assert gc.active
        gc.stop()
        assert not gc.active


# ------------------------------------------------------------------
# Forward predictor (alpha-beta tracker on smoothed bbox center)
# ------------------------------------------------------------------

class TestForwardPredictor:
    """Forward predictor compensates for camera + infer + MAVLink + ESC delay.

    The predictor is an alpha-beta tracker layered on top of the existing EMA.
    Output of update() is computed from a measurement projected forward by
    ``loop_delay_ms``, so the controller acts on where the target will be when
    the velocity command actually takes effect, not where it is now.
    """

    def _make_cfg(self, **kwargs):
        """Build a config with EMA disabled (smoothing alpha 1.0) by default."""
        defaults = dict(
            deadzone=0.0,
            smoothing=1.0,           # EMA passthrough so predictor sees raw input
            predictor_enabled=True,
            predictor_alpha=0.5,
            predictor_beta=0.1,
            loop_delay_ms=100.0,
        )
        defaults.update(kwargs)
        return GuidanceConfig(**defaults)

    def test_constant_velocity_lead(self):
        """After convergence on constant-velocity motion, predicted error leads
        measured error by velocity * loop_delay."""
        cfg = self._make_cfg()
        gc = GuidanceController(cfg)
        gc.start()

        dt = 0.02              # 50 Hz frame rate
        velocity_per_frame = 0.01  # error units per frame
        ex_final = 0.0
        for i in range(200):   # plenty of frames for the alpha-beta to converge
            ex_final = i * velocity_per_frame
            gc.update(ex_final, 0.0, 0.05, now_s=i * dt)

        pred_x, pred_y = gc.predicted_error
        # velocity in error units per second
        true_velocity = velocity_per_frame / dt   # 0.5 units/sec
        expected_lead = true_velocity * (cfg.loop_delay_ms / 1000.0)  # 0.05
        assert pred_x == pytest.approx(ex_final + expected_lead, abs=0.01)
        assert pred_y == pytest.approx(0.0, abs=0.01)

    def test_stationary_target_no_lead(self):
        """Predictor on a still target converges to truth with zero velocity."""
        cfg = self._make_cfg()
        gc = GuidanceController(cfg)
        gc.start()

        for i in range(100):
            gc.update(0.3, 0.0, 0.05, now_s=i * 0.02)

        pred_x, pred_y = gc.predicted_error
        pred_vx, pred_vy = gc.predicted_velocity
        assert pred_x == pytest.approx(0.3, abs=0.01)
        assert pred_y == pytest.approx(0.0, abs=0.01)
        assert pred_vx == pytest.approx(0.0, abs=0.05)
        assert pred_vy == pytest.approx(0.0, abs=0.05)

    def test_predictor_disabled_matches_legacy_behavior(self):
        """With predictor_enabled false, the controller reproduces the
        pre-predictor formula: gain * deadzone(EMA-smoothed error)."""
        cfg = GuidanceConfig(
            fwd_gain=2.0, lat_gain=1.5, vert_gain=1.0, yaw_gain=30.0,
            max_fwd_speed=5.0, max_lat_speed=2.0, max_vert_speed=1.5,
            max_yaw_rate=45.0,
            deadzone=0.05,
            smoothing=0.4,           # default EMA alpha
            target_bbox_ratio=0.15,
            lost_track_timeout_s=2.0,
            min_altitude_m=5.0,
            predictor_enabled=False,
        )
        gc = GuidanceController(cfg)
        gc.start()

        # Two frames of identical input. EMA at alpha 0.4:
        #   frame 1: smooth_ex equals 0.4 * 0.3 plus 0.6 * 0.0 equals 0.12
        #   frame 2: smooth_ex equals 0.4 * 0.3 plus 0.6 * 0.12 equals 0.192
        gc.update(0.3, 0.2, 0.05, now_s=0.0)
        cmd = gc.update(0.3, 0.2, 0.05, now_s=0.02)

        # vy equals lat_gain * deadzoned(smooth_ex)
        # 0.192 is above the 0.05 deadzone, so vy equals 1.5 * 0.192 equals 0.288
        assert cmd.vy == pytest.approx(0.288, abs=0.005)

    def test_predictor_disabled_lateral_exceeds_enabled_on_zero_velocity(self):
        """On a stationary target, predictor-on output should match predictor-off
        output (no lead to add). This guards against the predictor introducing
        spurious lead on still targets."""
        cfg_off = self._make_cfg(predictor_enabled=False)
        cfg_on = self._make_cfg(predictor_enabled=True)

        gc_off = GuidanceController(cfg_off)
        gc_on = GuidanceController(cfg_on)
        gc_off.start()
        gc_on.start()

        cmd_off = None
        cmd_on = None
        for i in range(50):
            cmd_off = gc_off.update(0.4, 0.0, 0.05, now_s=i * 0.02)
            cmd_on = gc_on.update(0.4, 0.0, 0.05, now_s=i * 0.02)

        # On a stationary target both controllers should produce the same vy
        # within filter-noise tolerance.
        assert cmd_on.vy == pytest.approx(cmd_off.vy, abs=0.05)

    def test_track_lost_resets_predictor_state(self):
        """A None-input frame must clear the velocity estimate so a re-acquired
        track does not inherit stale velocity."""
        cfg = self._make_cfg()
        gc = GuidanceController(cfg)
        gc.start()

        # Build up a non-zero velocity estimate.
        for i in range(50):
            gc.update(i * 0.01, 0.0, 0.05, now_s=i * 0.02)
        assert abs(gc.predicted_velocity[0]) > 0.1

        # Track lost.
        gc.update(None, None, None, now_s=1.0)
        assert gc.predicted_error is None
        assert gc.predicted_velocity == (0.0, 0.0)

    def test_predictor_extreme_input_does_not_break_clamps(self):
        """Aggressive predictor settings on a fast target must still produce
        clamped, finite velocity commands."""
        import math

        cfg = self._make_cfg(
            loop_delay_ms=500.0,
            predictor_alpha=0.9,
            predictor_beta=0.5,
            max_lat_speed=2.0,
            max_fwd_speed=5.0,
            max_vert_speed=1.5,
            max_yaw_rate=45.0,
        )
        gc = GuidanceController(cfg)
        gc.start()

        for i in range(20):
            gc.update(i * 0.5, 0.0, 0.05, now_s=i * 0.02)
        cmd = gc.update(10.0, 0.0, 0.05, now_s=0.42)

        for value in (cmd.vx, cmd.vy, cmd.vz, cmd.yaw_rate):
            assert math.isfinite(value)
        assert abs(cmd.vy) <= cfg.max_lat_speed
        assert cmd.vx <= cfg.max_fwd_speed
        assert abs(cmd.vz) <= cfg.max_vert_speed
        assert abs(cmd.yaw_rate) <= cfg.max_yaw_rate

    def test_first_frame_no_lead(self):
        """The first valid frame after start() initializes the predictor and
        emits no lead (no velocity history yet)."""
        cfg = self._make_cfg()
        gc = GuidanceController(cfg)
        gc.start()

        gc.update(0.3, 0.1, 0.05, now_s=0.0)
        pred_x, pred_y = gc.predicted_error
        assert pred_x == pytest.approx(0.3, abs=1e-6)
        assert pred_y == pytest.approx(0.1, abs=1e-6)


# ------------------------------------------------------------------
# Integration: ApproachController pixel-lock auto-abort chain
# ------------------------------------------------------------------

class TestPixelLockApproachIntegration:
    """Test the full track-loss → zero-velocity → timeout → abort chain."""

    def setup_method(self):
        self.mavlink = MagicMock()
        self.mavlink.get_vehicle_mode.return_value = "LOITER"
        self.mavlink.set_mode.return_value = True
        self.mavlink.send_velocity_ned.return_value = True
        self.mavlink.get_lat_lon.return_value = (35.0, -79.0, 30.0)

        self.cfg = ApproachConfig(
            guidance_cfg=GuidanceConfig(
                lost_track_timeout_s=0.5,
                min_altitude_m=5.0,
            ),
        )
        self.ctrl = ApproachController(self.mavlink, self.cfg)

    def _make_track(self, cx=320, cy=240, size=50):
        """Create a mock TrackedObject."""
        track = MagicMock()
        track.x1 = cx - size
        track.y1 = cy - size
        track.x2 = cx + size
        track.y2 = cy + size
        return track

    def test_pixel_lock_starts_and_sends_velocity(self):
        """Pixel-lock starts, sends velocity commands on update."""
        assert self.ctrl.start_pixel_lock(1)
        assert self.ctrl.mode == ApproachMode.PIXEL_LOCK
        assert self.ctrl.active

        self.ctrl.update(self._make_track(), 640, 480)
        self.mavlink.send_velocity_ned.assert_called()

    def test_pixel_lock_sends_zero_on_track_loss(self):
        """Track loss sends zero velocity (brake)."""
        self.ctrl.start_pixel_lock(1)
        self.ctrl.update(self._make_track(), 640, 480)

        # Lose the track
        self.ctrl.update(None, 640, 480)
        last_call = self.mavlink.send_velocity_ned.call_args
        assert last_call[0] == (0.0, 0.0, 0.0, 0.0)

    def test_pixel_lock_aborts_after_timeout(self):
        """After track-loss timeout, approach aborts to LOITER."""
        self.ctrl.start_pixel_lock(1)
        self.ctrl.update(self._make_track(), 640, 480)

        # Simulate timeout by backdating the guidance timer
        self.ctrl._guidance._last_track_time = time.monotonic() - 1.0

        # Next update with None should trigger abort
        self.ctrl.update(None, 640, 480)
        assert self.ctrl.mode == ApproachMode.IDLE
        self.mavlink.set_mode.assert_called_with("LOITER")

    def test_pixel_lock_abort_sends_zero_velocity(self):
        """Abort sends a final zero-velocity brake command."""
        self.ctrl.start_pixel_lock(1)
        self.ctrl.abort()
        # Should send zero velocity on abort
        self.mavlink.send_velocity_ned.assert_called_with(0, 0, 0, 0)

    def test_pixel_lock_min_altitude_clamps_descent(self):
        """Descent is clamped when at or below min altitude."""
        # Vehicle at min altitude
        self.mavlink.get_lat_lon.return_value = (35.0, -79.0, 5.0)
        self.ctrl.start_pixel_lock(1)

        # Target below centre → guidance wants positive vz (descend)
        track = self._make_track(cx=320, cy=400)  # below centre
        self.ctrl.update(track, 640, 480)

        # The vz sent should be 0 (clamped), not positive
        last_call = self.mavlink.send_velocity_ned.call_args
        vz_sent = last_call[0][2]
        assert vz_sent == 0.0

    def test_pixel_lock_allows_descent_above_min_altitude(self):
        """Descent is allowed when well above min altitude."""
        # Vehicle well above min altitude
        self.mavlink.get_lat_lon.return_value = (35.0, -79.0, 50.0)
        self.ctrl.start_pixel_lock(1)

        # Target well below centre
        track = self._make_track(cx=320, cy=400)
        self.ctrl.update(track, 640, 480)

        last_call = self.mavlink.send_velocity_ned.call_args
        vz_sent = last_call[0][2]
        assert vz_sent > 0.0  # descent allowed

    def test_guided_mode_failure_aborts_start(self):
        """GUIDED mode switch failure aborts pixel-lock start (safety fix)."""
        self.mavlink.set_mode.return_value = False
        result = self.ctrl.start_pixel_lock(1)
        assert result is False
        self.mavlink.set_mode.assert_called_with("GUIDED")
