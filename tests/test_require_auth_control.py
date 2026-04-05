"""Tests for require_auth_for_control config flag."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import (
    _auth_failures,
    app,
    configure_auth,
    configure_web_password,
    stream_state,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset auth state between tests."""
    configure_auth(None)
    configure_web_password(None)
    _auth_failures.clear()
    stream_state._callbacks.clear()
    yield
    # Restore defaults
    configure_auth(None)


@pytest.fixture
def client():
    return TestClient(app)


# A control POST endpoint to test against
_CONTROL_ENDPOINT = "/api/config/prompts"
_CONTROL_BODY = {"prompts": ["person"]}


class TestRequireAuthForControl:
    """require_auth_for_control = true with no api_token denies control."""

    def test_control_denied_when_flag_true_no_token(self, client):
        """POST control endpoint returns 401 when flag is true and no token."""
        configure_auth(None, require_auth_for_control=True)
        resp = client.post(_CONTROL_ENDPOINT, json=_CONTROL_BODY)
        assert resp.status_code == 401
        data = resp.json()
        assert "require" in data["error"].lower() or "api_token" in data["error"]

    def test_control_allowed_when_flag_false(self, client):
        """POST control endpoint succeeds when flag is false (default)."""
        configure_auth(None, require_auth_for_control=False)
        resp = client.post(_CONTROL_ENDPOINT, json=_CONTROL_BODY)
        # Should succeed (200) — no auth required
        assert resp.status_code == 200

    def test_bearer_token_works_regardless_of_flag(self, client):
        """Bearer token auth works whether flag is true or false."""
        token = "test-secret-token"
        configure_auth(token, require_auth_for_control=True)
        resp = client.post(
            _CONTROL_ENDPOINT,
            json=_CONTROL_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_read_endpoints_always_accessible(self, client):
        """GET read-only endpoints work regardless of flag setting."""
        configure_auth(None, require_auth_for_control=True)
        # Health is always accessible
        resp = client.get("/api/health")
        assert resp.status_code in (200, 503)  # depends on camera state
        # Stats is always accessible
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        # Tracks is always accessible
        resp = client.get("/api/tracks")
        assert resp.status_code == 200

    def test_flag_true_with_token_allows_authenticated_control(self, client):
        """When flag=true and token is set, authenticated requests succeed."""
        token = "my-token"
        configure_auth(token, require_auth_for_control=True)
        # Without token — denied
        resp = client.post(_CONTROL_ENDPOINT, json=_CONTROL_BODY)
        assert resp.status_code == 401
        # With token — allowed
        resp = client.post(
            _CONTROL_ENDPOINT,
            json=_CONTROL_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_error_message_content(self, client):
        """Error message guides user to set api_token or disable the flag."""
        configure_auth(None, require_auth_for_control=True)
        resp = client.post(_CONTROL_ENDPOINT, json=_CONTROL_BODY)
        msg = resp.json()["error"]
        assert "api_token" in msg
        assert "require_auth_for_control" in msg


class TestControlPageActionsWithTokenAuth:
    """Regression coverage for legacy /control actions when token auth is enabled."""

    def test_lock_unlock_strike_require_and_accept_bearer_token(self, client):
        token = "control-secret-token"
        configure_auth(token, require_auth_for_control=True)

        stream_state.set_callbacks(
            on_target_lock=lambda track_id, mode="track": True,
            on_target_unlock=lambda: True,
            on_strike_command=lambda track_id: True,
        )

        # Legacy /control action APIs should deny unauthenticated control posts.
        lock_unauth = client.post("/api/target/lock", json={"track_id": 7})
        unlock_unauth = client.post("/api/target/unlock")
        strike_unauth = client.post("/api/target/strike", json={"track_id": 7, "confirm": True})

        assert lock_unauth.status_code == 401
        assert unlock_unauth.status_code == 401
        assert strike_unauth.status_code == 401

        headers = {"Authorization": f"Bearer {token}"}

        lock_auth = client.post("/api/target/lock", json={"track_id": 7}, headers=headers)
        unlock_auth = client.post("/api/target/unlock", headers=headers)
        strike_auth = client.post("/api/target/strike", json={"track_id": 7, "confirm": True}, headers=headers)

        assert lock_auth.status_code == 200
        assert unlock_auth.status_code == 200
        assert strike_auth.status_code == 200
