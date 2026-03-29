"""Tests for mission profile presets and dogleg RTL."""

from __future__ import annotations

import math
import threading
import time
from unittest.mock import MagicMock

import pytest

from hydra_detect.mission_profiles import (
    DEFAULT_PROFILES,
    MissionProfile,
    get_profile,
    get_profiles,
    get_vehicle_post_action,
)
from hydra_detect.dogleg_rtl import DoglegRTL, compute_dogleg_waypoint


# ---------------------------------------------------------------------------
# Mission profile presets
# ---------------------------------------------------------------------------

class TestDefaultProfiles:
    def test_recon_profile_exists(self):
        profiles = get_profiles()
        assert "recon" in profiles

    def test_delivery_profile_exists(self):
        profiles = get_profiles()
        assert "delivery" in profiles

    def test_strike_profile_exists(self):
        profiles = get_profiles()
        assert "strike" in profiles

    def test_all_three_defaults(self):
        profiles = get_profiles()
        assert len(profiles) == 3
        assert set(profiles.keys()) == {"recon", "delivery", "strike"}

    def test_profiles_are_mission_profile_instances(self):
        for name, profile in get_profiles().items():
            assert isinstance(profile, MissionProfile)

    def test_recon_behavior(self):
        p = get_profiles()["recon"]
        assert p.behavior == "follow"
        assert p.post_action == "SMART_RTL"

    def test_delivery_behavior(self):
        p = get_profiles()["delivery"]
        assert p.behavior == "drop"
        assert p.approach_method == "gps_waypoint"

    def test_strike_behavior(self):
        p = get_profiles()["strike"]
        assert p.behavior == "strike"
        assert p.post_action == "LOITER"


class TestGetProfile:
    def test_get_existing_profile(self):
        p = get_profile("recon")
        assert p is not None
        assert p.name == "recon"

    def test_get_profile_case_insensitive(self):
        p = get_profile("RECON")
        assert p is not None
        assert p.name == "recon"

    def test_get_nonexistent_returns_none(self):
        assert get_profile("nonexistent") is None

    def test_get_empty_string_returns_none(self):
        assert get_profile("") is None


class TestGetVehiclePostAction:
    def test_drone_strike_returns_loiter(self):
        p = get_profile("strike")
        assert get_vehicle_post_action(p, "drone") == "LOITER"

    def test_ugv_strike_returns_hold(self):
        p = get_profile("strike")
        assert get_vehicle_post_action(p, "ugv") == "HOLD"

    def test_usv_strike_returns_loiter(self):
        p = get_profile("strike")
        assert get_vehicle_post_action(p, "usv") == "LOITER"

    def test_dogleg_rtl_only_for_drone(self):
        # Create a profile with DOGLEG_RTL post-action
        p = MissionProfile(
            name="test", display_name="TEST", description="test",
            behavior="drop", approach_method="gps_waypoint",
            post_action="DOGLEG_RTL",
        )
        assert get_vehicle_post_action(p, "drone") == "DOGLEG_RTL"
        assert get_vehicle_post_action(p, "usv") == "SMART_RTL"
        assert get_vehicle_post_action(p, "ugv") == "SMART_RTL"

    def test_recon_post_action_same_for_all_vehicles(self):
        p = get_profile("recon")
        assert get_vehicle_post_action(p, "drone") == "SMART_RTL"
        assert get_vehicle_post_action(p, "usv") == "SMART_RTL"
        assert get_vehicle_post_action(p, "ugv") == "SMART_RTL"

    def test_empty_vehicle_type(self):
        p = get_profile("recon")
        assert get_vehicle_post_action(p, "") == "SMART_RTL"

    def test_none_vehicle_type_handled(self):
        """None vehicle type should not crash."""
        p = get_profile("strike")
        # vehicle_type=None should be handled gracefully
        result = get_vehicle_post_action(p, None)
        assert result == "LOITER"


# ---------------------------------------------------------------------------
# Dogleg waypoint computation
# ---------------------------------------------------------------------------

