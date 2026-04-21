"""Tests for DoglegRTL — tactical return path that obscures the launch point.

Today this module only has one test (``test_dogleg_rtl_only_for_drone`` in
``test_mission_profiles.py``) which just validates profile filtering.  Here we
cover the geometry helper (``compute_dogleg_waypoint``), the phase lifecycle,
the background thread, and the fallback path on MAVLink errors.

The execute() thread has 25s + 60s polling windows.  We patch
``hydra_detect.dogleg_rtl.time.sleep`` to a no-op so the thread runs at wall
speed and tests finish in <1s.
"""

from __future__ import annotations

import math
import time
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.dogleg_rtl import DoglegRTL, compute_dogleg_waypoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink(lat=34.05, lon=-118.25, alt=10.0):
    mav = MagicMock()
    mav.get_lat_lon.return_value = (lat, lon, alt)
    mav.command_guided_to.return_value = True
    mav.set_mode.return_value = True
    return mav


def _wait_for_phase(dogleg: DoglegRTL, target: str, timeout: float = 3.0) -> bool:
    """Poll ``dogleg.phase`` until it matches ``target`` or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if dogleg.phase == target:
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# compute_dogleg_waypoint
# ---------------------------------------------------------------------------

class TestComputeDoglegWaypoint:
    def test_perpendicular_offset_is_orthogonal_to_bearing_home(self):
        """Offset waypoint should be displaced east/west of a north-south midline."""
        current = (34.10, -118.20)
        home = (34.05, -118.20)  # due south of current

        wp_lat, wp_lon = compute_dogleg_waypoint(
            current[0], current[1], home[0], home[1],
            offset_distance_m=500.0,
            offset_bearing="perpendicular",
        )
        mid_lon = (current[1] + home[1]) / 2
        assert abs(wp_lon - mid_lon) > 0.001, "expected east/west displacement"

    def test_explicit_numeric_bearing(self):
        wp_lat, wp_lon = compute_dogleg_waypoint(
            34.10, -118.20, 34.05, -118.20,
            offset_distance_m=200.0,
            offset_bearing="90",  # pure east
        )
        mid_lat = (34.10 + 34.05) / 2
        assert abs(wp_lat - mid_lat) < 0.001

    def test_invalid_bearing_falls_back_to_perpendicular(self):
        wp = compute_dogleg_waypoint(
            34.10, -118.20, 34.05, -118.20,
            offset_distance_m=200.0,
            offset_bearing="garbage",
        )
        assert isinstance(wp, tuple) and len(wp) == 2
        assert all(isinstance(x, float) for x in wp)

    def test_offset_distance_scales_linearly(self):
        current, home = (34.10, -118.20), (34.05, -118.20)
        wp_100 = compute_dogleg_waypoint(*current, *home, 100.0, "perpendicular")
        wp_500 = compute_dogleg_waypoint(*current, *home, 500.0, "perpendicular")

        mid = ((current[0] + home[0]) / 2, (current[1] + home[1]) / 2)
        d100 = math.hypot(wp_100[0] - mid[0], wp_100[1] - mid[1])
        d500 = math.hypot(wp_500[0] - mid[0], wp_500[1] - mid[1])
        assert 4.0 < d500 / d100 < 6.0


# ---------------------------------------------------------------------------
# DoglegRTL.execute — thread lifecycle
# ---------------------------------------------------------------------------

class TestExecute:
    def test_no_gps_returns_false(self):
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        dog = DoglegRTL(mav, 34.0, -118.0)
        assert dog.execute() is False

    def test_initial_phase_is_idle(self):
        mav = _make_mavlink()
        dog = DoglegRTL(mav, 34.0, -118.0)
        assert dog.phase == "idle"

    def test_execute_starts_background_thread(self):
        mav = _make_mavlink()
        dog = DoglegRTL(mav, 34.0, -118.0, climb_altitude_m=0.0)
        with patch("hydra_detect.dogleg_rtl.time.sleep", return_value=None):
            assert dog.execute() is True
            assert _wait_for_phase(dog, "done", timeout=3.0)

    def test_phase_progression_to_home(self):
        """With sleep patched and vehicle "at" the waypoint, phase reaches home → done."""
        mav = _make_mavlink()
        # Place vehicle near where the waypoint will compute (same current position)
        # so haversine_m returns 0 and the polling loop breaks immediately
        dog = DoglegRTL(
            mav, 34.0, -118.0,
            offset_distance_m=0.1,   # tiny offset so waypoint ≈ midpoint ≈ current
            climb_altitude_m=0.0,
        )
        with patch("hydra_detect.dogleg_rtl.time.sleep", return_value=None):
            dog.execute()
            assert _wait_for_phase(dog, "done", timeout=3.0)
        mav.set_mode.assert_any_call("SMART_RTL")

    @pytest.mark.regression
    def test_fallback_to_rtl_on_exception(self):
        """If SMART_RTL command raises, the exception handler must try RTL."""
        mav = _make_mavlink()
        call_log = []

        def fake_set_mode(mode):
            call_log.append(mode)
            if mode == "SMART_RTL":
                raise RuntimeError("link down")
            return True

        mav.set_mode.side_effect = fake_set_mode
        dog = DoglegRTL(
            mav, 34.0, -118.0,
            offset_distance_m=0.1,
            climb_altitude_m=0.0,
        )
        with patch("hydra_detect.dogleg_rtl.time.sleep", return_value=None):
            dog.execute()
            assert _wait_for_phase(dog, "done", timeout=3.0)
        assert "SMART_RTL" in call_log
        assert "RTL" in call_log, "fallback RTL must be attempted after SMART_RTL fails"

    def test_climb_phase_commands_waypoint_with_alt(self):
        """When climb_altitude_m > 0, execute must issue a climb waypoint with alt=."""
        mav = _make_mavlink(alt=5.0)
        # Vehicle jumps to target altitude immediately so the climb poll exits
        climb_log = []

        def fake_get_lat_lon():
            return (34.05, -118.25, 100.0)

        mav.get_lat_lon.side_effect = fake_get_lat_lon

        def fake_guided(*args, **kw):
            climb_log.append((args, kw))
            return True

        mav.command_guided_to.side_effect = fake_guided
        dog = DoglegRTL(
            mav, 34.0, -118.0,
            offset_distance_m=0.1,
            climb_altitude_m=50.0,
        )
        with patch("hydra_detect.dogleg_rtl.time.sleep", return_value=None):
            dog.execute()
            assert _wait_for_phase(dog, "done", timeout=3.0)
        # First command_guided_to should be the climb (alt kwarg set)
        assert any("alt" in kw and kw["alt"] == 50.0 for _, kw in climb_log), (
            f"expected climb waypoint with alt=50.0 in {climb_log}"
        )
