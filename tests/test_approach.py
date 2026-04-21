"""Tests for Follow and Pixel-Lock approach modes.

Sister file to ``test_drop_strike.py`` which covers Drop and Strike.  Shares
the same ``_make_controller`` / ``_make_track`` helper pattern.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from hydra_detect.approach import ApproachConfig, ApproachController, ApproachMode
from hydra_detect.guidance import GuidanceConfig


# ---------------------------------------------------------------------------
# Helpers (copied from test_drop_strike.py — house style, no shared conftest)
# ---------------------------------------------------------------------------

def _make_mavlink():
    mav = MagicMock()
    mav.get_vehicle_mode.return_value = "AUTO"
    mav.get_lat_lon.return_value = (34.05, -118.25, 50.0)
    mav.estimate_target_position.return_value = (34.051, -118.251)
    mav.command_guided_to.return_value = True
    mav.command_do_change_speed.return_value = True
    mav.send_velocity_ned.return_value = True
    mav.get_rc_channels.return_value = [1500] * 16
    mav.set_mode.return_value = True
    return mav


def _make_track(x1=100, y1=100, x2=200, y2=200, track_id=1):
    t = MagicMock()
    t.x1, t.y1, t.x2, t.y2 = x1, y1, x2, y2
    t.track_id = track_id
    return t


def _make_controller(mavlink=None, guidance_cfg=None, **kw):
    mav = mavlink or _make_mavlink()
    defaults = dict(
        follow_speed_min=2.0,
        follow_speed_max=10.0,
        follow_distance_m=15.0,
        camera_hfov_deg=60.0,
        waypoint_interval=0.0,  # disable rate limit by default
        guidance_cfg=guidance_cfg or GuidanceConfig(min_altitude_m=5.0),
    )
    defaults.update(kw)
    cfg = ApproachConfig(**defaults)
    return ApproachController(mav, cfg), mav


# ---------------------------------------------------------------------------
# Follow — start
# ---------------------------------------------------------------------------

class TestFollowStart:
    def test_start_follow_sets_mode(self):
        ctrl, mav = _make_controller()
        assert ctrl.start_follow(42) is True
        assert ctrl.mode == ApproachMode.FOLLOW
        assert ctrl.target_track_id == 42

    def test_start_follow_captures_pre_approach_mode(self):
        ctrl, mav = _make_controller()
        mav.get_vehicle_mode.return_value = "GUIDED"
        ctrl.start_follow(1)
        ctrl.abort()
        # Abort should restore the captured mode, not the generic abort fallback
        mav.set_mode.assert_called_with("GUIDED")

    def test_start_follow_rejects_when_active(self):
        ctrl, _ = _make_controller()
        ctrl.start_follow(1)
        assert ctrl.start_follow(2) is False
        assert ctrl.target_track_id == 1

    def test_start_follow_initializes_counters(self):
        ctrl, _ = _make_controller()
        ctrl.start_follow(1)
        assert ctrl.get_status()["waypoints_sent"] == 0


# ---------------------------------------------------------------------------
# Follow — update
# ---------------------------------------------------------------------------

class TestFollowUpdate:
    def test_update_sends_waypoint_and_speed(self):
        ctrl, mav = _make_controller()
        ctrl.start_follow(1)
        mav.command_guided_to.reset_mock()
        mav.command_do_change_speed.reset_mock()

        track = _make_track(x1=310, y1=200, x2=330, y2=280)  # near centre
        ctrl.update(track, 640, 480)

        mav.estimate_target_position.assert_called_once()
        mav.command_guided_to.assert_called_once_with(34.051, -118.251)
        mav.command_do_change_speed.assert_called_once()

    def test_update_none_track_holds_position(self):
        ctrl, mav = _make_controller()
        ctrl.start_follow(1)
        mav.command_guided_to.reset_mock()
        ctrl.update(None, 640, 480)
        mav.command_guided_to.assert_not_called()

    def test_waypoint_interval_rate_limit(self):
        """Two rapid calls within the interval should send only one waypoint."""
        ctrl, mav = _make_controller(waypoint_interval=1.0)
        ctrl.start_follow(1)
        track = _make_track()
        ctrl.update(track, 640, 480)
        call_count_1 = mav.command_guided_to.call_count
        # Immediate second call — inside the 1s window
        ctrl.update(track, 640, 480)
        assert mav.command_guided_to.call_count == call_count_1

    def test_speed_scales_with_centering(self):
        """A centred target commands higher speed than an off-centre one."""
        ctrl, mav = _make_controller(
            follow_speed_min=2.0, follow_speed_max=10.0,
        )
        ctrl.start_follow(1)

        # Centred: speed should approach max
        centred = _make_track(x1=315, y1=200, x2=325, y2=280)
        ctrl.update(centred, 640, 480)
        centred_speed = mav.command_do_change_speed.call_args[0][0]

        # Reset rate-limit (waypoint_interval=0 already, but _last_wp_time=now;
        # force it to 0 so the next update passes the check)
        ctrl._last_wp_time = 0.0
        mav.command_do_change_speed.reset_mock()

        # Off-centre: speed should drop toward min
        off_centre = _make_track(x1=10, y1=200, x2=30, y2=280)
        ctrl.update(off_centre, 640, 480)
        off_centre_speed = mav.command_do_change_speed.call_args[0][0]

        assert centred_speed > off_centre_speed
        assert 2.0 <= off_centre_speed < 10.0
        assert 2.0 < centred_speed <= 10.0

    def test_estimate_target_returns_none_skips_waypoint(self):
        ctrl, mav = _make_controller()
        ctrl.start_follow(1)
        mav.estimate_target_position.return_value = None
        mav.command_guided_to.reset_mock()

        ctrl.update(_make_track(), 640, 480)
        mav.command_guided_to.assert_not_called()

    def test_guided_to_exception_swallowed(self):
        """Follow must not crash the hot loop if MAVLink raises."""
        ctrl, mav = _make_controller()
        ctrl.start_follow(1)
        mav.command_guided_to.side_effect = RuntimeError("link down")
        # Should not raise
        ctrl.update(_make_track(), 640, 480)
        assert ctrl.mode == ApproachMode.FOLLOW  # still active


# ---------------------------------------------------------------------------
# Pixel-Lock — start
# ---------------------------------------------------------------------------

class TestPixelLockStart:
    def test_start_pixel_lock_switches_to_guided(self):
        ctrl, mav = _make_controller()
        assert ctrl.start_pixel_lock(1) is True
        mav.set_mode.assert_any_call("GUIDED")
        assert ctrl.mode == ApproachMode.PIXEL_LOCK

    def test_guided_mode_failure_aborts_start(self):
        """Safety invariant: must not enter PIXEL_LOCK if GUIDED switch fails."""
        ctrl, mav = _make_controller()
        mav.set_mode.return_value = False
        assert ctrl.start_pixel_lock(1) is False
        assert ctrl.mode == ApproachMode.IDLE

    def test_guided_mode_exception_aborts_start(self):
        ctrl, mav = _make_controller()
        mav.set_mode.side_effect = RuntimeError("mavlink down")
        assert ctrl.start_pixel_lock(1) is False
        assert ctrl.mode == ApproachMode.IDLE

    def test_start_pixel_lock_rejects_when_active(self):
        ctrl, _ = _make_controller()
        ctrl.start_follow(1)
        assert ctrl.start_pixel_lock(2) is False


# ---------------------------------------------------------------------------
# Pixel-Lock — update
# ---------------------------------------------------------------------------

class TestPixelLockUpdate:
    def test_update_sends_velocity_ned(self):
        ctrl, mav = _make_controller()
        ctrl.start_pixel_lock(1)
        mav.send_velocity_ned.reset_mock()

        track = _make_track(x1=200, y1=150, x2=400, y2=300)
        ctrl.update(track, 640, 480)

        mav.send_velocity_ned.assert_called_once()
        args = mav.send_velocity_ned.call_args[0]
        assert len(args) == 4  # vx, vy, vz, yaw_rate

    def test_min_altitude_clamps_descent(self):
        """Regression: descending velocity must be zeroed when at/below min altitude."""
        guidance_cfg = GuidanceConfig(
            min_altitude_m=10.0,
            max_vert_speed=2.0,
            vert_gain=5.0,
        )
        ctrl, mav = _make_controller(guidance_cfg=guidance_cfg)
        # Vehicle at the minimum altitude
        mav.get_lat_lon.return_value = (34.05, -118.25, 10.0)
        ctrl.start_pixel_lock(1)
        mav.send_velocity_ned.reset_mock()

        # Target BELOW centre → ey > 0 → vz > 0 (descend in NED)
        below = _make_track(x1=300, y1=400, x2=340, y2=470)
        ctrl.update(below, 640, 480)

        vx, vy, vz, yaw = mav.send_velocity_ned.call_args[0]
        assert vz == 0.0, "Descent must be clamped at min altitude"

    def test_min_altitude_allows_climb(self):
        guidance_cfg = GuidanceConfig(
            min_altitude_m=10.0,
            max_vert_speed=2.0,
            vert_gain=5.0,
        )
        ctrl, mav = _make_controller(guidance_cfg=guidance_cfg)
        mav.get_lat_lon.return_value = (34.05, -118.25, 10.0)
        ctrl.start_pixel_lock(1)
        mav.send_velocity_ned.reset_mock()

        # Target ABOVE centre → ey < 0 → vz < 0 (climb) — should NOT be clamped
        above = _make_track(x1=300, y1=20, x2=340, y2=100)
        ctrl.update(above, 640, 480)
        vx, vy, vz, yaw = mav.send_velocity_ned.call_args[0]
        assert vz <= 0.0, "Climb should be preserved even near min altitude"

    def test_track_lost_beyond_timeout_aborts(self):
        guidance_cfg = GuidanceConfig(lost_track_timeout_s=0.01)
        ctrl, mav = _make_controller(guidance_cfg=guidance_cfg)
        ctrl.start_pixel_lock(1)
        # Simulate lost track past the timeout
        time.sleep(0.05)
        ctrl.update(None, 640, 480)
        assert ctrl.mode == ApproachMode.IDLE

    def test_update_none_track_within_timeout_sends_zero_vel(self):
        guidance_cfg = GuidanceConfig(lost_track_timeout_s=10.0)
        ctrl, mav = _make_controller(guidance_cfg=guidance_cfg)
        ctrl.start_pixel_lock(1)
        mav.send_velocity_ned.reset_mock()

        ctrl.update(None, 640, 480)
        # Still active, zero-velocity brake
        assert ctrl.mode == ApproachMode.PIXEL_LOCK
        mav.send_velocity_ned.assert_called_once_with(0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Abort from Follow / Pixel-Lock
# ---------------------------------------------------------------------------

class TestAbort:
    @pytest.mark.regression
    def test_regression_abort_restores_pre_approach_mode(self):
        """Abort must restore the captured pre-approach mode, not LOITER.

        Prior bug: ``abort()`` hard-coded ``set_mode("LOITER")`` instead of the
        vehicle's pre-approach mode. A student aborting mid-AUTO mission would
        land in LOITER unexpectedly.
        """
        ctrl, mav = _make_controller()
        mav.get_vehicle_mode.return_value = "AUTO"
        ctrl.start_follow(1)
        ctrl.abort()
        mav.set_mode.assert_called_with("AUTO")

    def test_abort_from_pixel_lock_brakes_and_restores(self):
        ctrl, mav = _make_controller()
        ctrl.start_pixel_lock(1)
        mav.send_velocity_ned.reset_mock()
        mav.set_mode.reset_mock()

        ctrl.abort()

        # Zero velocity brake
        mav.send_velocity_ned.assert_called_with(0, 0, 0, 0)
        # Mode restored
        mav.set_mode.assert_called()
        assert ctrl.mode == ApproachMode.IDLE

    def test_abort_from_pixel_lock_tolerates_brake_failure(self):
        ctrl, mav = _make_controller()
        ctrl.start_pixel_lock(1)
        mav.send_velocity_ned.side_effect = RuntimeError("link down")
        # Must not raise — abort is a safety path
        ctrl.abort()
        assert ctrl.mode == ApproachMode.IDLE
