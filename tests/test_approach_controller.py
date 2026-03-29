"""Tests for the approach controller."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from hydra_detect.approach import (
    ApproachConfig,
    ApproachController,
    ApproachMode,
)
from hydra_detect.tracker import TrackedObject


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink(*, mode="AUTO"):
    """Build a mock MAVLinkIO with configurable state."""
    mav = MagicMock()
    mav.get_vehicle_mode.return_value = mode
    mav.set_mode.return_value = True
    mav.estimate_target_position.return_value = (34.05001, -118.25001)
    mav.command_guided_to.return_value = True
    mav.command_do_change_speed.return_value = True
    mav.send_statustext.return_value = None
    return mav


def _make_track(
    track_id: int = 1,
    x1: float = 200.0,
    y1: float = 150.0,
    x2: float = 300.0,
    y2: float = 250.0,
    label: str = "person",
    confidence: float = 0.9,
) -> TrackedObject:
    """Build a TrackedObject for testing."""
    return TrackedObject(
        track_id=track_id,
        x1=x1, y1=y1, x2=x2, y2=y2,
        confidence=confidence,
        class_id=0,
        label=label,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStartFollow:
    """Test start_follow sets mode and track."""

    def test_start_follow_sets_mode(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)

        result = ctrl.start_follow(42)

        assert result is True
        assert ctrl.mode == ApproachMode.FOLLOW
        assert ctrl.active is True
        assert ctrl._target_track_id == 42

    def test_start_follow_saves_vehicle_mode(self):
        mav = _make_mavlink(mode="AUTO")
        ctrl = ApproachController(mav)

        ctrl.start_follow(1)

        assert ctrl._pre_approach_mode == "AUTO"
        mav.get_vehicle_mode.assert_called_once()

    def test_start_follow_resets_waypoint_counter(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)
        ctrl._waypoints_sent = 99

        ctrl.start_follow(1)

        assert ctrl._waypoints_sent == 0

    def test_start_follow_records_active_since(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)

        before = time.monotonic()
        ctrl.start_follow(1)

        assert ctrl._active_since is not None
        assert ctrl._active_since >= before


class TestCannotStartWhileActive:
    """Test can't start follow while already active."""

    def test_rejects_double_start(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)

        assert ctrl.start_follow(1) is True
        assert ctrl.start_follow(2) is False
        assert ctrl._target_track_id == 1


class TestAbort:
    """Test abort returns to IDLE and sends LOITER."""

    def test_abort_returns_to_idle(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)
        ctrl.start_follow(1)

        ctrl.abort()

        assert ctrl.mode == ApproachMode.IDLE
        assert ctrl.active is False
        assert ctrl._target_track_id is None

    def test_abort_sends_loiter(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)
        ctrl.start_follow(1)

        ctrl.abort()

        mav.set_mode.assert_called_with("LOITER")

    def test_abort_noop_when_idle(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)

        ctrl.abort()

        mav.set_mode.assert_not_called()


