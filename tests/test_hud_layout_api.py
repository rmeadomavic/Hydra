"""Tests for /api/settings/hud_layout (Phase 2 B8).

Coverage:
  - GET returns the schema default on a fresh config
  - GET reflects a value already persisted in [web]
  - POST round-trips through write_config with atomic-write semantics
  - POST returns 400 on malformed JSON (via _parse_json)
  - POST returns 400 on schema-rejected values
  - POST rejects non-string and missing hud_layout fields with 400
  - Same-origin requests bypass Bearer auth even when a token is set
  - External callers without Bearer get 401 when auth is configured
  - Bearer-authenticated external calls succeed
"""

from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.config_schema import SCHEMA
from hydra_detect.web.server import app, configure_auth, stream_state


@pytest.fixture(autouse=True)
def _reset_state():
    """Drop any auth/callback state stashed by other tests."""
    configure_auth(None)
    stream_state._callbacks.clear()
    yield
    configure_auth(None)


@pytest.fixture
def client():
    return TestClient(app)


def _write_config(path: Path, web_section: dict[str, str] | None = None) -> None:
    cfg = configparser.ConfigParser()
    cfg["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "30"}
    cfg["web"] = {"host": "0.0.0.0", "port": "8080"}
    if web_section:
        cfg["web"].update(web_section)
    with open(path, "w") as f:
        cfg.write(f)


@pytest.fixture
def tmp_config(tmp_path):
    path = tmp_path / "config.ini"
    _write_config(path)
    return path


@pytest.fixture
def tmp_config_with_token(tmp_path):
    path = tmp_path / "config.ini"
    _write_config(path, {"api_token": "secret-test-token"})
    return path


# ---------------------------------------------------------------------------
# Schema sanity — defends the contract the endpoint relies on
# ---------------------------------------------------------------------------

class TestSchemaContract:
    def test_hud_layout_field_present(self):
        assert "hud_layout" in SCHEMA["web"]

    def test_hud_layout_choices_match_known_presets(self):
        spec = SCHEMA["web"]["hud_layout"]
        assert spec.choices is not None
        assert set(spec.choices) >= {"classic", "operator", "graphs", "hybrid"}

    def test_hud_layout_default_is_a_valid_choice(self):
        spec = SCHEMA["web"]["hud_layout"]
        assert spec.default in (spec.choices or [])


# ---------------------------------------------------------------------------
# GET behaviour
# ---------------------------------------------------------------------------

class TestGetHudLayout:
    def test_returns_schema_default_when_unset(self, client, tmp_path):
        # Config with no [web] hud_layout key — endpoint must fall back
        # to the schema default rather than 500.
        path = tmp_path / "config.ini"
        _write_config(path)  # no hud_layout in [web]
        with patch("hydra_detect.web.config_api.get_config_path", return_value=path), \
             patch("hydra_detect.web.server.read_config", side_effect=__import__(
                 "hydra_detect.web.config_api", fromlist=["read_config"]
             ).read_config):
            resp = client.get("/api/settings/hud_layout")
        assert resp.status_code == 200
        body = resp.json()
        assert body["hud_layout"] == SCHEMA["web"]["hud_layout"].default
        assert "classic" in body["choices"]
        assert body["default"] == SCHEMA["web"]["hud_layout"].default

    def test_returns_persisted_value(self, client, tmp_path):
        path = tmp_path / "config.ini"
        _write_config(path, {"hud_layout": "operator"})
        with patch("hydra_detect.web.config_api.get_config_path", return_value=path):
            resp = client.get("/api/settings/hud_layout")
        assert resp.status_code == 200
        assert resp.json()["hud_layout"] == "operator"

    def test_corrupt_value_falls_back_to_default(self, client, tmp_path):
        # Direct file edit could leave a bogus value; endpoint should not
        # echo it back as if it were valid.
        path = tmp_path / "config.ini"
        _write_config(path, {"hud_layout": "bogus-not-a-real-preset"})
        with patch("hydra_detect.web.config_api.get_config_path", return_value=path):
            resp = client.get("/api/settings/hud_layout")
        assert resp.status_code == 200
        assert resp.json()["hud_layout"] == SCHEMA["web"]["hud_layout"].default

    def test_get_no_auth_required_even_with_token(self, client, tmp_config_with_token):
        configure_auth("my-token")
        path_patch = patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_token,
        )
        with path_patch:
            resp = client.get("/api/settings/hud_layout")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST round-trip + validation
# ---------------------------------------------------------------------------

