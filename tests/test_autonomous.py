"""Tests for the autonomous strike controller."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from hydra_detect.autonomous import (
    AutonomousController,
    haversine_m,
    parse_polygon,
    point_in_polygon,
)
from hydra_detect.tracker import TrackedObject, TrackingResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink(*, lat=34.05, lon=-118.25, alt=10.0, mode="AUTO", fix=4):
    """Build a mock MAVLinkIO with configurable state."""
    mav = MagicMock()
    mav.get_lat_lon.return_value = (lat, lon, alt)
    mav.get_vehicle_mode.return_value = mode
    mav.get_position_string.return_value = f"{lat:.5f},{lon:.5f}"
    mav.gps_fix_ok = fix >= 3
    mav.get_gps.return_value = {"last_update": time.monotonic(), "fix": fix}
    mav.estimate_target_position.return_value = (lat + 0.0001, lon + 0.0001)
    mav.command_guided_to.return_value = True
    return mav


def _make_tracks(*specs) -> TrackingResult:
    """Build a TrackingResult from (track_id, label, confidence) tuples."""
    tracks = [
        TrackedObject(
            track_id=tid, x1=100, y1=100, x2=200, y2=200,
            confidence=conf, class_id=0, label=label,
        )
        for tid, label, conf in specs
    ]
    return TrackingResult(tracks=tracks, active_ids=len(tracks))


def _make_controller(**overrides) -> AutonomousController:
    """Build an AutonomousController with sensible test defaults.

    The production default mode is ``dryrun`` (safety default — fresh
    controller will not fire strike_cb). Existing tests here exercise the
    live strike path, so the helper promotes to ``live`` by default. Pass
    ``mode="dryrun"`` or ``mode="shadow"`` to override.
    """
    mode = overrides.pop("mode", "live")
    defaults = dict(
        enabled=True,
        geofence_lat=34.05,
        geofence_lon=-118.25,
        geofence_radius_m=500.0,
        min_confidence=0.80,
        min_track_frames=3,
        allowed_classes=["mine", "buoy", "kayak"],
        strike_cooldown_sec=1.0,
        allowed_vehicle_modes=["AUTO"],
        require_operator_lock=False,  # tests override; production default is True
    )
    defaults.update(overrides)
    ctrl = AutonomousController(**defaults)
    ctrl.set_mode(mode)
    return ctrl


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point(self):
        assert haversine_m(34.0, -118.0, 34.0, -118.0) == 0.0

    def test_known_distance(self):
        # ~111km per degree of latitude
        d = haversine_m(34.0, -118.0, 35.0, -118.0)
        assert 110_000 < d < 112_000

    def test_short_distance(self):
        # ~50m offset
        d = haversine_m(34.0, -118.0, 34.00045, -118.0)
        assert 40 < d < 60


# ---------------------------------------------------------------------------
# Point in polygon
# ---------------------------------------------------------------------------

class TestPointInPolygon:
    def test_inside_square(self):
        square = [(0, 0), (0, 10), (10, 10), (10, 0)]
        assert point_in_polygon(5, 5, square) is True

    def test_outside_square(self):
        square = [(0, 0), (0, 10), (10, 10), (10, 0)]
        assert point_in_polygon(15, 5, square) is False

    def test_triangle(self):
        tri = [(0, 0), (5, 10), (10, 0)]
        assert point_in_polygon(5, 3, tri) is True
        assert point_in_polygon(0, 10, tri) is False

    def test_insufficient_vertices(self):
        assert point_in_polygon(0, 0, [(0, 0), (1, 1)]) is False


# ---------------------------------------------------------------------------
# Polygon parsing
# ---------------------------------------------------------------------------

class TestParsePolygon:
    def test_valid(self):
        result = parse_polygon("34.05,-118.25;34.06,-118.24;34.05,-118.23")
        assert len(result) == 3
        assert result[0] == (34.05, -118.25)

    def test_whitespace(self):
        result = parse_polygon("  34.05 , -118.25 ; 34.06 , -118.24 ; 34.05 , -118.23  ")
        assert len(result) == 3

    def test_empty(self):
        assert parse_polygon("") == []

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_polygon("34.05;bad")


# ---------------------------------------------------------------------------
# Geofence checks
# ---------------------------------------------------------------------------

class TestGeofence:
    def test_circle_inside(self):
        ctrl = _make_controller(geofence_lat=34.05, geofence_lon=-118.25, geofence_radius_m=1000)
        assert ctrl.check_geofence(34.05, -118.25) is True
        assert ctrl.check_geofence(34.051, -118.251) is True

    def test_circle_outside(self):
        ctrl = _make_controller(geofence_lat=34.05, geofence_lon=-118.25, geofence_radius_m=100)
        # ~1km away
        assert ctrl.check_geofence(34.06, -118.25) is False

    def test_polygon_overrides_circle(self):
        polygon = [(34.0, -118.3), (34.0, -118.2), (34.1, -118.2), (34.1, -118.3)]
        ctrl = _make_controller(
            geofence_lat=0.0, geofence_lon=0.0, geofence_radius_m=1,
            geofence_polygon=polygon,
        )
        assert ctrl.check_geofence(34.05, -118.25) is True
        assert ctrl.check_geofence(35.0, -118.25) is False


# ---------------------------------------------------------------------------
# Full qualification — evaluate()
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_all_criteria_met(self):
        ctrl = _make_controller(min_track_frames=3)
        mav = _make_mavlink()
        lock_cb = MagicMock(return_value=True)
        strike_cb = MagicMock(return_value=True)
        tracks = _make_tracks((1, "mine", 0.92))

        # Need 3 consecutive frames
        for _ in range(3):
            ctrl.evaluate(tracks, mav, lock_cb, strike_cb)

        strike_cb.assert_called_once_with(1)
        lock_cb.assert_called_once_with(1, "strike")

    def test_disabled(self):
        ctrl = _make_controller(enabled=False)
        mav = _make_mavlink()
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_low_confidence(self):
        ctrl = _make_controller(min_confidence=0.90)
        mav = _make_mavlink()
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.70))  # Below threshold

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_wrong_class(self):
        ctrl = _make_controller(allowed_classes=["mine", "buoy"])
        mav = _make_mavlink()
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "person", 0.95))  # Not in whitelist

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_insufficient_frames(self):
        ctrl = _make_controller(min_track_frames=5)
        mav = _make_mavlink()
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        # Only 4 frames — not enough
        for _ in range(4):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_outside_geofence(self):
        ctrl = _make_controller(geofence_lat=34.05, geofence_lon=-118.25, geofence_radius_m=50)
        mav = _make_mavlink(lat=35.0, lon=-118.25)  # Far away
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_wrong_vehicle_mode(self):
        ctrl = _make_controller(allowed_vehicle_modes=["AUTO"])
        mav = _make_mavlink(mode="MANUAL")
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_no_mavlink(self):
        ctrl = _make_controller()
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        for _ in range(10):
            ctrl.evaluate(tracks, None, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_cooldown_enforced(self):
        ctrl = _make_controller(min_track_frames=1, strike_cooldown_sec=100.0)
        strike_cb = MagicMock(return_value=True)
        tracks = _make_tracks((1, "mine", 0.92))

        # Use deterministic time to avoid flaky results
        fake_time = [1000.0]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(time, "monotonic", lambda: fake_time[0])

            # Create mavlink mock INSIDE monkeypatch so GPS timestamp
            # uses the fake time (otherwise GPS age check fails).
            mav = _make_mavlink()

            # First strike succeeds at t=1000
            ctrl.evaluate(tracks, mav, MagicMock(return_value=True), strike_cb)
            assert strike_cb.call_count == 1

            # Second strike blocked by cooldown (only 10s later, need 100s)
            fake_time[0] = 1010.0
            tracks2 = _make_tracks((2, "buoy", 0.95))
            for _ in range(5):
                ctrl.evaluate(tracks2, mav, MagicMock(return_value=True), strike_cb)

            assert strike_cb.call_count == 1  # Still just the first

    def test_no_gps_fix(self):
        ctrl = _make_controller()
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_simulated_gps_zero_last_update_proceeds(self):
        """GPS with last_update=0.0 (sim/static) should not block eval."""
        ctrl = _make_controller(min_track_frames=1)
        mav = _make_mavlink()
        # Simulate static/sim GPS: last_update=0.0
        mav.get_gps.return_value = {"last_update": 0.0, "fix": 4}
        strike_cb = MagicMock(return_value=True)
        tracks = _make_tracks((1, "mine", 0.92))

        ctrl.evaluate(tracks, mav, MagicMock(return_value=True), strike_cb)

        strike_cb.assert_called_once()

    def test_stale_real_gps_blocks_eval(self):
        """GPS with a real but stale last_update should block eval."""
        ctrl = _make_controller(min_track_frames=1)
        mav = _make_mavlink()
        # Simulate stale GPS: last_update was 5 seconds ago (> default 2.0)
        mav.get_gps.return_value = {
            "last_update": time.monotonic() - 5.0,
            "fix": 4,
        }
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        ctrl.evaluate(tracks, mav, MagicMock(return_value=True), strike_cb)

        strike_cb.assert_not_called()

    def test_unknown_vehicle_mode(self):
        ctrl = _make_controller()
        mav = _make_mavlink(mode=None)
        mav.get_vehicle_mode.return_value = None
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()

    def test_empty_allowed_classes_blocks_all_strikes(self):
        ctrl = _make_controller(allowed_classes=[])
        mav = _make_mavlink()
        strike_cb = MagicMock(return_value=True)
        tracks = _make_tracks((1, "anything", 0.92))

        for _ in range(3):
            ctrl.evaluate(tracks, mav, MagicMock(return_value=True), strike_cb)

        strike_cb.assert_not_called()

    def test_track_persistence_resets_on_disappearance(self):
        ctrl = _make_controller(min_track_frames=3)
        mav = _make_mavlink()
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))
        empty = _make_tracks()

        # 2 frames seen, then track disappears, then 2 more
        ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)
        ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)
        ctrl.evaluate(empty, mav, MagicMock(), strike_cb)  # lost
        ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)
        ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        # Should NOT have struck — counter reset after disappearance
        strike_cb.assert_not_called()

    def test_polygon_geofence_in_evaluate(self):
        polygon = [(34.0, -118.3), (34.0, -118.2), (34.1, -118.2), (34.1, -118.3)]
        ctrl = _make_controller(
            geofence_polygon=polygon,
            min_track_frames=1,
        )
        mav = _make_mavlink(lat=34.05, lon=-118.25)
        strike_cb = MagicMock(return_value=True)
        tracks = _make_tracks((1, "mine", 0.92))

        ctrl.evaluate(tracks, mav, MagicMock(return_value=True), strike_cb)
        strike_cb.assert_called_once()

    def test_invalid_geofence_center_zero(self):
        """A geofence at 0,0 with no polygon is treated as unconfigured."""
        ctrl = _make_controller(
            geofence_lat=0.0, geofence_lon=0.0, geofence_radius_m=100.0,
            geofence_polygon=None,
        )
        mav = _make_mavlink(lat=0.0, lon=0.0)
        strike_cb = MagicMock()
        tracks = _make_tracks((1, "mine", 0.92))

        for _ in range(10):
            ctrl.evaluate(tracks, mav, MagicMock(), strike_cb)

        strike_cb.assert_not_called()


# ---------------------------------------------------------------------------
# Runtime autonomy mode — safety-critical gate for strike_cb
# ---------------------------------------------------------------------------

class TestAutonomyModeGating:
    """``_mode`` must gate execution, not just dashboard display.

    Regression guard: before this gate was wired in, selecting DRYRUN on the
    Autonomy view still allowed the full strike path to run because
    ``evaluate()`` only checked ``self.enabled``. Operators would see a
    non-LIVE label while actual strikes were firing.
    """

    def _qualify(self, ctrl, mav, lock_cb, strike_cb, frames=3):
        tracks = _make_tracks((1, "mine", 0.92))
        for _ in range(frames):
            ctrl.evaluate(tracks, mav, lock_cb, strike_cb)

    def test_default_mode_is_dryrun(self):
        """Fresh controller must start in dryrun — safety default."""
        ctrl = AutonomousController(
            enabled=True,
            geofence_lat=34.05, geofence_lon=-118.25, geofence_radius_m=500.0,
            min_confidence=0.80, min_track_frames=3,
            allowed_classes=["mine"],
            allowed_vehicle_modes=["AUTO"],
            require_operator_lock=False,
        )
        assert ctrl.get_mode() == "dryrun"

    def test_dryrun_never_calls_strike_or_lock(self):
        ctrl = _make_controller(mode="dryrun", min_track_frames=3)
        mav = _make_mavlink()
        lock_cb = MagicMock(return_value=True)
        strike_cb = MagicMock(return_value=True)

        self._qualify(ctrl, mav, lock_cb, strike_cb)

        strike_cb.assert_not_called()
        lock_cb.assert_not_called()

    def test_dryrun_still_records_passthrough_decision(self):
        ctrl = _make_controller(mode="dryrun", min_track_frames=3)
        mav = _make_mavlink()
        self._qualify(ctrl, mav, MagicMock(return_value=True), MagicMock(return_value=True))

        snap = ctrl.get_dashboard_snapshot()
        assert snap["log"], "decision log should record dryrun passthrough"
        assert snap["log"][0]["action"] == "passthrough"
        assert "dryrun" in snap["log"][0]["reason"]

    def test_shadow_locks_but_does_not_strike(self):
        ctrl = _make_controller(mode="shadow", min_track_frames=3)
        mav = _make_mavlink()
        lock_cb = MagicMock(return_value=True)
        strike_cb = MagicMock(return_value=True)

        self._qualify(ctrl, mav, lock_cb, strike_cb)

        strike_cb.assert_not_called()
        # Shadow tags the lock reason so downstream can distinguish it from
        # a real strike lock.
        lock_cb.assert_called_once_with(1, "shadow")

    def test_shadow_records_passthrough_decision(self):
        ctrl = _make_controller(mode="shadow", min_track_frames=3)
        mav = _make_mavlink()
        self._qualify(ctrl, mav, MagicMock(return_value=True), MagicMock(return_value=True))

        snap = ctrl.get_dashboard_snapshot()
        assert snap["log"][0]["action"] == "passthrough"
        assert "shadow" in snap["log"][0]["reason"]

    def test_live_strikes_normally(self):
        ctrl = _make_controller(mode="live", min_track_frames=3)
        mav = _make_mavlink()
        lock_cb = MagicMock(return_value=True)
        strike_cb = MagicMock(return_value=True)

        self._qualify(ctrl, mav, lock_cb, strike_cb)

        strike_cb.assert_called_once_with(1)
        lock_cb.assert_called_once_with(1, "strike")

    def test_switching_live_to_dryrun_halts_strikes(self):
        """Operator flipping to dryrun mid-sortie must stop the strike path."""
        ctrl = _make_controller(mode="live", min_track_frames=1, strike_cooldown_sec=0.0)
        mav = _make_mavlink()
        lock_cb = MagicMock(return_value=True)
        strike_cb = MagicMock(return_value=True)
        tracks = _make_tracks((1, "mine", 0.92))

        ctrl.evaluate(tracks, mav, lock_cb, strike_cb)
        assert strike_cb.call_count == 1

        ctrl.set_mode("dryrun")
        # Give a different track id to avoid the cooldown skip path
        tracks2 = _make_tracks((2, "mine", 0.92))
        ctrl.evaluate(tracks2, mav, lock_cb, strike_cb)
        assert strike_cb.call_count == 1, "mode=dryrun must not fire strike_cb"

    def test_set_mode_rejects_unknown_value(self):
        ctrl = _make_controller(mode="live")
        with pytest.raises(ValueError):
            ctrl.set_mode("arm")
