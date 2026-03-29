"""Tests for dashboard resilience features: stream quality, brightness, restart."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import _auth_failures, app, configure_auth, stream_state


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    _auth_failures.clear()
    stream_state.target_lock = {"locked": False, "track_id": None, "mode": None, "label": None}
    stream_state.runtime_config = {"threshold": 0.45, "auto_loiter": False}
    stream_state._callbacks.clear()
    stream_state.set_mjpeg_quality(70)
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Stream Quality API
# ---------------------------------------------------------------------------

class TestStreamQualityAPI:
    def test_get_quality_default(self, client):
        resp = client.get("/api/stream/quality")
        assert resp.status_code == 200
        assert resp.json()["quality"] == 70

    def test_set_quality(self, client):
        resp = client.post("/api/stream/quality", json={"quality": 50})
        assert resp.status_code == 200
        assert resp.json()["quality"] == 50

    def test_set_quality_persists(self, client):
        client.post("/api/stream/quality", json={"quality": 30})
        resp = client.get("/api/stream/quality")
        assert resp.json()["quality"] == 30

    def test_set_quality_clamped_high(self, client):
        resp = client.post("/api/stream/quality", json={"quality": 200})
        assert resp.status_code == 200
        assert resp.json()["quality"] == 100

    def test_set_quality_clamped_low(self, client):
        resp = client.post("/api/stream/quality", json={"quality": -5})
        assert resp.status_code == 200
        assert resp.json()["quality"] == 1

    def test_set_quality_invalid_type(self, client):
        resp = client.post("/api/stream/quality", json={"quality": "abc"})
        assert resp.status_code == 400

    def test_set_quality_requires_auth(self, client):
        configure_auth("secret-token-123")
        resp = client.post("/api/stream/quality", json={"quality": 50})
        assert resp.status_code == 401

    def test_set_quality_with_auth(self, client):
        configure_auth("secret-token-123")
        headers = {"Authorization": "Bearer secret-token-123"}
        resp = client.post("/api/stream/quality", json={"quality": 50}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["quality"] == 50


# ---------------------------------------------------------------------------
# Brightness Stats in API
# ---------------------------------------------------------------------------

class TestBrightnessStats:
    def test_brightness_in_stats(self, client):
        stream_state.update_stats(brightness=120.5, low_light=False)
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "brightness" in data
        assert data["brightness"] == 120.5

    def test_low_light_flag_in_stats(self, client):
        stream_state.update_stats(brightness=25.0, low_light=True)
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["low_light"] is True

    def test_low_light_false_when_bright(self, client):
        stream_state.update_stats(brightness=150.0, low_light=False)
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["low_light"] is False


# ---------------------------------------------------------------------------
# Restart Endpoint
# ---------------------------------------------------------------------------

class TestRestartEndpoint:
    def test_restart_no_callback(self, client):
        resp = client.post("/api/restart")
        assert resp.status_code == 503
        assert "not available" in resp.json()["error"]

    def test_restart_with_callback(self, client):
        called = {"count": 0}

        def on_restart():
            called["count"] += 1

        stream_state.set_callbacks(on_restart_command=on_restart)
        resp = client.post("/api/restart")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarting"
        assert called["count"] == 1

    def test_restart_requires_auth(self, client):
        configure_auth("secret-token-123")
        resp = client.post("/api/restart")
        assert resp.status_code == 401

    def test_restart_with_correct_auth(self, client):
        configure_auth("secret-token-123")
        stream_state.set_callbacks(on_restart_command=lambda: None)
        headers = {"Authorization": "Bearer secret-token-123"}
        resp = client.post("/api/restart", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarting"


# ---------------------------------------------------------------------------
# StreamState MJPEG Quality Methods
# ---------------------------------------------------------------------------

class TestStreamStateMjpegQuality:
    def test_default_quality(self):
        assert stream_state.get_mjpeg_quality() == 70

    def test_set_and_get(self):
        stream_state.set_mjpeg_quality(42)
        assert stream_state.get_mjpeg_quality() == 42

    def test_clamp_upper(self):
        stream_state.set_mjpeg_quality(150)
        assert stream_state.get_mjpeg_quality() == 100

    def test_clamp_lower(self):
        stream_state.set_mjpeg_quality(0)
        assert stream_state.get_mjpeg_quality() == 1
