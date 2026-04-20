"""Tests for the servo state holder and /api/servo/status endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.servo import ServoState
from hydra_detect.web import server as server_module


@pytest.fixture
def client():
    return TestClient(server_module.app)


@pytest.fixture(autouse=True)
def _reset_servo_ref():
    server_module.set_servo_tracker(None)
    yield
    server_module.set_servo_tracker(None)


# ---------------------------------------------------------------------------
# ServoState unit tests
# ---------------------------------------------------------------------------

class TestServoStateDefaults:
    def test_default_state_disabled_and_zeroed(self):
        state = ServoState()
        snap = state.get_api_status()
        assert snap["enabled"] is False
        assert snap["pan_deg"] == 0.0
        assert snap["tilt_deg"] == 0.0
        assert snap["scanning"] is False
        assert snap["locked_track_id"] is None
        # Limits exposed for UI rendering
        for k in (
            "pan_limit_min", "pan_limit_max",
            "tilt_limit_min", "tilt_limit_max",
        ):
            assert k in snap

    def test_update_applies_fields(self):
        state = ServoState()
        state.update(enabled=True, pan_deg=15.5, tilt_deg=-5.25, scanning=True)
        snap = state.get_api_status()
        assert snap["enabled"] is True
        assert snap["pan_deg"] == 15.5
        assert snap["tilt_deg"] == -5.25
        assert snap["scanning"] is True

    def test_lock_track_id_assign_and_clear(self):
        state = ServoState()
        state.update(locked_track_id=7)
        assert state.get_api_status()["locked_track_id"] == 7
        state.clear_lock()
        assert state.get_api_status()["locked_track_id"] is None

    def test_limits_configurable(self):
        state = ServoState()
        state.set_limits(pan_limit_min=-45.0, tilt_limit_max=45.0)
        snap = state.get_api_status()
        assert snap["pan_limit_min"] == -45.0
        assert snap["tilt_limit_max"] == 45.0


# ---------------------------------------------------------------------------
# /api/servo/status endpoint
# ---------------------------------------------------------------------------

class TestServoStatusEndpoint:
    def test_no_controller_registered_returns_idle(self, client):
        r = client.get("/api/servo/status")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert body["pan_deg"] == 0.0
        assert body["tilt_deg"] == 0.0
        assert body["locked_track_id"] is None
        for k in (
            "pan_limit_min", "pan_limit_max",
            "tilt_limit_min", "tilt_limit_max",
        ):
            assert k in body

    def test_registered_state_surfaces_live_values(self, client):
        state = ServoState()
        state.update(enabled=True, pan_deg=22.0, tilt_deg=3.0, locked_track_id=11)
        server_module.set_servo_tracker(state)
        r = client.get("/api/servo/status")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["pan_deg"] == 22.0
        assert body["tilt_deg"] == 3.0
        assert body["locked_track_id"] == 11
