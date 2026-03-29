"""Tests for RF hunt geofence clipping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.autonomous import AutonomousController, haversine_m
from hydra_detect.rf.hunt import HuntState, RFHuntController


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink(*, lat=34.05, lon=-118.25, alt=15.0):
    """Build a mock MAVLinkIO."""
    mav = MagicMock()
    mav.get_lat_lon.return_value = (lat, lon, alt)
    mav.get_position_string.return_value = f"{lat:.5f},{lon:.5f}"
    mav.command_guided_to.return_value = True
    mav.send_statustext = MagicMock()
    mav.connected = True
    return mav


def _make_controller(mav=None, **overrides):
    """Build an RFHuntController with test defaults."""
    if mav is None:
        mav = _make_mavlink()
    defaults = dict(
        mode="wifi",
        target_bssid="AA:BB:CC:DD:EE:FF",
        kismet_host="http://localhost:2501",
        search_area_m=50.0,
        search_spacing_m=10.0,
        search_alt_m=15.0,
        rssi_threshold_dbm=-80.0,
        rssi_converge_dbm=-40.0,
        poll_interval_sec=0.01,
        arrival_tolerance_m=3.0,
        gps_required=True,
    )
    defaults.update(overrides)
    return RFHuntController(mav, **defaults)


def _make_autonomous(*, lat=34.05, lon=-118.25, radius_m=500.0):
    """Build an AutonomousController with a circular geofence."""
    return AutonomousController(
        enabled=True,
        geofence_lat=lat,
        geofence_lon=lon,
        geofence_radius_m=radius_m,
    )


# ---------------------------------------------------------------------------
# clip_to_geofence — circular
# ---------------------------------------------------------------------------

class TestClipToGeofenceCircular:
    def test_point_inside_returns_unchanged(self):
        ctrl = _make_autonomous(lat=34.05, lon=-118.25, radius_m=1000)
        result = ctrl.clip_to_geofence(34.05, -118.25)
        assert result == (34.05, -118.25)

    def test_point_outside_clipped_to_boundary(self):
        ctrl = _make_autonomous(lat=34.05, lon=-118.25, radius_m=100)
        # Point ~1 km away (well outside 100 m geofence)
        clipped_lat, clipped_lon = ctrl.clip_to_geofence(34.06, -118.25)

        # The clipped point should be inside (or on boundary of) the geofence
        dist = haversine_m(clipped_lat, clipped_lon, 34.05, -118.25)
        assert dist <= 100.0 + 5.0  # small tolerance for floating point

        # The clipped point should be closer to 34.06 than the centre is
        # (i.e., it's on the boundary in the direction of the original point)
        dist_to_original = haversine_m(clipped_lat, clipped_lon, 34.06, -118.25)
        dist_center_to_original = haversine_m(34.05, -118.25, 34.06, -118.25)
        assert dist_to_original < dist_center_to_original

    def test_point_at_center_returns_unchanged(self):
        ctrl = _make_autonomous(lat=34.05, lon=-118.25, radius_m=100)
        # Point at the exact center — inside, so returned unchanged
        result = ctrl.clip_to_geofence(34.05, -118.25)
        assert result == (34.05, -118.25)


# ---------------------------------------------------------------------------
# clip_to_geofence — polygon
# ---------------------------------------------------------------------------

class TestClipToGeofencePolygon:
    def test_point_inside_polygon_unchanged(self):
        polygon = [(34.0, -118.3), (34.0, -118.2), (34.1, -118.2), (34.1, -118.3)]
        ctrl = AutonomousController(
            enabled=True,
            geofence_polygon=polygon,
        )
        result = ctrl.clip_to_geofence(34.05, -118.25)
        assert result == (34.05, -118.25)

    def test_point_outside_polygon_clipped_inside(self):
        polygon = [(34.0, -118.3), (34.0, -118.2), (34.1, -118.2), (34.1, -118.3)]
        ctrl = AutonomousController(
            enabled=True,
            geofence_polygon=polygon,
        )
        # Point well outside the polygon
        clipped_lat, clipped_lon = ctrl.clip_to_geofence(35.0, -118.25)

        # Clipped point should be inside the polygon
        assert ctrl.check_geofence(clipped_lat, clipped_lon) is True


# ---------------------------------------------------------------------------
# Geofence waypoint integration in RF hunt
# ---------------------------------------------------------------------------

class TestRFHuntGeofenceWaypoint:
    def test_waypoint_inside_geofence_passes_through(self):
        """Waypoint inside geofence is sent directly."""
        mav = _make_mavlink()
        check_fn = MagicMock(return_value=True)
        clip_fn = MagicMock()

        ctrl = _make_controller(
            mav=mav,
            geofence_check=check_fn,
            geofence_clip=clip_fn,
        )

        result = ctrl._geofence_waypoint(34.05, -118.25, 15.0)

        assert result is True
        mav.command_guided_to.assert_called_once_with(34.05, -118.25, 15.0)
        clip_fn.assert_not_called()
        assert ctrl._consecutive_clips == 0

    def test_waypoint_outside_geofence_gets_clipped(self):
        """Waypoint outside geofence is clipped and then sent."""
        mav = _make_mavlink()
        check_fn = MagicMock(return_value=False)
        clip_fn = MagicMock(return_value=(34.051, -118.251))

        ctrl = _make_controller(
            mav=mav,
            geofence_check=check_fn,
            geofence_clip=clip_fn,
        )

        result = ctrl._geofence_waypoint(34.06, -118.25, 15.0)

        assert result is True
        clip_fn.assert_called_once_with(34.06, -118.25)
        mav.command_guided_to.assert_called_once_with(34.051, -118.251, 15.0)
        assert ctrl._consecutive_clips == 1

    def test_waypoint_outside_no_clip_fn_skipped(self):
        """Waypoint outside geofence with no clip callback is skipped."""
        mav = _make_mavlink()
        check_fn = MagicMock(return_value=False)

        ctrl = _make_controller(
            mav=mav,
            geofence_check=check_fn,
            geofence_clip=None,
        )

        result = ctrl._geofence_waypoint(34.06, -118.25, 15.0)

        assert result is False
        mav.command_guided_to.assert_not_called()

    def test_three_consecutive_clips_triggers_converged(self):
        """3 consecutive clips triggers CONVERGED state."""
        mav = _make_mavlink()
        check_fn = MagicMock(return_value=False)
        clip_fn = MagicMock(return_value=(34.051, -118.251))

        ctrl = _make_controller(
            mav=mav,
            geofence_check=check_fn,
            geofence_clip=clip_fn,
        )

        # First two clips succeed
        assert ctrl._geofence_waypoint(34.06, -118.25, 15.0) is True
        assert ctrl._consecutive_clips == 1
        assert ctrl._geofence_waypoint(34.07, -118.25, 15.0) is True
        assert ctrl._consecutive_clips == 2

        # Third clip triggers CONVERGED
        assert ctrl._geofence_waypoint(34.08, -118.25, 15.0) is False
        assert ctrl._consecutive_clips == 3
        assert ctrl.state == HuntState.CONVERGED
        mav.send_statustext.assert_called_with(
            "RF: SIGNAL BEYOND GEOFENCE", severity=4,
        )

    def test_consecutive_clip_counter_resets_on_valid_waypoint(self):
        """Counter resets when a waypoint is inside the geofence."""
        mav = _make_mavlink()
        # First call: outside, second call: inside
        check_fn = MagicMock(side_effect=[False, True])
        clip_fn = MagicMock(return_value=(34.051, -118.251))

        ctrl = _make_controller(
            mav=mav,
            geofence_check=check_fn,
            geofence_clip=clip_fn,
        )

        # First waypoint: outside -> clipped
        ctrl._geofence_waypoint(34.06, -118.25, 15.0)
        assert ctrl._consecutive_clips == 1

        # Second waypoint: inside -> counter resets
        ctrl._geofence_waypoint(34.05, -118.25, 15.0)
        assert ctrl._consecutive_clips == 0

    def test_no_geofence_check_passes_through(self):
        """Without geofence check, waypoints pass through directly."""
        mav = _make_mavlink()
        ctrl = _make_controller(mav=mav)

        assert ctrl._check_geofence is None
        result = ctrl._geofence_waypoint(34.06, -118.25, 15.0)

        assert result is True
        mav.command_guided_to.assert_called_once_with(34.06, -118.25, 15.0)


class TestRFHuntSearchWithGeofence:
    """Test that _do_search uses geofence clipping on waypoints."""

    def test_search_first_waypoint_geofenced(self):
        """First search waypoint goes through geofence check."""
        mav = _make_mavlink(lat=34.05, lon=-118.25)
        check_fn = MagicMock(return_value=True)
        ctrl = _make_controller(
            mav=mav,
            geofence_check=check_fn,
            geofence_clip=None,
        )
        ctrl._set_state(HuntState.SEARCHING)
        ctrl._waypoints = [(34.06, -118.24, 15.0), (34.07, -118.23, 15.0)]
        ctrl._wp_index = 0
        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = None

        ctrl._do_search()

        # Should have checked geofence for the first waypoint
        check_fn.assert_called_once_with(34.06, -118.24)


class TestRFHuntHomingWithGeofence:
    """Test that _do_homing uses geofence clipping on gradient steps."""

    def test_homing_gradient_step_geofenced(self):
        """Gradient step waypoint goes through geofence check."""
        mav = _make_mavlink()
        check_fn = MagicMock(return_value=True)
        ctrl = _make_controller(
            mav=mav,
            geofence_check=check_fn,
            geofence_clip=None,
            rssi_converge_dbm=-40.0,
        )
        ctrl._set_state(HuntState.HOMING)
        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = -60.0  # above threshold, below converge

        ctrl._do_homing()

        # Geofence check should have been called for the gradient waypoint
        assert check_fn.called