class TestUpdateWithTrack:
    """Test update with track sends waypoint."""

    def test_sends_waypoint_at_update_hz(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(update_hz=100.0)  # high Hz so first call triggers
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        track = _make_track(track_id=1, x1=200, y1=150, x2=300, y2=250)
        ctrl.update(track, 640, 480)

        mav.command_guided_to.assert_called_once()
        assert ctrl._waypoints_sent == 1

    def test_sends_speed_command(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(update_hz=100.0)
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        track = _make_track(track_id=1, x1=200, y1=150, x2=300, y2=250)
        ctrl.update(track, 640, 480)

        mav.command_do_change_speed.assert_called_once()


class TestUpdateWithoutTrack:
    """Test update without track holds position."""

    def test_no_waypoint_when_track_lost(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(update_hz=100.0)
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        ctrl.update(None, 640, 480)

        mav.command_guided_to.assert_not_called()
        mav.command_do_change_speed.assert_not_called()
        assert ctrl._waypoints_sent == 0


class TestSpeedScaling:
    """Test speed scales with bbox area."""

    def test_far_target_gets_max_speed(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(
            update_hz=100.0,
            follow_speed_max=5.0,
            speed_scale_far=1.0,
            speed_scale_near=0.3,
            near_threshold_px=0.4,
        )
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        # Small bbox = far target (~1% of frame)
        track = _make_track(track_id=1, x1=310, y1=230, x2=330, y2=250)
        ctrl.update(track, 640, 480)

        speed_arg = mav.command_do_change_speed.call_args[0][0]
        assert speed_arg > 4.0  # close to max speed

    def test_near_target_gets_slow_speed(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(
            update_hz=100.0,
            follow_speed_max=5.0,
            speed_scale_far=1.0,
            speed_scale_near=0.3,
            near_threshold_px=0.1,
        )
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        # bbox area ~30% of frame (above near_threshold but below 0.5 hold)
        # 400*360 / (640*480) = 144000/307200 = 0.468
        track = _make_track(track_id=1, x1=120, y1=60, x2=520, y2=420)
        ctrl.update(track, 640, 480)

        # This target is > near_threshold_px, so speed should be near scale
        speed_arg = mav.command_do_change_speed.call_args[0][0]
        assert speed_arg <= 5.0 * 0.3 + 0.01


class TestVeryCloseTarget:
    """Test very close target (large bbox) holds position."""

    def test_holds_when_bbox_over_half_frame(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(update_hz=100.0)
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        # bbox > 50% of frame area
        track = _make_track(track_id=1, x1=0, y1=0, x2=600, y2=430)
        ctrl.update(track, 640, 480)

        mav.command_guided_to.assert_not_called()
        mav.command_do_change_speed.assert_not_called()


class TestWaypointCounter:
    """Test waypoint counter increments."""

    def test_counter_increments_each_update(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(update_hz=100.0)
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        track = _make_track(track_id=1, x1=200, y1=150, x2=300, y2=250)

        ctrl.update(track, 640, 480)
        assert ctrl._waypoints_sent == 1

        ctrl._last_update = 0.0  # reset rate limiter
        ctrl.update(track, 640, 480)
        assert ctrl._waypoints_sent == 2


class TestGetStatus:
    """Test get_status returns correct dict."""

    def test_idle_status(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)

        status = ctrl.get_status()

        assert status["mode"] == "idle"
        assert status["active"] is False
        assert status["target_track_id"] is None
        assert status["method"] == "gps_waypoint"
        assert status["waypoints_sent"] == 0

    def test_follow_status(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)
        ctrl.start_follow(42)

        status = ctrl.get_status()

        assert status["mode"] == "follow"
        assert status["active"] is True
        assert status["target_track_id"] == 42
        assert status["active_since"] is not None

    def test_status_after_waypoints(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(update_hz=100.0)
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        track = _make_track(track_id=1, x1=200, y1=150, x2=300, y2=250)
        ctrl.update(track, 640, 480)

        status = ctrl.get_status()
        assert status["waypoints_sent"] == 1
        assert status["bbox_area"] > 0


class TestUpdateRateLimiting:
    """Test the update rate limiter."""

    def test_skips_when_too_soon(self):
        mav = _make_mavlink()
        cfg = ApproachConfig(update_hz=2.0)  # 0.5s interval
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        track = _make_track(track_id=1, x1=200, y1=150, x2=300, y2=250)
        ctrl.update(track, 640, 480)  # First update
        ctrl.update(track, 640, 480)  # Too soon, should skip

        assert mav.command_guided_to.call_count == 1

    def test_idle_skips_all(self):
        mav = _make_mavlink()
        ctrl = ApproachController(mav)

        track = _make_track(track_id=1)
        ctrl.update(track, 640, 480)

        mav.command_guided_to.assert_not_called()


class TestEstimateFailure:
    """Test behaviour when GPS position estimate fails."""

    def test_no_waypoint_when_estimate_returns_none(self):
        mav = _make_mavlink()
        mav.estimate_target_position.return_value = None
        cfg = ApproachConfig(update_hz=100.0)
        ctrl = ApproachController(mav, cfg)
        ctrl.start_follow(1)

        track = _make_track(track_id=1, x1=200, y1=150, x2=300, y2=250)
        ctrl.update(track, 640, 480)

        mav.command_guided_to.assert_not_called()
        assert ctrl._waypoints_sent == 0
