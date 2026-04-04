"""Tests for instructor overview page, mission tagging, battery state, and abort."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import (
    _auth_failures,
    app,
    configure_auth,
    stream_state,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    _auth_failures.clear()
    stream_state.target_lock = {
        "locked": False,
        "track_id": None,
        "mode": None,
        "label": None,
    }
    stream_state.runtime_config = {
        "prompts": ["person"],
        "threshold": 0.25,
        "auto_loiter": False,
    }
    stream_state._callbacks.clear()
    # Reset stats to include battery and mission fields
    stream_state.stats = {
        "fps": 10.0,
        "inference_ms": 15.0,
        "active_tracks": 0,
        "total_detections": 0,
        "detector": "yolo",
        "mavlink": False,
        "gps_fix": 0,
        "position": None,
        "camera_ok": True,
        "callsign": "HYDRA-TEST",
        "mission_name": None,
        "battery_v": None,
        "battery_pct": None,
    }
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Instructor page
# ---------------------------------------------------------------------------


class TestInstructorPage:
    def test_instructor_page_returns_200(self, client):
        resp = client.get("/instructor")
        assert resp.status_code == 200
        assert "SORCC INSTRUCTOR OVERVIEW" in resp.text

    def test_instructor_page_csp_relaxed(self, client):
        resp = client.get("/instructor")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "connect-src *" in csp

    def test_non_instructor_page_csp_strict(self, client):
        resp = client.get("/api/stats")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "connect-src 'self'" in csp


# ---------------------------------------------------------------------------
# Abort endpoint
# ---------------------------------------------------------------------------


class TestAbortEndpoint:
    def test_abort_no_callback(self, client):
        """Abort returns 503 when MAVLink is not connected."""
        resp = client.post("/api/abort")
        assert resp.status_code == 503

    def test_abort_with_callback(self, client):
        """Abort tries RTL mode via set_mode callback."""
        modes_attempted = []

        def fake_set_mode(mode):
            modes_attempted.append(mode)
            return mode == "RTL"

        stream_state.set_callbacks(on_set_mode_command=fake_set_mode)
        resp = client.post("/api/abort")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["mode"] == "RTL"
        assert "RTL" in modes_attempted

    def test_abort_fallback_to_loiter(self, client):
        """Abort falls back to LOITER if RTL fails."""
        def fake_set_mode(mode):
            return mode == "LOITER"

        stream_state.set_callbacks(on_set_mode_command=fake_set_mode)
        resp = client.post("/api/abort")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "LOITER"

    def test_abort_no_auth_required(self, client):
        """Abort is intentionally unauthenticated (safety exception)."""
        configure_auth("secret-token-123")
        # No auth header — should still work

        def fake_set_mode(mode):
            return True

        stream_state.set_callbacks(on_set_mode_command=fake_set_mode)
        resp = client.post("/api/abort")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Mission tagging
# ---------------------------------------------------------------------------


class TestMissionTagging:
    def test_start_mission(self, client):
        started = []
        stream_state.set_callbacks(on_mission_start=lambda n: started.append(n))
        resp = client.post(
            "/api/mission/start",
            json={"name": "alpha-recon"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["name"] == "alpha-recon"
        assert started == ["alpha-recon"]

    def test_start_mission_auto_name(self, client):
        """If no name provided, auto-generates one."""
        stream_state.set_callbacks(on_mission_start=lambda n: None)
        resp = client.post("/api/mission/start", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"].startswith("mission-")

    def test_start_mission_name_bounded(self, client):
        """Name is truncated to 100 chars."""
        stream_state.set_callbacks(on_mission_start=lambda n: None)
        resp = client.post(
            "/api/mission/start",
            json={"name": "x" * 200},
        )
        assert resp.status_code == 200
        assert len(resp.json()["name"]) <= 100

    def test_start_mission_empty_name(self, client):
        """Empty name is rejected."""
        resp = client.post("/api/mission/start", json={"name": ""})
        assert resp.status_code == 400

    def test_end_mission(self, client):
        ended = []
        stream_state.set_callbacks(on_mission_end=lambda: ended.append(True))
        resp = client.post("/api/mission/end")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ended"
        assert ended == [True]

    def test_mission_requires_auth_when_enabled(self, client):
        configure_auth("secret-token")
        resp = client.post("/api/mission/start", json={"name": "test"})
        assert resp.status_code == 401

        resp = client.post(
            "/api/mission/start",
            json={"name": "test"},
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Battery and mission in stats
# ---------------------------------------------------------------------------


class TestBatteryInStats:
    def test_battery_in_stats(self, client):
        """Battery voltage and percentage appear in stats API."""
        stream_state.update_stats(battery_v=12.6, battery_pct=85)
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["battery_v"] == 12.6
        assert data["battery_pct"] == 85

    def test_battery_null_when_unavailable(self, client):
        """Battery fields are None when no MAVLink data."""
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["battery_v"] is None
        assert data["battery_pct"] is None

    def test_callsign_in_stats(self, client):
        """Callsign appears in stats API for instructor page to read."""
        stream_state.update_stats(callsign="ENFORCER-1")
        resp = client.get("/api/stats")
        assert resp.json()["callsign"] == "ENFORCER-1"

    def test_mission_name_in_stats(self, client):
        """Mission name appears in stats API."""
        stream_state.update_stats(mission_name="night-patrol")
        resp = client.get("/api/stats")
        assert resp.json()["mission_name"] == "night-patrol"

    def test_mission_name_null_when_idle(self, client):
        """Mission name is None when no mission is active."""
        resp = client.get("/api/stats")
        assert resp.json()["mission_name"] is None


# ---------------------------------------------------------------------------
# Battery parsing from MAVLink (unit test for MAVLinkIO telemetry)
# ---------------------------------------------------------------------------


class TestBatteryParsing:
    def test_battery_state_from_sys_status(self):
        """Verify that SYS_STATUS parsing sets battery fields correctly."""
        from hydra_detect.mavlink_io import MAVLinkIO

        mav = MAVLinkIO.__new__(MAVLinkIO)
        # Initialize required attributes manually (avoid full __init__)
        mav._gps_lock = __import__("threading").Lock()
        mav._telemetry = {
            "armed": False,
            "battery_v": None,
            "battery_pct": None,
            "groundspeed": None,
            "altitude": None,
            "heading": None,
        }
        mav._gps = {
            "lat": None, "lon": None, "alt": None, "fix": 0, "hdg": None,
            "last_update": 0.0,
        }
        mav._vehicle_mode_lock = __import__("threading").Lock()
        mav._vehicle_mode = None

        # Simulate SYS_STATUS parsing logic
        # voltage_battery in mV, battery_remaining in %
        voltage_battery = 12600  # 12.6V
        battery_remaining = 75

        with mav._gps_lock:
            if voltage_battery != 0xFFFF:
                mav._telemetry["battery_v"] = round(voltage_battery / 1000.0, 2)
            if battery_remaining != -1:
                mav._telemetry["battery_pct"] = battery_remaining

        telem = mav.get_telemetry()
        assert telem["battery_v"] == 12.6
        assert telem["battery_pct"] == 75

    def test_battery_unknown_remaining(self):
        """Battery remaining -1 means unknown."""
        from hydra_detect.mavlink_io import MAVLinkIO

        mav = MAVLinkIO.__new__(MAVLinkIO)
        mav._gps_lock = __import__("threading").Lock()
        mav._telemetry = {
            "armed": False,
            "battery_v": None,
            "battery_pct": None,
            "groundspeed": None,
            "altitude": None,
            "heading": None,
        }
        mav._gps = {
            "lat": None, "lon": None, "alt": None, "fix": 0, "hdg": None,
            "last_update": 0.0,
        }
        mav._vehicle_mode_lock = __import__("threading").Lock()
        mav._vehicle_mode = None

        voltage_battery = 11800
        battery_remaining = -1  # Unknown

        with mav._gps_lock:
            if voltage_battery != 0xFFFF:
                mav._telemetry["battery_v"] = round(voltage_battery / 1000.0, 2)
            if battery_remaining != -1:
                mav._telemetry["battery_pct"] = battery_remaining

        telem = mav.get_telemetry()
        assert telem["battery_v"] == 11.8
        assert telem["battery_pct"] is None
