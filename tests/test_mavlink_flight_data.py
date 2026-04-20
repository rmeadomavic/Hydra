"""Tests for MAVLink flight-instrument accessors and /api/stats exposure."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hydra_detect.mavlink_io import MAVLinkIO
from hydra_detect.web import server as web_server


def _make_vfr_hud(airspeed, groundspeed, heading, alt, climb):
    msg = MagicMock()
    msg.get_type.return_value = "VFR_HUD"
    msg.airspeed = airspeed
    msg.groundspeed = groundspeed
    msg.heading = heading
    msg.alt = alt
    msg.climb = climb
    return msg


def _make_mavlink_io() -> MAVLinkIO:
    """Build a MAVLinkIO with no real serial connection."""
    return MAVLinkIO(connection_string="/dev/null", baud=115200)


class TestFlightDataAccessor:
    def test_defaults_when_no_data_ingested(self):
        mav = _make_mavlink_io()
        assert mav.get_flight_data() == {
            "heading": None,
            "airspeed": None,
            "altitude": None,
            "vertical_speed": None,
        }

    def test_vfr_hud_populates_three_values(self):
        mav = _make_mavlink_io()
        msg = _make_vfr_hud(
            airspeed=12.3, groundspeed=10.1,
            heading=235, alt=45.7, climb=1.5,
        )
        mav._handle_vfr_hud(msg)
        fd = mav.get_flight_data()
        assert fd["heading"] == 235.0
        assert fd["airspeed"] == 12.3
        assert fd["altitude"] == 45.7
        assert fd["vertical_speed"] == 1.5

    def test_global_position_heading_used_before_vfr_hud(self):
        mav = _make_mavlink_io()
        # GLOBAL_POSITION_INT reports hdg in centidegrees, alt in mm
        with mav._gps_lock:
            mav._gps["hdg"] = 18050  # 180.5 deg
            mav._gps["alt"] = 25000  # 25.0 m
        fd = mav.get_flight_data()
        assert fd["heading"] == 180.5
        assert fd["altitude"] == 25.0
        assert fd["airspeed"] is None
        assert fd["vertical_speed"] is None

    def test_vfr_hud_takes_precedence_over_global_position(self):
        mav = _make_mavlink_io()
        with mav._gps_lock:
            mav._gps["hdg"] = 9000  # 90 deg
        mav._handle_vfr_hud(_make_vfr_hud(
            airspeed=0.0, groundspeed=0.0,
            heading=270, alt=10.0, climb=0.0,
        ))
        assert mav.get_flight_data()["heading"] == 270.0

    def test_heading_wraps_to_0_360_range(self):
        mav = _make_mavlink_io()
        mav._handle_vfr_hud(_make_vfr_hud(
            airspeed=0.0, groundspeed=0.0,
            heading=725, alt=0.0, climb=0.0,
        ))
        assert mav.get_flight_data()["heading"] == 5.0


class TestApiStatsFlightFields:
    @pytest.fixture(autouse=True)
    def _reset(self):
        web_server._response_cache.clear()
        web_server._mavlink_ref = None
        yield
        web_server._mavlink_ref = None
        web_server._response_cache.clear()

    def test_stats_includes_flight_keys_when_mavlink_absent(self):
        client = TestClient(web_server.app)
        r = client.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        for key in ("heading", "airspeed", "altitude", "vertical_speed"):
            assert key in body
            assert body[key] is None

    def test_stats_surfaces_flight_data_from_mavlink(self):
        mav = _make_mavlink_io()
        mav._handle_vfr_hud(_make_vfr_hud(
            airspeed=15.2, groundspeed=14.8,
            heading=90, alt=32.0, climb=-0.4,
        ))
        web_server.set_mavlink(mav)

        client = TestClient(web_server.app)
        r = client.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["heading"] == 90.0
        assert body["airspeed"] == 15.2
        assert body["altitude"] == 32.0
        assert body["vertical_speed"] == -0.4

    def test_existing_stats_keys_preserved(self):
        client = TestClient(web_server.app)
        r = client.get("/api/stats")
        body = r.json()
        for key in (
            "fps", "inference_ms", "active_tracks",
            "total_detections", "mavlink", "gps_fix", "position",
        ):
            assert key in body
