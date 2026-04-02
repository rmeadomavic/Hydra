"""Tests for waypoint export — QGC WPL 110 format generation."""

from __future__ import annotations

from hydra_detect.waypoint_export import (
    Waypoint,
    _haversine_m,
    deduplicate,
    format_wpl,
    tracks_to_waypoints,
)


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def test_haversine_same_point():
    """Two identical points should be 0 m apart."""
    assert _haversine_m(35.0, -117.0, 35.0, -117.0) == 0.0


def test_haversine_known_distance():
    """Verify approximate distance between two known points.

    Fort Bragg to Camp Lejeune is roughly 155 km.  Use a shorter, more
    precise pair for unit testing: two points ~111 km apart (1 degree lat).
    """
    d = _haversine_m(35.0, -117.0, 36.0, -117.0)
    # 1 degree of latitude is approximately 111 km
    assert 110_000 < d < 112_000


def test_haversine_short_distance():
    """Two points ~10 m apart should report roughly 10 m."""
    # ~0.0001 degrees lat ≈ 11 m
    d = _haversine_m(35.0, -117.0, 35.0001, -117.0)
    assert 10 < d < 12


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_deduplicate_keeps_highest_confidence():
    """Of two nearby waypoints, the one with higher confidence survives."""
    wps = [
        Waypoint(lat=35.0, lon=-117.0, alt=15.0, label="person", confidence=0.7),
        Waypoint(lat=35.00001, lon=-117.00001, alt=15.0, label="person", confidence=0.9),
    ]
    result = deduplicate(wps, radius_m=50.0)
    assert len(result) == 1
    assert result[0].confidence == 0.9


def test_deduplicate_keeps_distant_points():
    """Points far apart should not be merged."""
    wps = [
        Waypoint(lat=35.0, lon=-117.0, alt=15.0, label="person", confidence=0.8),
        Waypoint(lat=36.0, lon=-117.0, alt=15.0, label="car", confidence=0.7),
    ]
    result = deduplicate(wps, radius_m=10.0)
    assert len(result) == 2


def test_deduplicate_empty_list():
    """Empty input produces empty output."""
    assert deduplicate([], radius_m=10.0) == []


def test_deduplicate_single_waypoint():
    """Single waypoint passes through unchanged."""
    wps = [Waypoint(lat=35.0, lon=-117.0, alt=15.0, label="person", confidence=0.5)]
    result = deduplicate(wps, radius_m=10.0)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# WPL format
# ---------------------------------------------------------------------------

def test_format_wpl_header():
    """First line must be the QGC WPL 110 header."""
    wps = [Waypoint(lat=35.0, lon=-117.0, alt=15.0, label="person", confidence=0.9)]
    output = format_wpl(wps, home_lat=35.0, home_lon=-117.0)
    lines = output.strip().split("\n")
    assert lines[0] == "QGC WPL 110"


def test_format_wpl_home_line():
    """Line 1 (seq 0) should be the home waypoint with current=1, frame=0."""
    wps = [Waypoint(lat=35.0, lon=-117.0, alt=15.0, label="person", confidence=0.9)]
    output = format_wpl(wps, home_lat=34.5, home_lon=-118.0, home_alt=100.0)
    lines = output.strip().split("\n")
    fields = lines[1].split("\t")
    assert fields[0] == "0"   # seq
    assert fields[1] == "1"   # current
    assert fields[2] == "0"   # frame (absolute)
    assert fields[3] == "16"  # cmd NAV_WAYPOINT
    assert "34.50000000" in fields[8]  # home lat
    assert "-118.00000000" in fields[9]  # home lon
    assert "100.00" in fields[10]  # home alt


def test_format_wpl_waypoint_fields():
    """Waypoints use frame=3 (GLOBAL_RELATIVE_ALT), cmd=16, loiter in p1."""
    wps = [Waypoint(lat=35.1, lon=-117.2, alt=20.0, label="car", confidence=0.85)]
    output = format_wpl(wps, home_lat=35.0, home_lon=-117.0, loiter_sec=10.0)
    lines = output.strip().split("\n")
    fields = lines[2].split("\t")
    assert fields[0] == "1"   # seq
    assert fields[1] == "0"   # current
    assert fields[2] == "3"   # frame (relative alt)
    assert fields[3] == "16"  # cmd
    assert fields[4] == "10"  # loiter seconds (p1)
    assert "35.10000000" in fields[8]
    assert "-117.20000000" in fields[9]
    assert "20.00" in fields[10]


