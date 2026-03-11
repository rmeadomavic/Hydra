"""Integration tests for the FastAPI web server endpoints and auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    stream_state.target_lock = {"locked": False, "track_id": None, "mode": None, "label": None}
    stream_state.runtime_config = {"prompts": ["person"], "threshold": 0.25, "auto_loiter": False}
    # Clear callbacks
    stream_state.set_callbacks()
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Read-only endpoints (no auth required)
# ---------------------------------------------------------------------------

class TestReadOnlyEndpoints:
    def test_stats(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        assert "fps" in resp.json()

    def test_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        assert "threshold" in resp.json()

    def test_tracks_empty(self, client):
        resp = client.get("/api/tracks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_target_status(self, client):
        resp = client.get("/api/target")
        assert resp.status_code == 200
        assert resp.json()["locked"] is False

    def test_detections_empty(self, client):
        resp = client.get("/api/detections")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Auth enforcement on control endpoints
# ---------------------------------------------------------------------------

class TestAuthEnforcement:
    CONTROL_ENDPOINTS = [
        ("POST", "/api/config/prompts", {"prompts": ["car"]}),
        ("POST", "/api/config/threshold", {"threshold": 0.5}),
        ("POST", "/api/vehicle/loiter", None),
        ("POST", "/api/target/lock", {"track_id": 1}),
        ("POST", "/api/target/unlock", None),
        ("POST", "/api/target/strike", {"track_id": 1, "confirm": True}),
    ]

    def test_no_auth_when_disabled(self, client):
        """When no token is configured, control endpoints should work without auth."""
        configure_auth(None)
        # Just check we don't get 401/403 (may get 400/503 from missing callbacks — that's fine)
        for method, url, body in self.CONTROL_ENDPOINTS:
            if body:
                resp = client.post(url, json=body)
            else:
                resp = client.post(url)
            assert resp.status_code not in (401, 403), f"{url} returned {resp.status_code}"

    def test_missing_token_rejected(self, client):
        configure_auth("secret-token-123")
        for method, url, body in self.CONTROL_ENDPOINTS:
            if body:
                resp = client.post(url, json=body)
            else:
                resp = client.post(url)
            assert resp.status_code == 401, f"{url} should require auth"

    def test_wrong_token_rejected(self, client):
        configure_auth("secret-token-123")
        headers = {"Authorization": "Bearer wrong-token"}
        for method, url, body in self.CONTROL_ENDPOINTS:
            if body:
                resp = client.post(url, json=body, headers=headers)
            else:
                resp = client.post(url, headers=headers)
            assert resp.status_code == 403, f"{url} should reject wrong token"

    def test_correct_token_accepted(self, client):
        configure_auth("secret-token-123")
        headers = {"Authorization": "Bearer secret-token-123"}
        for method, url, body in self.CONTROL_ENDPOINTS:
            if body:
                resp = client.post(url, json=body, headers=headers)
            else:
                resp = client.post(url, headers=headers)
            # Should NOT be 401 or 403 (may be 400/503 from missing callbacks)
            assert resp.status_code not in (401, 403), f"{url} returned {resp.status_code}"


# ---------------------------------------------------------------------------
# Control endpoint behaviour
# ---------------------------------------------------------------------------

class TestControlEndpoints:
    def test_set_prompts_validates_input(self, client):
        resp = client.post("/api/config/prompts", json={"prompts": []})
        assert resp.status_code == 400

    def test_set_threshold_validates_range(self, client):
        resp = client.post("/api/config/threshold", json={"threshold": 2.0})
        assert resp.status_code == 400

    def test_set_threshold_success(self, client):
        called_with = {}

        def on_threshold(t):
            called_with["t"] = t

        stream_state.set_callbacks(on_threshold_change=on_threshold)
        resp = client.post("/api/config/threshold", json={"threshold": 0.6})
        assert resp.status_code == 200
        assert called_with["t"] == 0.6

    def test_lock_requires_track_id(self, client):
        resp = client.post("/api/target/lock", json={})
        assert resp.status_code == 400

    def test_strike_requires_confirm(self, client):
        resp = client.post("/api/target/strike", json={"track_id": 1})
        assert resp.status_code == 400

    def test_strike_requires_track_id(self, client):
        resp = client.post("/api/target/strike", json={"confirm": True})
        assert resp.status_code == 400

    def test_loiter_no_mavlink(self, client):
        resp = client.post("/api/vehicle/loiter")
        assert resp.status_code == 503
