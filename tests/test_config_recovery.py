"""Tests for config recovery endpoints — issue #75.

Three controls:
  1. POST /api/config/factory-reset — restore config.ini.factory over config.ini
  2. GET  /api/config/export        — download current config as JSON
  3. POST /api/config/import        — upload JSON to restore

All must:
  - require auth when api_token is configured (Bearer or same-origin)
  - audit-log every attempt (success AND failure)
  - validate against schema before writing (import only)
  - never corrupt config.ini on partial/invalid input
"""

from __future__ import annotations

import configparser
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_state():
    configure_auth(None)
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tmp_config(tmp_path):
    """Create a tmp config.ini with a matching .factory snapshot."""
    cfg = configparser.ConfigParser()
    cfg["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "30"}
    cfg["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.45"}
    cfg["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": "secret-test-token"}
    cfg["tracker"] = {"track_thresh": "0.5", "track_buffer": "30"}
    cfg["mavlink"] = {"enabled": "true", "connection_string": "udp:127.0.0.1:14550"}

    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        cfg.write(f)

    # Factory snapshot — slightly different from config.ini so we can prove
    # restore actually swapped values rather than just no-oping.
    factory = configparser.ConfigParser()
    factory["camera"] = {"source": "auto", "width": "1280", "height": "720", "fps": "30"}
    factory["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.5"}
    factory["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": "factory-token"}
    factory["tracker"] = {"track_thresh": "0.5", "track_buffer": "30"}
    factory["mavlink"] = {"enabled": "true", "connection_string": "udp:127.0.0.1:14550"}
    factory_path = tmp_path / "config.ini.factory"
    with open(factory_path, "w") as f:
        factory.write(f)
    return path


@pytest.fixture
def tmp_config_no_factory(tmp_path):
    """A config.ini with NO sibling .factory file — exercises the failure path."""
    cfg = configparser.ConfigParser()
    cfg["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "30"}
    cfg["web"] = {"host": "0.0.0.0", "port": "8080"}
    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        cfg.write(f)
    return path


# ── Factory Reset ────────────────────────────────────────────────────


class TestFactoryReset:
    def test_factory_reset_restores_known_good(self, client, tmp_config):
        """Calling factory-reset must overwrite config.ini with .factory contents."""
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 200, resp.text

        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        # Factory had width=1280; original had 640 — proves the swap happened.
        assert cfg["camera"]["width"] == "1280"
        assert cfg["camera"]["height"] == "720"

    def test_factory_reset_audited(self, client, tmp_config, caplog):
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            with patch(
                "hydra_detect.web.config_api.get_config_path",
                return_value=tmp_config,
            ):
                resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 200
        audit_records = [r for r in caplog.records if r.name == "hydra.audit"]
        assert any(
            "config_factory_reset" in r.getMessage() and "outcome=ok" in r.getMessage()
            for r in audit_records
        ), f"audit log missing factory reset entry: {[r.getMessage() for r in audit_records]}"

    def test_factory_reset_missing_factory_file_returns_503(
        self, client, tmp_config_no_factory, caplog,
    ):
        """If config.ini.factory is absent, return a clear error and DON'T
        corrupt the existing config."""
        original = tmp_config_no_factory.read_text()
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            with patch(
                "hydra_detect.web.config_api.get_config_path",
                return_value=tmp_config_no_factory,
            ):
                resp = client.post("/api/config/factory-reset")
        # 503 (or 5xx) — clearly a service-state failure, not a client error.
        assert resp.status_code in (500, 503), resp.text
        assert tmp_config_no_factory.read_text() == original
        # Audit even on rejection — instructors need to see students' attempts.
        audit_records = [r for r in caplog.records if r.name == "hydra.audit"]
        assert any(
            "config_factory_reset" in r.getMessage()
            and "outcome=no_factory_file" in r.getMessage()
            for r in audit_records
        )

    def test_factory_reset_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token")
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 401

    def test_factory_reset_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post("/api/config/factory-reset", headers=headers)
        assert resp.status_code == 200

    def test_factory_reset_same_origin_bypasses_bearer(self, client, tmp_config):
        """Dashboard requests carry an Origin matching the request URL — they
        should authenticate without a Bearer token."""
        configure_auth("my-token")
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/factory-reset",
                headers={"Origin": "http://testserver"},
            )
        assert resp.status_code == 200


# ── Export ───────────────────────────────────────────────────────────


class TestConfigExport:
    def test_export_returns_current_config(self, client, tmp_config):
        # First mutate so we have a known recent value to round-trip.
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            client.post("/api/config/full", json={"camera": {"fps": "12"}})
            resp = client.get("/api/config/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["camera"]["fps"] == "12"
        assert "web" in data

    def test_export_redacts_secrets(self, client, tmp_config):
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/export")
        assert resp.status_code == 200
        assert resp.json()["web"]["api_token"] == "***"

    def test_export_filename_header_set(self, client, tmp_config):
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/export")
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd.lower()
        assert "filename=" in cd
        assert ".json" in cd
        assert "hydra-config-" in cd

    def test_export_audited(self, client, tmp_config, caplog):
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            with patch(
                "hydra_detect.web.config_api.get_config_path",
                return_value=tmp_config,
            ):
                resp = client.get("/api/config/export")
        assert resp.status_code == 200
        audit = [r.getMessage() for r in caplog.records if r.name == "hydra.audit"]
        assert any("config_export" in m for m in audit), audit


# ── Import ───────────────────────────────────────────────────────────


class TestConfigImport:
    def test_import_valid_config_sticks(self, client, tmp_config):
        payload = {"camera": {"fps": "20", "width": "800"}}
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post("/api/config/import", json=payload)
        assert resp.status_code == 200, resp.text

        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        assert cfg["camera"]["fps"] == "20"
        assert cfg["camera"]["width"] == "800"

    def test_import_invalid_schema_returns_400(self, client, tmp_config):
        original = tmp_config.read_text()
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"camera": {"fps": "not-an-int"}},
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "Validation failed"
        assert "camera.fps" in body["field_errors"]
        # Config must be untouched.
        assert tmp_config.read_text() == original

    def test_import_audited(self, client, tmp_config, caplog):
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            with patch(
                "hydra_detect.web.config_api.get_config_path",
                return_value=tmp_config,
            ):
                resp = client.post(
                    "/api/config/import",
                    json={"camera": {"fps": "21"}},
                )
        assert resp.status_code == 200
        audit = [r.getMessage() for r in caplog.records if r.name == "hydra.audit"]
        assert any(
            "config_import" in m and "outcome=ok" in m for m in audit
        ), audit

    def test_import_validation_failure_is_audited(self, client, tmp_config, caplog):
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            with patch(
                "hydra_detect.web.config_api.get_config_path",
                return_value=tmp_config,
            ):
                resp = client.post(
                    "/api/config/import",
                    json={"camera": {"fps": "garbage"}},
                )
        assert resp.status_code == 400
        audit = [r.getMessage() for r in caplog.records if r.name == "hydra.audit"]
        assert any(
            "config_import" in m and "validation_failed" in m for m in audit
        ), audit

    def test_import_malformed_json_returns_400(self, client, tmp_config, caplog):
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            with patch(
                "hydra_detect.web.config_api.get_config_path",
                return_value=tmp_config,
            ):
                resp = client.post(
                    "/api/config/import",
                    content=b"{not json at all",
                    headers={"Content-Type": "application/json"},
                )
        assert resp.status_code == 400
        audit = [r.getMessage() for r in caplog.records if r.name == "hydra.audit"]
        assert any(
            "config_import" in m and "malformed_json" in m for m in audit
        ), audit

    def test_import_non_object_body_returns_400(self, client, tmp_config):
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post("/api/config/import", json=["not", "an", "object"])
        assert resp.status_code == 400

    def test_import_oversized_body_rejected(self, client, tmp_config):
        # Bigger than MAX_BODY_SIZE (64 KB).
        huge = {"camera": {"source": "x" * 70000}}
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post("/api/config/import", json=huge)
        assert resp.status_code == 413

    def test_import_preserves_unknown_keys(self, client, tmp_config):
        """A partial import (only [camera]) must NOT wipe other sections.
        The dashboard's [ui]/[web] keys (e.g. hud_layout from B8) must
        survive a partial config import."""
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import", json={"camera": {"fps": "11"}},
            )
        assert resp.status_code == 200

        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        # camera updated
        assert cfg["camera"]["fps"] == "11"
        # other sections still present (write_config never deletes sections)
        assert "web" in cfg
        assert cfg["web"]["host"] == "0.0.0.0"
        assert "tracker" in cfg

    def test_import_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token")
        resp = client.post("/api/config/import", json={"camera": {"fps": "15"}})
        assert resp.status_code == 401

    def test_import_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"camera": {"fps": "15"}},
                headers=headers,
            )
        assert resp.status_code == 200

    def test_import_same_origin_bypasses_bearer(self, client, tmp_config):
        configure_auth("my-token")
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"camera": {"fps": "16"}},
                headers={"Origin": "http://testserver"},
            )
        assert resp.status_code == 200


# ── Round-trip ───────────────────────────────────────────────────────


class TestRoundTrip:
    def test_export_then_import_preserves_values(self, client, tmp_config):
        # Set a value, export, mutate, re-import the export, confirm restore.
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            client.post("/api/config/full", json={"camera": {"fps": "27"}})

            export = client.get("/api/config/export").json()
            assert export["camera"]["fps"] == "27"

            client.post("/api/config/full", json={"camera": {"fps": "5"}})

            # Strip redacted secrets — the export sends "***" placeholders;
            # write_config preserves the existing value when it sees those.
            resp = client.post("/api/config/import", json=export)
            assert resp.status_code == 200, resp.text

        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        assert cfg["camera"]["fps"] == "27"