class TestComputeDoglegWaypoint:
    def test_perpendicular_offset(self):
        """Dogleg waypoint should be offset from the direct path."""
        # Two nearby points so the 200m offset is meaningful
        wp_lat, wp_lon = compute_dogleg_waypoint(
            34.05, -118.25,
            34.06, -118.24,
            offset_distance_m=200.0,
            offset_bearing="perpendicular",
        )
        # Waypoint should be near the midpoint but offset
        mid_lat = (34.05 + 34.06) / 2
        mid_lon = (-118.25 + -118.24) / 2
        # Verify offset from midpoint using haversine
        from hydra_detect.autonomous import haversine_m
        dist_from_mid = haversine_m(wp_lat, wp_lon, mid_lat, mid_lon)
        # Should be roughly 200m from midpoint
        assert dist_from_mid > 100

    def test_waypoint_different_from_direct_path(self):
        """Dogleg waypoint should NOT lie on the direct line."""
        current_lat, current_lon = 34.05, -118.25
        home_lat, home_lon = 34.10, -118.20

        wp_lat, wp_lon = compute_dogleg_waypoint(
            current_lat, current_lon,
            home_lat, home_lon,
            offset_distance_m=200.0,
        )

        # Midpoint of direct line
        mid_lat = (current_lat + home_lat) / 2
        mid_lon = (current_lon + home_lon) / 2

        # The waypoint should be displaced from midpoint
        from hydra_detect.autonomous import haversine_m
        dist_from_mid = haversine_m(wp_lat, wp_lon, mid_lat, mid_lon)
        # Should be roughly offset_distance_m from midpoint
        assert dist_from_mid > 100  # At least 100m away from midpoint

    def test_compass_bearing_offset(self):
        """A specific compass bearing should produce a deterministic offset."""
        wp1 = compute_dogleg_waypoint(34.05, -118.25, 34.10, -118.20,
                                      offset_distance_m=200.0, offset_bearing="90")
        wp2 = compute_dogleg_waypoint(34.05, -118.25, 34.10, -118.20,
                                      offset_distance_m=200.0, offset_bearing="270")
        # Opposite bearings should produce different waypoints (lat or lon)
        assert (wp1[0] != pytest.approx(wp2[0], abs=0.00001) or
                wp1[1] != pytest.approx(wp2[1], abs=0.00001))

    def test_zero_offset_returns_midpoint(self):
        """Zero offset distance should return approximately the midpoint."""
        wp_lat, wp_lon = compute_dogleg_waypoint(
            34.05, -118.25,
            34.10, -118.20,
            offset_distance_m=0.0,
        )
        mid_lat = (34.05 + 34.10) / 2
        mid_lon = (-118.25 + -118.20) / 2
        assert wp_lat == pytest.approx(mid_lat, abs=0.0001)
        assert wp_lon == pytest.approx(mid_lon, abs=0.0001)

    def test_same_position_does_not_crash(self):
        """Same current and home should not raise."""
        wp_lat, wp_lon = compute_dogleg_waypoint(
            34.05, -118.25, 34.05, -118.25,
            offset_distance_m=200.0,
        )
        # Result should be finite numbers
        assert math.isfinite(wp_lat)
        assert math.isfinite(wp_lon)


# ---------------------------------------------------------------------------
# DoglegRTL controller
# ---------------------------------------------------------------------------

class TestDoglegRTL:
    def test_execute_starts_background_thread(self):
        """execute() should start a background thread and return True."""
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.05, -118.25, 50.0)
        mav.command_guided_to.return_value = True

        rtl = DoglegRTL(
            mavlink=mav,
            home_lat=34.10,
            home_lon=-118.20,
            offset_distance_m=200.0,
        )

        # Count threads before
        threads_before = threading.active_count()
        result = rtl.execute()

        assert result is True
        # A new thread should have been spawned
        time.sleep(0.1)
        assert rtl.phase in ("climb", "offset", "home", "done")

    def test_execute_no_gps_returns_false(self):
        """execute() should return False when GPS is unavailable."""
        mav = MagicMock()
        mav.get_lat_lon.return_value = (None, None, None)

        rtl = DoglegRTL(
            mavlink=mav,
            home_lat=34.10,
            home_lon=-118.20,
        )

        result = rtl.execute()
        assert result is False
        assert rtl.phase == "idle"

    def test_initial_phase_is_idle(self):
        """Phase should be 'idle' before execute is called."""
        mav = MagicMock()
        rtl = DoglegRTL(mavlink=mav, home_lat=34.10, home_lon=-118.20)
        assert rtl.phase == "idle"

    def test_execute_calls_guided_to(self):
        """execute() should command the vehicle to the dogleg waypoint."""
        mav = MagicMock()
        mav.command_guided_to.return_value = True

        start = (34.05, -118.25)
        home = (34.10, -118.20)

        # Pre-compute the waypoint (same params DoglegRTL will use)
        wp_lat, wp_lon = compute_dogleg_waypoint(
            start[0], start[1], home[0], home[1],
            offset_distance_m=200.0,
        )

        # First call (in execute) returns start position;
        # all subsequent calls (in _run loop) return waypoint position
        # so the distance check passes on the first poll.
        call_count = {"n": 0}
        def _get_lat_lon():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (start[0], start[1], 50.0)
            return (wp_lat, wp_lon, 50.0)

        mav.get_lat_lon.side_effect = _get_lat_lon

        rtl = DoglegRTL(
            mavlink=mav,
            home_lat=home[0],
            home_lon=home[1],
            offset_distance_m=200.0,
            climb_altitude_m=0,  # skip climb phase for test speed
        )

        rtl.execute()
        time.sleep(2.0)  # Let the background thread run

        # Should have called command_guided_to at least once
        assert mav.command_guided_to.called
        # Should eventually call set_mode for the home phase
        assert mav.set_mode.called
        mav.set_mode.assert_called_with("SMART_RTL")
