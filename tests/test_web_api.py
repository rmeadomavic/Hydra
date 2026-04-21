"""Integration tests for the FastAPI web server endpoints and auth."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import (
    MAX_PROMPT_LENGTH,
    MAX_PROMPTS,
    _auth_failures,
    _cached_callback,
    _categorize_classes,
    _response_cache,
    _RESPONSE_CACHE_TTL,
    app,
    configure_auth,
    configure_web_password,
    stream_state,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    configure_web_password(None)
    _auth_failures.clear()
    _response_cache.clear()
    stream_state.target_lock = {"locked": False, "track_id": None, "mode": None, "label": None}
    stream_state.runtime_config = {"prompts": ["person"], "threshold": 0.25, "auto_loiter": False}
    # Clear callbacks
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Read-only endpoints (no auth required)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_ok(self, client):
        stream_state.update_stats(camera_ok=True, fps=15.0)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True
        assert data["camera_ok"] is True

    def test_health_camera_lost(self, client):
        stream_state.update_stats(camera_ok=False, fps=0.0)
        resp = client.get("/api/health")
        assert resp.status_code == 503
        assert resp.json()["healthy"] is False

    def test_health_zero_fps(self, client):
        stream_state.update_stats(camera_ok=True, fps=0.0)
        resp = client.get("/api/health")
        assert resp.status_code == 503
        assert resp.json()["healthy"] is False


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
# /api/tak/commands — B1 inbound command feed
# ---------------------------------------------------------------------------

class TestTakCommandsEndpoint:
    """Covers GET /api/tak/commands — disabled path, accept/reject content,
    limit query-param clamping, same-origin auth-bypass behavior."""

    def _install_tak_input(self):
        """Install a minimal TAKInput with known state for endpoint tests."""
        from unittest.mock import MagicMock

        from hydra_detect.tak.tak_input import TAKInput
        from hydra_detect.web import server as srv

        ti = TAKInput(
            listen_port=16999, multicast_group="",
            on_lock=MagicMock(return_value=True),
            on_strike=MagicMock(return_value=True),
            on_unlock=MagicMock(),
            allowed_callsigns=["ALPHA-1"], my_callsign="HYDRA-1",
        )
        srv.set_tak_input(ti)
        return ti

    def _teardown(self):
        from hydra_detect.web import server as srv
        srv.set_tak_input(None)

    def test_disabled_when_no_tak_input(self, client):
        from hydra_detect.web import server as srv
        srv.set_tak_input(None)
        resp = client.get("/api/tak/commands")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["commands"] == []
        assert body["allowed_callsigns"] == []
        assert body["hmac_enforced"] is False
        assert body["duplicate_callsign_alarm"] is False

    def test_enabled_returns_events(self, client):
        ti = self._install_tak_input()
        try:
            ti._log_command_event(
                accepted=True, sender="ALPHA-1", addressee="HYDRA",
                action="LOCK", track_id=5, hmac_state="disabled",
                routing="fleet", reject_reason=None,
                raw_text="HYDRA LOCK 5",
            )
            ti._log_command_event(
                accepted=False, sender="HACKER", addressee="HYDRA",
                action="LOCK", track_id=7, hmac_state="disabled",
                routing="fleet", reject_reason="unauthorized_sender",
                raw_text="HYDRA LOCK 7",
            )
            resp = client.get("/api/tak/commands")
            assert resp.status_code == 200
            body = resp.json()
            assert body["enabled"] is True
            assert body["hmac_enforced"] is False
            assert body["allowed_callsigns"] == ["ALPHA-1"]
            assert len(body["commands"]) == 2
            first, second = body["commands"]
            assert first["action"] == "LOCK" and first["accepted"] is True
            assert second["reject_reason"] == "unauthorized_sender"
            # Every entry contains the documented fields
            for ev in body["commands"]:
                for field in (
                    "ts", "accepted", "sender", "addressee", "action",
                    "track_id", "hmac_state", "routing", "reject_reason",
                    "raw_text",
                ):
                    assert field in ev, f"missing {field}"
        finally:
            self._teardown()

    def test_limit_query_param_clamps(self, client):
        ti = self._install_tak_input()
        try:
            for i in range(25):
                ti._log_command_event(
                    accepted=True, sender="ALPHA-1", addressee="HYDRA",
                    action="LOCK", track_id=i, hmac_state="disabled",
                    routing="fleet", reject_reason=None, raw_text="",
                )
            # Default = 100 (all 25 fit)
            resp = client.get("/api/tak/commands")
            assert len(resp.json()["commands"]) == 25

            # Explicit limit = 5 returns newest 5
            resp = client.get("/api/tak/commands?limit=5")
            body = resp.json()
            assert body["limit"] == 5
            assert len(body["commands"]) == 5
            assert [e["track_id"] for e in body["commands"]] == [20, 21, 22, 23, 24]

            # Over-cap limit clamped to 500
            resp = client.get("/api/tak/commands?limit=9999")
            assert resp.json()["limit"] == 500

            # Bad value → default 100
            resp = client.get("/api/tak/commands?limit=not-a-number")
            assert resp.json()["limit"] == 100

            # Sub-1 limit clamped to 1
            resp = client.get("/api/tak/commands?limit=0")
            assert resp.json()["limit"] == 1
        finally:
            self._teardown()

    def test_auth_free_read_when_token_enabled(self, client):
        """Endpoint skips Bearer auth — like /api/stats and /api/tracks."""
        configure_auth("secret-token-xyz")
        self._install_tak_input()
        try:
            resp = client.get("/api/tak/commands")
            # No Authorization header — should still be 200, not 401/403
            assert resp.status_code == 200
            assert resp.json()["enabled"] is True
        finally:
            self._teardown()
            configure_auth(None)

    def test_hmac_enforced_flag_reflects_config(self, client):
        from unittest.mock import MagicMock

        from hydra_detect.tak.tak_input import TAKInput
        from hydra_detect.web import server as srv

        ti = TAKInput(
            listen_port=16999, multicast_group="",
            on_lock=MagicMock(), on_strike=MagicMock(), on_unlock=MagicMock(),
            allowed_callsigns=["ALPHA-1"], hmac_secret="shhh",
            my_callsign="HYDRA-1",
        )
        srv.set_tak_input(ti)
        try:
            resp = client.get("/api/tak/commands")
            assert resp.json()["hmac_enforced"] is True
        finally:
            srv.set_tak_input(None)


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
        ("POST", "/api/config/alert-classes", {"classes": ["person"]}),
        ("POST", "/api/vehicle/mode", {"mode": "AUTO"}),
        ("POST", "/api/rtsp/toggle", {"enabled": True}),
        ("POST", "/api/mavlink-video/toggle", {"enabled": True}),
        ("POST", "/api/mavlink-video/tune", {"width": 80}),
        ("POST", "/api/profiles/switch", {"profile": "general"}),
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

    def test_forged_sec_fetch_site_does_not_bypass_token_checks(self, client):
        configure_auth("secret-token-123")
        headers = {"Sec-Fetch-Site": "same-origin"}
        for method, url, body in self.CONTROL_ENDPOINTS:
            if body:
                resp = client.post(url, json=body, headers=headers)
            else:
                resp = client.post(url, headers=headers)
            assert resp.status_code == 401, (
                f"{url} should require auth despite forged sec-fetch-site"
            )

    def test_origin_subdomain_spoof_does_not_bypass_token_checks(self, client):
        configure_auth("secret-token-123")
        headers = {"Origin": "https://testserver.evil.tld"}
        for method, url, body in self.CONTROL_ENDPOINTS:
            if body:
                resp = client.post(url, json=body, headers=headers)
            else:
                resp = client.post(url, headers=headers)
            assert resp.status_code == 401, f"{url} should require auth for spoofed origin"

    def test_valid_session_cookie_bypasses_bearer_token(self, client):
        configure_auth("secret-token-123")
        configure_web_password("ui-password-1")
        login_resp = client.post("/auth/login", json={"password": "ui-password-1"})
        assert login_resp.status_code == 200
        assert "hydra_session" in login_resp.headers.get("set-cookie", "")

        for method, url, body in self.CONTROL_ENDPOINTS:
            if body:
                resp = client.post(url, json=body)
            else:
                resp = client.post(url)
            assert resp.status_code not in (401, 403), f"{url} should accept valid session cookie"

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


class TestAuthStatusEndpoint:
    def test_auth_status_when_password_disabled(self, client):
        configure_web_password(None)
        resp = client.get("/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {"password_enabled": False, "authenticated": True}

    def test_auth_status_when_password_enabled_without_session(self, client):
        configure_web_password("ui-password-1")
        resp = client.get("/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {"password_enabled": True, "authenticated": False}

    def test_auth_status_when_password_enabled_with_session(self, client):
        configure_web_password("ui-password-1")
        login_resp = client.post("/auth/login", json={"password": "ui-password-1"})
        assert login_resp.status_code == 200

        resp = client.get("/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {"password_enabled": True, "authenticated": True}


class TestWaypointExportAuth:
    def test_waypoint_export_requires_auth_when_token_enabled(self, client):
        stream_state.set_callbacks(
            get_recent_detections=lambda: [
                {"label": "person", "confidence": 0.9, "lat": 35.1, "lon": -117.2},
            ],
        )
        configure_auth("secret-token-123")

        resp = client.get("/api/export/waypoints")
        assert resp.status_code == 401

    def test_waypoint_export_rejects_wrong_token(self, client):
        stream_state.set_callbacks(
            get_recent_detections=lambda: [
                {"label": "person", "confidence": 0.9, "lat": 35.1, "lon": -117.2},
            ],
        )
        configure_auth("secret-token-123")

        resp = client.get(
            "/api/export/waypoints",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 403

    def test_waypoint_export_succeeds_with_valid_token(self, client):
        stream_state.set_callbacks(
            get_recent_detections=lambda: [
                {"label": "person", "confidence": 0.9, "lat": 35.1, "lon": -117.2},
            ],
        )
        configure_auth("secret-token-123")

        resp = client.get(
            "/api/export/waypoints",
            headers={"Authorization": "Bearer secret-token-123"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "hydra-waypoints.wpl" in resp.headers["content-disposition"]
        assert resp.text.startswith("QGC WPL 110\n")


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


# ---------------------------------------------------------------------------
# Prompt input validation
# ---------------------------------------------------------------------------

class TestPromptValidation:
    def test_too_many_prompts(self, client):
        prompts = [f"item{i}" for i in range(MAX_PROMPTS + 1)]
        resp = client.post("/api/config/prompts", json={"prompts": prompts})
        assert resp.status_code == 400
        assert "max" in resp.json()["error"]

    def test_non_string_prompt(self, client):
        resp = client.post("/api/config/prompts", json={"prompts": [123]})
        assert resp.status_code == 400

    def test_empty_string_prompt(self, client):
        resp = client.post("/api/config/prompts", json={"prompts": ["valid", "  "]})
        assert resp.status_code == 400

    def test_long_prompt_truncated(self, client):
        received = {}

        def on_prompts(p):
            received["p"] = p

        stream_state.set_callbacks(on_prompts_change=on_prompts)
        long_prompt = "x" * (MAX_PROMPT_LENGTH + 50)
        resp = client.post("/api/config/prompts", json={"prompts": [long_prompt]})
        assert resp.status_code == 200
        assert len(received["p"][0]) == MAX_PROMPT_LENGTH

    def test_prompts_stripped(self, client):
        received = {}

        def on_prompts(p):
            received["p"] = p

        stream_state.set_callbacks(on_prompts_change=on_prompts)
        resp = client.post("/api/config/prompts", json={"prompts": ["  person  ", " car"]})
        assert resp.status_code == 200
        assert received["p"] == ["person", "car"]


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

class TestAuditLogging:
    def test_strike_logs_audit(self, client, caplog):
        stream_state.set_callbacks(on_strike_command=lambda tid: True)
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            resp = client.post("/api/target/strike", json={"track_id": 7, "confirm": True})
        assert resp.status_code == 200
        assert any("action=strike" in r.message and "target=7" in r.message for r in caplog.records)

    def test_loiter_logs_audit(self, client, caplog):
        stream_state.set_callbacks(on_loiter_command=lambda: None)
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            resp = client.post("/api/vehicle/loiter")
        assert resp.status_code == 200
        assert any(
            "action=loiter" in r.message and "outcome=ok" in r.message
            for r in caplog.records
        )

    def test_failed_action_logs_outcome(self, client, caplog):
        stream_state.set_callbacks(on_strike_command=lambda tid: False)
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            resp = client.post("/api/target/strike", json={"track_id": 3, "confirm": True})
        assert resp.status_code == 503
        assert any(
            "action=strike" in r.message and "outcome=failed" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Alert classes endpoints
# ---------------------------------------------------------------------------

class TestAlertClassesEndpoints:
    def test_get_alert_classes(self, client):
        stream_state.set_callbacks(
            get_class_names=lambda: ["person", "car", "dog"],
        )
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
        stream_state.set_callbacks(
            get_class_names=lambda: ["person", "car"],
        )
        resp = client.post("/api/config/alert-classes", json={"classes": ["person", "INVALID"]})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Case-insensitive category matching
# ---------------------------------------------------------------------------

class TestCategorizeClasses:
    def test_mixed_case_aircraft(self):
        """'Drone' (title case) maps to Aircraft via case-insensitive lookup."""
        result = _categorize_classes(["Drone", "DRONE", "drone"])
        assert "Aircraft" in result
        assert result["Aircraft"] == ["Drone", "DRONE", "drone"]

    def test_mixed_case_ground_vehicles(self):
        """'APC' (uppercase) maps to Ground Vehicles."""
        result = _categorize_classes(["APC", "Car", "TRUCK"])
        assert "Ground Vehicles" in result
        assert set(result["Ground Vehicles"]) == {"APC", "Car", "TRUCK"}

    def test_mixed_case_weapons(self):
        """'Gun' (title case) maps to Weapons/Threats."""
        result = _categorize_classes(["Gun", "KNIFE", "grenade"])
        assert "Weapons/Threats" in result
        assert set(result["Weapons/Threats"]) == {"Gun", "KNIFE", "grenade"}

    def test_unknown_class_falls_to_other(self):
        """Classes not in any category fall to 'Other'."""
        result = _categorize_classes(["spaceship", "laser_cannon"])
        assert "Other" in result
        assert set(result["Other"]) == {"spaceship", "laser_cannon"}

    def test_mixed_known_and_unknown(self):
        """Mix of known and unknown classes are correctly categorized."""
        result = _categorize_classes(["Person", "unknown_widget", "car"])
        assert "People" in result
        assert result["People"] == ["Person"]
        assert "Ground Vehicles" in result
        assert result["Ground Vehicles"] == ["car"]
        assert "Other" in result
        assert result["Other"] == ["unknown_widget"]

    def test_get_endpoint_categories_case_insensitive(self, client):
        """GET /api/config/alert-classes returns properly categorized mixed-case classes."""
        stream_state.set_callbacks(
            get_class_names=lambda: ["Person", "DRONE", "Gun", "alien"],
        )
        stream_state.runtime_config["alert_classes"] = []
        resp = client.get("/api/config/alert-classes")
        assert resp.status_code == 200
        data = resp.json()
        cats = data["categories"]
        assert "People" in cats
        assert "Person" in cats["People"]
        assert "Aircraft" in cats
        assert "DRONE" in cats["Aircraft"]
        assert "Weapons/Threats" in cats
        assert "Gun" in cats["Weapons/Threats"]
        assert "Other" in cats
        assert "alien" in cats["Other"]


# ---------------------------------------------------------------------------
# Vehicle mode endpoint
# ---------------------------------------------------------------------------

class TestVehicleModeEndpoint:
    def test_set_mode_success(self, client):
        called = {}

        def on_mode(mode):
            called["mode"] = mode
            return True
        stream_state.set_callbacks(on_set_mode_command=on_mode)
        resp = client.post("/api/vehicle/mode", json={"mode": "AUTO"})
        assert resp.status_code == 200
        assert called["mode"] == "AUTO"

    def test_set_mode_missing_mode(self, client):
        resp = client.post("/api/vehicle/mode", json={})
        assert resp.status_code == 400

    def test_set_mode_no_callback(self, client):
        resp = client.post("/api/vehicle/mode", json={"mode": "AUTO"})
        assert resp.status_code == 503

    def test_set_mode_failed(self, client):
        stream_state.set_callbacks(on_set_mode_command=lambda m: False)
        resp = client.post("/api/vehicle/mode", json={"mode": "AUTO"})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

class TestSPAShell:
    def test_index_serves_base_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "HYDRA DETECT" in resp.text
        assert "view-ops" in resp.text
        assert "view-config" in resp.text
        assert "view-settings" in resp.text
        assert "mjpeg-stream" in resp.text

    def test_index_includes_static_css(self, client):
        resp = client.get("/")
        assert "/static/css/variables.css" in resp.text
        assert "/static/js/app.js" in resp.text

    def test_bandwidth_toggle_uses_js_listener_for_csp(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="bandwidth-toggle"' in resp.text
        assert 'onclick="HydraApp.toggleLowBandwidth()"' not in resp.text


class TestStaticFileServing:
    def test_css_variables_served(self, client):
        resp = client.get("/static/css/variables.css")
        assert resp.status_code == 200
        assert "olive-primary" in resp.text

    def test_app_js_binds_bandwidth_click_handler(self, client):
        resp = client.get("/static/js/app.js")
        assert resp.status_code == 200
        assert "btn.addEventListener('click', toggleLowBandwidth);" in resp.text

    def test_missing_static_file_404(self, client):
        resp = client.get("/static/nonexistent.css")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RTSP endpoints
# ---------------------------------------------------------------------------

class TestRTSPEndpoints:
    def test_rtsp_status_default(self, client):
        """Status endpoint returns shape even without callback."""
        resp = client.get("/api/rtsp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "running" in data

    def test_rtsp_toggle_requires_auth(self, client):
        configure_auth("secret-token-123")
        resp = client.post("/api/rtsp/toggle", json={"enabled": True})
        assert resp.status_code == 401

    def test_rtsp_toggle_with_auth(self, client):
        configure_auth("secret-token-123")
        called = {}

        def on_toggle(enabled):
            called["enabled"] = enabled
            return {"status": "ok", "running": enabled}
        stream_state.set_callbacks(on_rtsp_toggle=on_toggle)
        headers = {"Authorization": "Bearer secret-token-123"}
        resp = client.post("/api/rtsp/toggle", json={"enabled": True}, headers=headers)
        assert resp.status_code == 200
        assert called["enabled"] is True

    def test_rtsp_toggle_no_auth_when_disabled(self, client):
        configure_auth(None)
        called = {}

        def on_toggle(enabled):
            called["enabled"] = enabled
            return {"status": "ok", "running": enabled}
        stream_state.set_callbacks(on_rtsp_toggle=on_toggle)
        resp = client.post("/api/rtsp/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert called["enabled"] is False

    def test_rtsp_toggle_missing_body(self, client):
        resp = client.post("/api/rtsp/toggle", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# MAVLink Video endpoints
# ---------------------------------------------------------------------------

class TestMAVLinkVideoEndpoints:
    def test_status_default(self, client):
        resp = client.get("/api/mavlink-video/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data

    def test_toggle_requires_auth(self, client):
        configure_auth("secret-token-123")
        resp = client.post("/api/mavlink-video/toggle", json={"enabled": True})
        assert resp.status_code == 401

    def test_toggle_works(self, client):
        called = {}

        def on_toggle(enabled):
            called["enabled"] = enabled
            return {"status": "ok", "running": enabled}
        stream_state.set_callbacks(on_mavlink_video_toggle=on_toggle)
        resp = client.post("/api/mavlink-video/toggle", json={"enabled": True})
        assert resp.status_code == 200

    def test_toggle_missing_field(self, client):
        resp = client.post("/api/mavlink-video/toggle", json={})
        assert resp.status_code == 400

    def test_tune_validates_range(self, client):
        def on_tune(params):
            return {"status": "error", "message": "Invalid parameter value"}
        stream_state.set_callbacks(on_mavlink_video_tune=on_tune)
        resp = client.post("/api/mavlink-video/tune", json={"width": 5000})
        assert resp.status_code == 400  # Server-side validation rejects before callback

    def test_tune_success(self, client):
        def on_tune(params):
            return {"status": "ok", "width": 80, "height": 60}
        stream_state.set_callbacks(on_mavlink_video_tune=on_tune)
        resp = client.post("/api/mavlink-video/tune", json={"width": 80, "height": 60})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Mission Profile endpoints
# ---------------------------------------------------------------------------

class TestProfileEndpoints:
    def test_get_profiles(self, client):
        stream_state.set_callbacks(
            get_profiles=lambda: {
                "profiles": [
                    {"id": "general", "name": "General", "description": "test",
                     "model": "yolov8n.pt", "model_exists": True,
                     "confidence": 0.45, "alert_classes": ["person"],
                     "auto_loiter_on_detect": False},
                ],
                "active_profile": "general",
            },
        )
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["profiles"]) == 1
        assert data["active_profile"] == "general"

    def test_get_profiles_no_callback(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["profiles"] == []

    def test_switch_profile_requires_auth(self, client):
        configure_auth("secret-token")
        resp = client.post("/api/profiles/switch", json={"profile": "general"})
        assert resp.status_code == 401

    def test_switch_profile_success(self, client):
        configure_auth("secret-token")
        stream_state.set_callbacks(on_profile_switch=lambda pid: True)
        resp = client.post("/api/profiles/switch",
                           json={"profile": "general"},
                           headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_switch_profile_failure(self, client):
        configure_auth("secret-token")
        stream_state.set_callbacks(on_profile_switch=lambda pid: False)
        resp = client.post("/api/profiles/switch",
                           json={"profile": "bad"},
                           headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 400

    def test_switch_profile_missing_id(self, client):
        configure_auth("secret-token")
        resp = client.post("/api/profiles/switch",
                           json={},
                           headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Response caching with stale-data fallback
# ---------------------------------------------------------------------------

class TestResponseCaching:
    """Tests for _cached_callback and stale-data fallback on /api/stats and /api/tracks."""

    def test_stale_cache_served_when_callback_raises(self):
        """When a callback raises, the cache should serve last-known-good data."""
        call_count = 0

        def good_then_bad():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"fps": 30.0}
            raise RuntimeError("pipeline busy")

        # First call succeeds and caches
        result = _cached_callback("test_key", good_then_bad)
        assert result == {"fps": 30.0}
        assert "test_key" in _response_cache

        # Second call raises — should return cached data
        result = _cached_callback("test_key", good_then_bad)
        assert result == {"fps": 30.0}

    def test_fresh_data_replaces_cache(self):
        """New successful callback data should replace previous cache entry."""
        _cached_callback("test_key", lambda: {"fps": 10.0})
        assert _response_cache["test_key"][1] == {"fps": 10.0}

        _cached_callback("test_key", lambda: {"fps": 25.0})
        assert _response_cache["test_key"][1] == {"fps": 25.0}

    def test_cache_expires_after_ttl(self, monkeypatch):
        """Stale cache beyond TTL should return None, not stale data."""
        # Seed the cache
        _cached_callback("test_key", lambda: {"fps": 15.0})

        # Advance time beyond TTL
        original_ts = _response_cache["test_key"][0]
        _response_cache["test_key"] = (original_ts - _RESPONSE_CACHE_TTL - 1, {"fps": 15.0})

        # Callback raises — cache is too old, should return None
        result = _cached_callback("test_key", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        assert result is None

    def test_none_callback_returns_cached(self):
        """When callback is None and cache exists, serve cached data."""
        _cached_callback("test_key", lambda: [{"id": 1, "label": "person"}])
        result = _cached_callback("test_key", None)
        assert result == [{"id": 1, "label": "person"}]

    def test_none_callback_no_cache_returns_none(self):
        """When callback is None and no cache exists, return None."""
        result = _cached_callback("test_key", None)
        assert result is None

    def test_stats_endpoint_serves_stale_on_failure(self, client):
        """GET /api/stats should serve cached data when get_stats raises."""
        # Prime the cache via a normal call
        stream_state.update_stats(fps=20.0, camera_ok=True)
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        assert resp.json()["fps"] == 20.0

        # Sabotage get_stats to simulate lock contention / pipeline failure
        original_get_stats = stream_state.get_stats
        stream_state.get_stats = lambda: (_ for _ in ()).throw(RuntimeError("busy"))
        try:
            resp = client.get("/api/stats")
            assert resp.status_code == 200
            # Should serve cached data with fps=20.0
            assert resp.json()["fps"] == 20.0
        finally:
            stream_state.get_stats = original_get_stats

    def test_tracks_endpoint_serves_stale_on_failure(self, client):
        """GET /api/tracks should serve cached data when callback raises."""
        tracks_data = [{"id": 1, "label": "person", "bbox": [10, 20, 100, 200]}]

        call_count = 0

        def tracks_callback():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tracks_data
            raise RuntimeError("pipeline busy")

        stream_state.set_callbacks(get_active_tracks=tracks_callback)

        # First call succeeds and caches
        resp = client.get("/api/tracks")
        assert resp.status_code == 200
        assert resp.json() == tracks_data

        # Second call — callback raises, should serve stale data
        resp = client.get("/api/tracks")
        assert resp.status_code == 200
        assert resp.json() == tracks_data

    def test_tracks_endpoint_returns_empty_when_no_callback_no_cache(self, client):
        """GET /api/tracks with no callback and no cache returns empty list."""
        resp = client.get("/api/tracks")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Malformed-JSON fuzz table — every POST endpoint should return 400 (not 500)
# on bad input.  Auth must be disabled so the body actually gets parsed.
# ---------------------------------------------------------------------------

_POST_ENDPOINTS_TAKING_JSON = [
    # (path, sample_body_or_none_for_skip)
    "/api/config/prompts",
    "/api/config/threshold",
    "/api/config/alert-classes",
    "/api/target/lock",
    "/api/target/strike",
    "/api/vehicle/mode",
    "/api/rtsp/toggle",
    "/api/mavlink-video/toggle",
    "/api/mavlink-video/tune",
    "/api/profiles/switch",
    "/api/rf/start",
    "/api/approach/drop/5",
    "/api/approach/strike/5",
    "/api/stream/quality",
]


class TestMalformedJsonFuzz:
    """Every POST endpoint accepting a body must reject junk input with a 4xx.

    A lax ``< 500`` assertion would pass on a buggy endpoint that silently
    accepts malformed JSON and returns 200 — exactly the regression this
    fuzz is supposed to catch. Anchor the lower bound at 400 so success and
    redirect responses fail the test.
    """

    @pytest.mark.parametrize("path", _POST_ENDPOINTS_TAKING_JSON)
    def test_malformed_json_returns_4xx(self, client, path):
        resp = client.post(
            path,
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        # 400 (malformed body), 401/403 (auth), 413 (oversize), 422 (schema).
        # Never a 2xx/3xx (silent accept) and never a 5xx (crash).
        assert 400 <= resp.status_code < 500, (
            f"{path} returned {resp.status_code} on malformed JSON"
        )

    @pytest.mark.parametrize("path", _POST_ENDPOINTS_TAKING_JSON)
    def test_non_utf8_body_returns_4xx(self, client, path):
        resp = client.post(
            path,
            content=b"\xff\xfe\x00invalid-utf8",
            headers={"Content-Type": "application/json"},
        )
        assert 400 <= resp.status_code < 500, (
            f"{path} returned {resp.status_code} on non-UTF8 body"
        )

    @pytest.mark.parametrize("path", _POST_ENDPOINTS_TAKING_JSON)
    def test_empty_body_returns_4xx(self, client, path):
        resp = client.post(path, content=b"", headers={"Content-Type": "application/json"})
        assert 400 <= resp.status_code < 500, (
            f"{path} returned {resp.status_code} on empty body"
        )


# ---------------------------------------------------------------------------
# /auth/login rate-limiting
# ---------------------------------------------------------------------------

class TestAuthLoginRateLimit:
    def test_login_succeeds_with_correct_password(self, client):
        configure_web_password("good-pw")
        resp = client.post("/auth/login", json={"password": "good-pw"})
        assert resp.status_code == 200

    def test_login_rejects_wrong_password(self, client):
        configure_web_password("good-pw")
        resp = client.post("/auth/login", json={"password": "wrong"})
        assert resp.status_code == 401

    def test_login_missing_password_returns_400(self, client):
        configure_web_password("good-pw")
        resp = client.post("/auth/login", json={})
        assert resp.status_code == 400

    def test_rate_limit_after_50_failures(self, client):
        """51st consecutive wrong-password attempt must return 429."""
        from hydra_detect.web.server import _AUTH_FAIL_MAX
        configure_web_password("good-pw")
        # Fire _AUTH_FAIL_MAX bad attempts
        for _ in range(_AUTH_FAIL_MAX):
            resp = client.post("/auth/login", json={"password": "wrong"})
            assert resp.status_code == 401
        # Next attempt — even with correct password — must be 429
        resp = client.post("/auth/login", json={"password": "good-pw"})
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Approach-mode endpoints (follow / drop / strike / pixel_lock / abort)
# ---------------------------------------------------------------------------

class TestApproachEndpoints:
    def test_approach_status_no_callback(self, client):
        resp = client.get("/api/approach/status")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "idle"

    def test_approach_status_with_callback(self, client):
        stream_state.set_callbacks(
            get_approach_status=lambda: {"mode": "follow", "active": True},
        )
        resp = client.get("/api/approach/status")
        assert resp.json() == {"mode": "follow", "active": True}

    def test_follow_unavailable_when_no_callback(self, client):
        resp = client.post("/api/approach/follow/5")
        assert resp.status_code == 503

    def test_follow_success(self, client):
        stream_state.set_callbacks(on_follow_command=lambda tid: True)
        resp = client.post("/api/approach/follow/5")
        assert resp.status_code == 200
        assert resp.json()["track_id"] == 5
        assert resp.json()["mode"] == "follow"

    def test_follow_failure(self, client):
        stream_state.set_callbacks(on_follow_command=lambda tid: False)
        resp = client.post("/api/approach/follow/5")
        assert resp.status_code == 503

    def test_drop_requires_confirm(self, client):
        stream_state.set_callbacks(on_drop_command=lambda tid: True)
        # Missing confirm
        resp = client.post("/api/approach/drop/5", json={})
        assert resp.status_code == 400
        # confirm=false
        resp = client.post("/api/approach/drop/5", json={"confirm": False})
        assert resp.status_code == 400

    def test_drop_happy_path(self, client):
        stream_state.set_callbacks(on_drop_command=lambda tid: True)
        resp = client.post("/api/approach/drop/5", json={"confirm": True})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "drop"

    def test_approach_strike_requires_confirm(self, client):
        stream_state.set_callbacks(on_approach_strike_command=lambda tid: True)
        resp = client.post("/api/approach/strike/5", json={})
        assert resp.status_code == 400

    def test_approach_strike_happy_path(self, client):
        stream_state.set_callbacks(on_approach_strike_command=lambda tid: True)
        resp = client.post("/api/approach/strike/5", json={"confirm": True})
        assert resp.status_code == 200

    def test_pixel_lock_no_body_required(self, client):
        stream_state.set_callbacks(on_pixel_lock_command=lambda tid: True)
        resp = client.post("/api/approach/pixel_lock/5")
        assert resp.status_code == 200

    def test_pixel_lock_failure_returns_503(self, client):
        stream_state.set_callbacks(on_pixel_lock_command=lambda tid: False)
        resp = client.post("/api/approach/pixel_lock/5")
        assert resp.status_code == 503

    def test_abort_no_callback_is_handled(self, client):
        """Abort must not crash even when no callback is wired."""
        resp = client.post("/api/approach/abort")
        # 503 (no controller) or 200 (ok) — never an unhandled 500
        assert resp.status_code in (200, 503)


# ---------------------------------------------------------------------------
# RF start / stop endpoints
# ---------------------------------------------------------------------------

class TestRfEndpoints:
    def test_rf_start_missing_callback(self, client):
        resp = client.post("/api/rf/start", json={})
        assert resp.status_code == 503

    def test_rf_start_validates_mode(self, client):
        stream_state.set_callbacks(on_rf_start=lambda body: True)
        resp = client.post("/api/rf/start", json={"mode": "bogus"})
        assert resp.status_code == 400

    def test_rf_start_wifi_requires_bssid(self, client):
        stream_state.set_callbacks(on_rf_start=lambda body: True)
        resp = client.post("/api/rf/start", json={"mode": "wifi"})
        assert resp.status_code == 400

    def test_rf_start_bssid_format_validated(self, client):
        stream_state.set_callbacks(on_rf_start=lambda body: True)
        resp = client.post(
            "/api/rf/start",
            json={"mode": "wifi", "target_bssid": "not-a-mac"},
        )
        assert resp.status_code == 400

    def test_rf_start_freq_range_validated(self, client):
        stream_state.set_callbacks(on_rf_start=lambda body: True)
        resp = client.post(
            "/api/rf/start",
            json={"mode": "sdr", "target_freq_mhz": 99999.0},
        )
        assert resp.status_code == 400

    def test_rf_start_happy_path_sdr(self, client):
        stream_state.set_callbacks(on_rf_start=lambda body: True)
        resp = client.post(
            "/api/rf/start",
            json={"mode": "sdr", "target_freq_mhz": 2437.0},
        )
        assert resp.status_code == 200

    def test_rf_stop_no_callback(self, client):
        resp = client.post("/api/rf/stop")
        assert resp.status_code == 503

    def test_rf_stop_happy_path(self, client):
        stream_state.set_callbacks(on_rf_stop=lambda: None)
        resp = client.post("/api/rf/stop")
        assert resp.status_code == 200

    def test_rf_status_read(self, client):
        resp = client.get("/api/rf/status")
        # Auth-free read — never 5xx on empty pipeline
        assert resp.status_code < 500


# ---------------------------------------------------------------------------
# Streaming endpoints — /stream.jpg (snapshot polling)
# ---------------------------------------------------------------------------

class TestStreamingEndpoints:
    def test_stream_jpg_no_frame(self, client):
        """With no frame published, /stream.jpg returns a valid response (never 5xx)."""
        resp = client.get("/stream.jpg")
        # 200 (placeholder), 204 (no content), 404, or 503 — never an unhandled 500
        assert resp.status_code in (200, 204, 404, 503)

    def test_stream_jpg_returns_jpeg_content_type_when_frame_set(self, client):
        import numpy as np
        # Publish a tiny test frame
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        stream_state.update_frame(frame)
        resp = client.get("/stream.jpg")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        # JPEG magic bytes
        assert resp.content[:3] == b"\xff\xd8\xff"

    def test_stream_quality_post_accepts_preferences(self, client):
        resp = client.post("/api/stream/quality", json={"quality": 70})
        assert resp.status_code < 500