def test_format_wpl_max_99_waypoints():
    """Output must cap at 99 waypoints (plus home = 100 lines total + header)."""
    wps = [
        Waypoint(lat=35.0 + i * 0.01, lon=-117.0, alt=15.0,
                 label="person", confidence=0.5)
        for i in range(150)
    ]
    output = format_wpl(wps, home_lat=35.0, home_lon=-117.0)
    lines = output.strip().split("\n")
    # header + home + 99 waypoints = 101 lines
    assert len(lines) == 101


def test_format_wpl_trailing_newline():
    """Output must end with a newline."""
    wps = [Waypoint(lat=35.0, lon=-117.0, alt=15.0, label="person", confidence=0.9)]
    output = format_wpl(wps, home_lat=35.0, home_lon=-117.0)
    assert output.endswith("\n")


def test_format_wpl_empty():
    """No waypoints should produce only header + home."""
    output = format_wpl([], home_lat=35.0, home_lon=-117.0)
    lines = output.strip().split("\n")
    assert len(lines) == 2  # header + home


# ---------------------------------------------------------------------------
# tracks_to_waypoints
# ---------------------------------------------------------------------------

def test_tracks_to_waypoints_basic():
    """Valid GPS tracks convert to waypoints."""
    tracks = [
        {"label": "person", "confidence": 0.9, "lat": 35.1, "lon": -117.2},
        {"label": "car", "confidence": 0.7, "lat": 35.2, "lon": -117.3},
    ]
    wps = tracks_to_waypoints(tracks, alt_m=20.0)
    assert len(wps) == 2
    assert wps[0].lat == 35.1
    assert wps[0].alt == 20.0
    assert wps[1].label == "car"


def test_tracks_to_waypoints_filters_zero_gps():
    """Tracks with lat=0, lon=0 (no GPS fix) are excluded."""
    tracks = [
        {"label": "person", "confidence": 0.9, "lat": 0.0, "lon": 0.0},
        {"label": "car", "confidence": 0.7, "lat": 35.2, "lon": -117.3},
    ]
    wps = tracks_to_waypoints(tracks)
    assert len(wps) == 1
    assert wps[0].label == "car"


def test_tracks_to_waypoints_filters_none_gps():
    """Tracks with None lat/lon are excluded."""
    tracks = [
        {"label": "person", "confidence": 0.9, "lat": None, "lon": None},
        {"label": "car", "confidence": 0.7, "lat": 35.2, "lon": -117.3},
    ]
    wps = tracks_to_waypoints(tracks)
    assert len(wps) == 1


def test_tracks_to_waypoints_missing_gps_keys():
    """Tracks without lat/lon keys are excluded."""
    tracks = [
        {"label": "person", "confidence": 0.9},
        {"label": "car", "confidence": 0.7, "lat": 35.2, "lon": -117.3},
    ]
    wps = tracks_to_waypoints(tracks)
    assert len(wps) == 1


def test_tracks_to_waypoints_class_filter():
    """Class filter restricts which detections become waypoints."""
    tracks = [
        {"label": "person", "confidence": 0.9, "lat": 35.1, "lon": -117.2},
        {"label": "car", "confidence": 0.7, "lat": 35.2, "lon": -117.3},
        {"label": "dog", "confidence": 0.6, "lat": 35.3, "lon": -117.4},
    ]
    wps = tracks_to_waypoints(tracks, classes={"person", "dog"})
    assert len(wps) == 2
    labels = {w.label for w in wps}
    assert labels == {"person", "dog"}


def test_tracks_to_waypoints_alternate_keys():
    """Tracks using 'latitude'/'longitude' or 'class'/'conf' keys work."""
    tracks = [
        {"class": "boat", "conf": 0.8, "latitude": 35.5, "longitude": -117.5},
    ]
    wps = tracks_to_waypoints(tracks)
    assert len(wps) == 1
    assert wps[0].label == "boat"
    assert wps[0].confidence == 0.8


def test_tracks_to_waypoints_default_altitude():
    """Default altitude is 15 m when not specified."""
    tracks = [
        {"label": "person", "confidence": 0.9, "lat": 35.1, "lon": -117.2},
    ]
    wps = tracks_to_waypoints(tracks)
    assert wps[0].alt == 15.0


def test_tracks_to_waypoints_empty():
    """Empty input produces empty output."""
    assert tracks_to_waypoints([]) == []
