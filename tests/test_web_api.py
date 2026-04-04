"""Integration tests for the FastAPI web server endpoints and auth."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import MAX_PROMPT_LENGTH, MAX_PROMPTS, app, configure_auth, stream_state


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    stream_state.target_lock = {"locked": False, "track_id": None, "mode": None, "label": None}
    stream_state.runtime_config = {"prompts": ["person"], "threshold": 0.25, "auto_loiter": False}
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


CONTROL_ENDPOINTS = [
    ("POST", "/api/config/prompts", {"prompts": ["car"]}),
    ("POST", "/api/config/threshold", {"threshold": 0.5}),
    ("POST", "/api/vehicle/loiter", None),
    ("POST", "/api/target/lock", {"track_id": 1}),
    ("POST", "/api/target/unlock", None),
    ("POST", "/api/target/strike", {"track_id": 1, "confirm": True}),
    ("POST", "/api/config/alert-classes", {"classes": ["person"]}),
    ("POST", "/api/vehicle/mode", {"mode": "AUTO"}),
]

READ_ONLY_ENDPOINTS = [
    ("GET", "/api/stats"),
    ("GET", "/api/config"),
    ("GET", "/api/tracks"),
    ("GET", "/api/target"),
    ("GET", "/api/detections"),
    ("GET", "/api/config/alert-classes"),
]


def _request(client: TestClient, method: str, url: str, body=None, headers=None):
    if method == "POST":
        return client.post(url, json=body, headers=headers or {})
    return client.get(url, headers=headers or {})


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


class TestAuthEnforcement:
    def test_empty_token_fails_closed_for_control_routes(self, client):
        configure_auth(None)
        for method, url, body in CONTROL_ENDPOINTS:
            resp = _request(client, method, url, body=body)
            assert resp.status_code == 401, f"{url} should fail closed without auth"

    def test_read_only_routes_stay_available_without_token(self, client):
        configure_auth(None)
        for method, url in READ_ONLY_ENDPOINTS:
            resp = _request(client, method, url)
            assert resp.status_code == 200, f"{url} should remain readable"

    def test_missing_token_rejected_when_token_configured(self, client):
        configure_auth("secret-token-123")
        for method, url, body in CONTROL_ENDPOINTS:
            resp = _request(client, method, url, body=body)
            assert resp.status_code == 401, f"{url} should require auth"

    def test_wrong_token_rejected(self, client):
        configure_auth("secret-token-123")
        headers = {"Authorization": "Bearer wrong-token"}
        for method, url, body in CONTROL_ENDPOINTS:
            resp = _request(client, method, url, body=body, headers=headers)
            assert resp.status_code == 403, f"{url} should reject wrong token"

    def test_valid_bearer_token_allows_control_routes(self, client):
        configure_auth("secret-token-123")
        headers = {"Authorization": "Bearer secret-token-123"}
        for method, url, body in CONTROL_ENDPOINTS:
            resp = _request(client, method, url, body=body, headers=headers)
            assert resp.status_code not in (401, 403), f"{url} returned {resp.status_code}"

    def test_explicit_insecure_override_re_enables_unauthenticated_control(self, client):
        configure_auth(None, allow_insecure_control=True)
        for method, url, body in CONTROL_ENDPOINTS:
            resp = _request(client, method, url, body=body)
            assert resp.status_code not in (401, 403), f"{url} returned {resp.status_code}"


class TestControlEndpoints:
    def test_set_prompts_validates_input(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/config/prompts", json={"prompts": []})
        assert resp.status_code == 400

    def test_set_threshold_validates_range(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/config/threshold", json={"threshold": 2.0})
        assert resp.status_code == 400

    def test_set_threshold_success(self, client):
        configure_auth(None, allow_insecure_control=True)
        called_with = {}

        def on_threshold(t):
            called_with["t"] = t

        stream_state.set_callbacks(on_threshold_change=on_threshold)
        resp = client.post("/api/config/threshold", json={"threshold": 0.6})
        assert resp.status_code == 200
        assert called_with["t"] == 0.6

    def test_lock_requires_track_id(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/target/lock", json={})
        assert resp.status_code == 400

    def test_strike_requires_confirm(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/target/strike", json={"track_id": 1})
        assert resp.status_code == 400

    def test_strike_requires_track_id(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/target/strike", json={"confirm": True})
        assert resp.status_code == 400

    def test_loiter_no_mavlink(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/vehicle/loiter")
        assert resp.status_code == 503


class TestPromptValidation:
    def test_too_many_prompts(self, client):
        configure_auth(None, allow_insecure_control=True)
        prompts = [f"item{i}" for i in range(MAX_PROMPTS + 1)]
        resp = client.post("/api/config/prompts", json={"prompts": prompts})
        assert resp.status_code == 400
        assert "max" in resp.json()["error"]

    def test_non_string_prompt(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/config/prompts", json={"prompts": [123]})
        assert resp.status_code == 400

    def test_empty_string_prompt(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/config/prompts", json={"prompts": ["valid", "  "]})
        assert resp.status_code == 400

    def test_long_prompt_truncated(self, client):
        configure_auth(None, allow_insecure_control=True)
        received = {}

        def on_prompts(p):
            received["p"] = p

        stream_state.set_callbacks(on_prompts_change=on_prompts)
        long_prompt = "x" * (MAX_PROMPT_LENGTH + 50)
        resp = client.post("/api/config/prompts", json={"prompts": [long_prompt]})
        assert resp.status_code == 200
        assert len(received["p"][0]) == MAX_PROMPT_LENGTH

    def test_prompts_stripped(self, client):
        configure_auth(None, allow_insecure_control=True)
        received = {}

        def on_prompts(p):
            received["p"] = p

        stream_state.set_callbacks(on_prompts_change=on_prompts)
        resp = client.post("/api/config/prompts", json={"prompts": ["  person  ", " car"]})
        assert resp.status_code == 200
        assert received["p"] == ["person", "car"]


class TestAuditLogging:
    def test_strike_logs_audit(self, client, caplog):
        configure_auth(None, allow_insecure_control=True)
        stream_state.set_callbacks(on_strike_command=lambda tid: True)
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            resp = client.post("/api/target/strike", json={"track_id": 7, "confirm": True})
        assert resp.status_code == 200
        assert any("action=strike" in r.message and "target=7" in r.message for r in caplog.records)

    def test_loiter_logs_audit(self, client, caplog):
        configure_auth(None, allow_insecure_control=True)
        stream_state.set_callbacks(on_loiter_command=lambda: None)
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            resp = client.post("/api/vehicle/loiter")
        assert resp.status_code == 200
        assert any("action=loiter" in r.message and "outcome=ok" in r.message for r in caplog.records)

    def test_failed_action_logs_outcome(self, client, caplog):
        configure_auth(None, allow_insecure_control=True)
        stream_state.set_callbacks(on_strike_command=lambda tid: False)
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            resp = client.post("/api/target/strike", json={"track_id": 3, "confirm": True})
        assert resp.status_code == 503
        assert any("action=strike" in r.message and "outcome=failed" in r.message for r in caplog.records)


class TestAlertClassesEndpoints:
    def test_get_alert_classes(self, client):
        stream_state.set_callbacks(get_class_names=lambda: ["person", "car", "dog"])
        stream_state.runtime_config["alert_classes"] = ["person"]
        resp = client.get("/api/config/alert-classes")
        assert resp.status_code == 200
        data = resp.json()
        assert "all_classes" in data
        assert "alert_classes" in data
        assert "categories" in data
        assert data["all_classes"] == ["person", "car", "dog"]
        assert data["alert_classes"] == ["person"]

    def test_post_alert_classes(self, client):
        configure_auth(None, allow_insecure_control=True)
        called = {}

        def on_change(classes):
            called["classes"] = classes

        stream_state.set_callbacks(
            on_alert_classes_change=on_change,
            get_class_names=lambda: ["person", "car", "dog"],
        )
        resp = client.post("/api/config/alert-classes", json={"classes": ["person", "car"]})
        assert resp.status_code == 200
        assert called["classes"] == ["person", "car"]

    def test_post_empty_means_all(self, client):
        configure_auth(None, allow_insecure_control=True)
        called = {}

        def on_change(classes):
            called["classes"] = classes

        stream_state.set_callbacks(
            on_alert_classes_change=on_change,
            get_class_names=lambda: ["person", "car"],
        )
        resp = client.post("/api/config/alert-classes", json={"classes": []})
        assert resp.status_code == 200
        assert called["classes"] == []

    def test_post_invalid_class_rejected(self, client):
        configure_auth(None, allow_insecure_control=True)
        stream_state.set_callbacks(get_class_names=lambda: ["person", "car"])
        resp = client.post("/api/config/alert-classes", json={"classes": ["person", "INVALID"]})
        assert resp.status_code == 400


class TestVehicleModeEndpoint:
    def test_set_mode_success(self, client):
        configure_auth(None, allow_insecure_control=True)
        called = {}

        def on_mode(mode):
            called["mode"] = mode
            return True

        stream_state.set_callbacks(on_set_mode_command=on_mode)
        resp = client.post("/api/vehicle/mode", json={"mode": "AUTO"})
        assert resp.status_code == 200
        assert called["mode"] == "AUTO"

    def test_set_mode_missing_mode(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/vehicle/mode", json={})
        assert resp.status_code == 400

    def test_set_mode_no_callback(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/vehicle/mode", json={"mode": "AUTO"})
        assert resp.status_code == 503

    def test_set_mode_failed(self, client):
        configure_auth(None, allow_insecure_control=True)
        stream_state.set_callbacks(on_set_mode_command=lambda m: False)
        resp = client.post("/api/vehicle/mode", json={"mode": "AUTO"})
        assert resp.status_code == 503


class TestSPAShell:
    def test_index_serves_base_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "HYDRA DETECT" in resp.text
        assert "view-operations" in resp.text
        assert "view-settings" in resp.text
        assert "stream.mjpeg" in resp.text

    def test_index_includes_static_css(self, client):
        resp = client.get("/")
        assert "/static/css/variables.css" in resp.text
        assert "/static/js/app.js" in resp.text


class TestStaticFileServing:
    def test_css_variables_served(self, client):
        resp = client.get("/static/css/variables.css")
        assert resp.status_code == 200
        assert "ogt-green" in resp.text

    def test_missing_static_file_404(self, client):
        resp = client.get("/static/nonexistent.css")
        assert resp.status_code == 404
