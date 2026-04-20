"""Tests for the /api/preflight endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state, _auth_failures


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    _auth_failures.clear()
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


class TestPreflightEndpoint:
    def test_no_callback_returns_fail(self, client):
        """Without a pipeline callback, /api/preflight returns fail overall."""
        resp = client.get("/api/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"] == "fail"
        assert data["checks"] == []

    def test_preflight_returns_correct_structure(self, client):
        """With a callback wired, response has checks list and overall field."""
        def mock_preflight():
            return {
                "checks": [
                    {"name": "camera", "status": "pass", "message": "Camera operational"},
                    {"name": "mavlink", "status": "warn", "message": "MAVLink disabled"},
                    {"name": "gps", "status": "warn", "message": "GPS unavailable"},
                    {"name": "model", "status": "pass", "message": "Model loaded"},
                    {"name": "disk", "status": "pass", "message": "12.3 GB free"},
                ],
                "overall": "warn",
            }

        stream_state._callbacks["get_preflight"] = mock_preflight
        resp = client.get("/api/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"] == "warn"
        assert len(data["checks"]) == 5
        names = [c["name"] for c in data["checks"]]
        assert "camera" in names
        assert "mavlink" in names
        assert "gps" in names
        assert "model" in names
        assert "disk" in names

    def test_preflight_includes_expected_subsystems(self, client):
        """Verify that preflight checks cover the five core subsystems."""
        expected_names = {"camera", "mavlink", "gps", "model", "disk"}

        def mock_preflight():
            return {
                "checks": [
                    {"name": n, "status": "pass", "message": "OK"}
                    for n in expected_names
                ],
                "overall": "pass",
            }

        stream_state._callbacks["get_preflight"] = mock_preflight
        resp = client.get("/api/preflight")
        data = resp.json()
        returned_names = {c["name"] for c in data["checks"]}
        assert returned_names == expected_names

    def test_preflight_overall_fail_when_any_fail(self, client):
        """Overall should be 'fail' if any check has status 'fail'."""
        def mock_preflight():
            return {
                "checks": [
                    {"name": "camera", "status": "fail", "message": "Camera not detected"},
                    {"name": "model", "status": "pass", "message": "Model loaded"},
                ],
                "overall": "fail",
            }

        stream_state._callbacks["get_preflight"] = mock_preflight
        resp = client.get("/api/preflight")
        data = resp.json()
        assert data["overall"] == "fail"

    def test_preflight_overall_pass_when_all_pass(self, client):
        """Overall should be 'pass' when every check passes."""
        def mock_preflight():
            return {
                "checks": [
                    {"name": "camera", "status": "pass", "message": "OK"},
                    {"name": "model", "status": "pass", "message": "OK"},
                ],
                "overall": "pass",
            }

        stream_state._callbacks["get_preflight"] = mock_preflight
        resp = client.get("/api/preflight")
        data = resp.json()
        assert data["overall"] == "pass"

    def test_warning_modal_gated_by_session_storage(self, client):
        """Served preflight.js must guard the WARNING modal with sessionStorage
        and set the dismissed flag on Continue click. Operators complained the
        warn modal fired on every page load — this asserts the fix stays wired."""
        resp = client.get("/static/js/preflight/preflight.js")
        assert resp.status_code == 200
        js = resp.text
        assert "sessionStorage.getItem('hydra-preflight-dismissed')" in js
        assert "sessionStorage.setItem('hydra-preflight-dismissed', '1')" in js

    def test_preflight_check_status_values(self, client):
        """All status values should be one of pass/warn/fail."""
        def mock_preflight():
            return {
                "checks": [
                    {"name": "camera", "status": "pass", "message": "OK"},
                    {"name": "mavlink", "status": "warn", "message": "Disabled"},
                    {"name": "model", "status": "fail", "message": "Missing"},
                ],
                "overall": "fail",
            }

        stream_state._callbacks["get_preflight"] = mock_preflight
        resp = client.get("/api/preflight")
        data = resp.json()
        valid_statuses = {"pass", "warn", "fail"}
        for check in data["checks"]:
            assert check["status"] in valid_statuses
            assert "name" in check
            assert "message" in check