class TestPostHudLayout:
    def test_round_trips_via_get(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            post = client.post("/api/settings/hud_layout", json={"hud_layout": "operator"})
            assert post.status_code == 200
            assert post.json()["hud_layout"] == "operator"

            get = client.get("/api/settings/hud_layout")
            assert get.status_code == 200
            assert get.json()["hud_layout"] == "operator"

        # Verify the config file actually got updated (atomic write path).
        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        assert cfg["web"]["hud_layout"] == "operator"

    def test_malformed_json_returns_400(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post(
                "/api/settings/hud_layout",
                content=b"{ this is not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_schema_rejects_unknown_preset(self, client, tmp_config):
        original = tmp_config.read_text()
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post(
                "/api/settings/hud_layout",
                json={"hud_layout": "definitely-not-a-real-layout"},
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("error") == "Validation failed"
        assert "web.hud_layout" in body.get("field_errors", {})
        # Persistence must not have run on a rejected payload.
        assert tmp_config.read_text() == original

    def test_missing_hud_layout_key_returns_400(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/settings/hud_layout", json={})
        assert resp.status_code == 400

    def test_non_string_hud_layout_returns_400(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/settings/hud_layout", json={"hud_layout": 42})
        assert resp.status_code == 400

    def test_non_object_body_returns_400(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/settings/hud_layout", json=["operator"])
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth model — same-origin bypass + Bearer for external callers
# ---------------------------------------------------------------------------

class TestPostHudLayoutAuth:
    def test_same_origin_bypasses_bearer(self, client, tmp_config_with_token):
        """Dashboard calls with matching Origin must work without a token.

        Mirrors the auth-model contract described in CLAUDE.md and the
        behaviour of every other settings POST endpoint.
        """
        configure_auth("my-token")
        path_patch = patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_token,
        )
        with path_patch:
            resp = client.post(
                "/api/settings/hud_layout",
                json={"hud_layout": "graphs"},
                headers={"Origin": "http://testserver"},
            )
        assert resp.status_code == 200
        assert resp.json()["hud_layout"] == "graphs"

    def test_external_caller_without_bearer_gets_401(self, client, tmp_config_with_token):
        configure_auth("my-token")
        path_patch = patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_token,
        )
        with path_patch:
            resp = client.post(
                "/api/settings/hud_layout",
                json={"hud_layout": "operator"},
            )
        assert resp.status_code == 401

    def test_external_caller_with_bearer_succeeds(self, client, tmp_config_with_token):
        configure_auth("my-token")
        path_patch = patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_token,
        )
        with path_patch:
            resp = client.post(
                "/api/settings/hud_layout",
                json={"hud_layout": "hybrid"},
                headers={"Authorization": "Bearer my-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["hud_layout"] == "hybrid"


# ---------------------------------------------------------------------------
# Migration 004 — old configs gain the key on next load
# ---------------------------------------------------------------------------

class TestMigration004:
    def test_migration_inserts_default_when_absent(self, tmp_path, monkeypatch):
        from hydra_detect import config_migrate as cm

        path = tmp_path / "config.ini"
        cfg = configparser.ConfigParser()
        cfg["meta"] = {"schema_version": "3"}
        cfg["web"] = {"host": "0.0.0.0", "port": "8080"}
        # Provide minimal sections so other migrations are no-ops.
        cfg["guidance"] = {
            "forward_predictor_enabled": "true",
            "forward_predictor_horizon_s": "0.5",
            "attitude_compensation_enabled": "true",
            "gimbal_stabilized": "false",
        }
        with open(path, "w") as f:
            cfg.write(f)

        result = cm.run_migrations(path)
        assert result.to_version == cm.CURRENT_SCHEMA_VERSION
        assert any("004" in stem for stem in result.applied)

        cfg_after = configparser.ConfigParser()
        cfg_after.read(path)
        assert cfg_after["web"]["hud_layout"] == "classic"

    def test_migration_preserves_existing_value(self, tmp_path):
        from hydra_detect import config_migrate as cm

        path = tmp_path / "config.ini"
        cfg = configparser.ConfigParser()
        cfg["meta"] = {"schema_version": "3"}
        cfg["web"] = {"host": "0.0.0.0", "port": "8080", "hud_layout": "operator"}
        cfg["guidance"] = {
            "forward_predictor_enabled": "true",
            "forward_predictor_horizon_s": "0.5",
            "attitude_compensation_enabled": "true",
            "gimbal_stabilized": "false",
        }
        with open(path, "w") as f:
            cfg.write(f)

        cm.run_migrations(path)

        cfg_after = configparser.ConfigParser()
        cfg_after.read(path)
        assert cfg_after["web"]["hud_layout"] == "operator"
