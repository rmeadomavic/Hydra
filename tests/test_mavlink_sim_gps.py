"""Tests for simulated GPS fallback in MAVLinkIO."""

from __future__ import annotations

from hydra_detect.mavlink_io import MAVLinkIO


def _make_mav(*, sim_lat=None, sim_lon=None, min_gps_fix=3):
    """Build MAVLinkIO with sim GPS config but no real connection."""
    mav = MAVLinkIO(
        connection_string="udp:127.0.0.1:14550",
        min_gps_fix=min_gps_fix,
        sim_gps_lat=sim_lat,
        sim_gps_lon=sim_lon,
    )
    return mav


class TestSimGpsDisabled:
    def test_no_sim_returns_none(self):
        mav = _make_mav()
        lat, lon, alt = mav.get_lat_lon()
        assert lat is None

    def test_is_sim_gps_false_by_default(self):
        mav = _make_mav()
        assert mav.is_sim_gps is False

    def test_gps_fix_not_ok_without_sim(self):
        mav = _make_mav()
        assert mav.gps_fix_ok is False


class TestSimGpsFallback:
    def test_sim_gps_returns_coords_when_no_fix(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        lat, lon, alt = mav.get_lat_lon()
        assert lat == 34.05
        assert lon == -118.25
        assert alt == 30.0

    def test_sim_gps_fix_ok(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        assert mav.gps_fix_ok is True

    def test_is_sim_gps_true_when_using_sim(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        mav.get_lat_lon()
        assert mav.is_sim_gps is True

    def test_real_gps_takes_priority(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        with mav._gps_lock:
            mav._gps["lat"] = int(40.7128 * 1e7)
            mav._gps["lon"] = int(-74.006 * 1e7)
            mav._gps["alt"] = int(10.0 * 1000)
            mav._gps["fix"] = 3
        lat, lon, alt = mav.get_lat_lon()
        assert abs(lat - 40.7128) < 0.001
        assert mav.is_sim_gps is False

    def test_sim_requires_both_coords(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=None)
        lat, lon, alt = mav.get_lat_lon()
        assert lat is None
