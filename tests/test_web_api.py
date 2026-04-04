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
            assert resp.status_code == 401, f"{url} should require auth despite forged sec-fetch-site"

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


class TestStaticFileServing:
    def test_css_variables_served(self, client):
        resp = client.get("/static/css/variables.css")
        assert resp.status_code == 200
        assert "ogt-green" in resp.text

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
