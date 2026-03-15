"""Tests for RF search pattern generators."""

from __future__ import annotations

import math

from hydra_detect.rf.search import (
    generate_lawnmower,
    generate_spiral,
    offset_position,
)
from hydra_detect.autonomous import haversine_m


class TestLawnmower:
    def test_generates_waypoints(self):
        wps = generate_lawnmower(34.05, -118.25, width_m=100, height_m=100, spacing_m=20)
        assert len(wps) > 0
        # Each waypoint is (lat, lon, alt)
        for wp in wps:
            assert len(wp) == 3

    def test_even_number_of_waypoints(self):
        wps = generate_lawnmower(34.05, -118.25, width_m=100, height_m=100, spacing_m=25)
        # Each leg produces 2 waypoints (start + end)
        assert len(wps) % 2 == 0

    def test_covers_area(self):
        wps = generate_lawnmower(34.05, -118.25, width_m=100, height_m=100, spacing_m=20)
        lats = [wp[0] for wp in wps]
        lons = [wp[1] for wp in wps]
        # Should span roughly 100m in each direction
        lat_span = (max(lats) - min(lats)) * 111320  # approx metres
        lon_span = (max(lons) - min(lons)) * 111320 * math.cos(math.radians(34.05))
        assert lat_span > 80  # allow some tolerance
        assert lon_span > 80

    def test_altitude_set(self):
        wps = generate_lawnmower(34.05, -118.25, alt=25.0)
        for wp in wps:
            assert wp[2] == 25.0

    def test_small_area(self):
        wps = generate_lawnmower(34.05, -118.25, width_m=10, height_m=10, spacing_m=5)
        assert len(wps) >= 4


class TestSpiral:
    def test_generates_waypoints(self):
        wps = generate_spiral(34.05, -118.25, max_radius_m=50, spacing_m=10)
        assert len(wps) > 1

    def test_starts_at_center(self):
        wps = generate_spiral(34.05, -118.25)
        assert abs(wps[0][0] - 34.05) < 1e-8
        assert abs(wps[0][1] - (-118.25)) < 1e-8

    def test_expands_outward(self):
        center_lat, center_lon = 34.05, -118.25
        wps = generate_spiral(center_lat, center_lon, max_radius_m=50, spacing_m=10)
        # Distance from center should generally increase
        dists = [haversine_m(center_lat, center_lon, wp[0], wp[1]) for wp in wps]
        # Last point should be further than the second point
        assert dists[-1] > dists[1]

    def test_respects_max_radius(self):
        center_lat, center_lon = 34.05, -118.25
        wps = generate_spiral(center_lat, center_lon, max_radius_m=50, spacing_m=10)
        for wp in wps:
            d = haversine_m(center_lat, center_lon, wp[0], wp[1])
            assert d < 55  # small tolerance

    def test_altitude_set(self):
        wps = generate_spiral(34.05, -118.25, alt=30.0)
        for wp in wps:
            assert wp[2] == 30.0


class TestOffsetPosition:
    def test_north(self):
        lat, lon = offset_position(34.0, -118.0, 0.0, 100.0)
        assert lat > 34.0  # moved north
        assert abs(lon - (-118.0)) < 1e-6  # didn't move east/west

    def test_east(self):
        lat, lon = offset_position(34.0, -118.0, 90.0, 100.0)
        assert lon > -118.0  # moved east
        assert abs(lat - 34.0) < 0.001  # roughly same latitude

    def test_south(self):
        lat, lon = offset_position(34.0, -118.0, 180.0, 100.0)
        assert lat < 34.0  # moved south

    def test_distance_accuracy(self):
        lat2, lon2 = offset_position(34.0, -118.0, 45.0, 1000.0)
        d = haversine_m(34.0, -118.0, lat2, lon2)
        assert abs(d - 1000.0) < 1.0  # within 1m for 1km

    def test_zero_distance(self):
        lat, lon = offset_position(34.0, -118.0, 0.0, 0.0)
        assert abs(lat - 34.0) < 1e-10
        assert abs(lon - (-118.0)) < 1e-10
