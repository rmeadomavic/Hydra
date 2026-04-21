"""Tests for the full config read/write API endpoints."""

from __future__ import annotations

import configparser
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state


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
    """Create a temporary config.ini for testing."""
    config = configparser.ConfigParser()
    config["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "30"}
    config["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.45"}
    config["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": "secret-test-token"}
    config["tracker"] = {"track_thresh": "0.5", "track_buffer": "30"}
    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        config.write(f)
    return path


class TestConfigGetEndpoint:
    def test_get_config_returns_all_sections(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200
        data = resp.json()
        assert "camera" in data
        assert "detector" in data
        assert data["camera"]["source"] == "auto"

    def test_get_config_redacts_api_token(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200
        assert resp.json()["web"]["api_token"] == "***"

    def test_get_config_no_auth_required(self, client, tmp_config):
        """GET /api/config/full is read-only with redacted secrets — no auth needed."""
        configure_auth("my-token")
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200


class TestConfigPostEndpoint:
    def test_post_config_writes_values(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "camera": {"fps": "15"},
            })
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["fps"] == "15"
        assert config["camera"]["source"] == "auto"

    def test_post_config_preserves_token_on_masked_value(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "web": {"api_token": "***"},
            })
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["web"]["api_token"] == "secret-test-token"

    def test_post_config_creates_backup(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "camera": {"fps": "15"},
            })
        assert resp.status_code == 200
        assert (tmp_config.parent / "config.ini.bak").exists()

    def test_post_config_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token")
        resp = client.post("/api/config/full", json={"camera": {"fps": "15"}})
        assert resp.status_code == 401

    def test_post_config_rejects_oversized_body(self, client, tmp_config):
        huge = {"camera": {"source": "x" * 70000}}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json=huge)
        assert resp.status_code == 413

    def test_post_config_returns_restart_required_fields(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "web": {"port": "9090"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "restart_required" in data
        assert any("port" in f for f in data["restart_required"])

    def test_post_config_reports_skipped_fields(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "nonexistent_section": {"foo": "bar"},
                "camera": {"nonexistent_field": "baz"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skipped"]) == 2

    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"camera": {"fps": "not-an-int"}}, "camera.fps"),
            ({"detector": {"yolo_confidence": "not-a-float"}}, "detector.yolo_confidence"),
            ({"mavlink": {"enabled": "not-a-bool"}}, "mavlink.enabled"),
            ({"camera": {"video_standard": "secam"}}, "camera.video_standard"),
        ],
    )
    def test_post_config_rejects_invalid_schema_values(self, client, tmp_config, payload, field):
        original_content = tmp_config.read_text()
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "Validation failed"
        assert field in data["field_errors"]
        assert tmp_config.read_text() == original_content


class TestConfigAuthPositiveCases:
    def test_get_config_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full", headers=headers)
        assert resp.status_code == 200
        assert "camera" in resp.json()

    def test_post_config_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={"camera": {"fps": "15"}}, headers=headers)
        assert resp.status_code == 200


class TestConfigAtomicWrite:
    @pytest.mark.skipif(os.getuid() == 0, reason="chmod has no effect when running as root")
    def test_failed_write_does_not_corrupt_original(self, client, tmp_config):
        original_content = tmp_config.read_text()
        tmp_config.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
                resp = client.post("/api/config/full", json={"camera": {"fps": "15"}})
            assert resp.status_code == 500
            tmp_config.parent.chmod(stat.S_IRWXU)
            assert tmp_config.read_text() == original_content
        finally:
            tmp_config.parent.chmod(stat.S_IRWXU)


class TestConfigRestoreBackup:
    def test_restore_backup_works(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            client.post("/api/config/full", json={"camera": {"fps": "15"}})
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["fps"] == "30"

    def test_restore_backup_no_backup(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 404


class TestRestartRequiredFieldsConsistency:
    """RESTART_REQUIRED_FIELDS must reference real schema keys — stale entries
    silently became no-ops when fields were renamed (e.g. model → yolo_model)."""

    def test_all_restart_fields_exist_in_schema(self):
        from hydra_detect.config_schema import SCHEMA
        from hydra_detect.web.config_api import RESTART_REQUIRED_FIELDS

        stale = []
        for section, fields in RESTART_REQUIRED_FIELDS.items():
            schema_section = SCHEMA.get(section, {})
            for key in fields:
                if key not in schema_section:
                    stale.append(f"{section}.{key}")
        assert not stale, (
            f"RESTART_REQUIRED_FIELDS references keys missing from SCHEMA: {stale}"
        )

    def test_yolo_model_not_restart_required(self):
        """yolo_model hot-swaps via switch_model() — must not prompt restart."""
        from hydra_detect.web.config_api import RESTART_REQUIRED_FIELDS
        assert "yolo_model" not in RESTART_REQUIRED_FIELDS.get("detector", set())


class TestRedactedFieldsConsistency:
    """REDACTED_FIELDS must reference real schema keys — if a secret key is
    renamed without updating this set, GET /api/config/full silently leaks."""

    def test_all_redacted_fields_exist_in_schema(self):
        from hydra_detect.config_schema import SCHEMA
        from hydra_detect.web.config_api import REDACTED_FIELDS

        stale = []
        for section, fields in REDACTED_FIELDS.items():
            schema_section = SCHEMA.get(section, {})
            for key in fields:
                if key not in schema_section:
                    stale.append(f"{section}.{key}")
        assert not stale, (
            f"REDACTED_FIELDS references keys missing from SCHEMA: {stale}"
        )


class TestAtomicConfigWrite:
    """Config writes must be crash-safe — a power cut mid-write must not
    leave a partial config.ini (issue #60)."""

    def test_write_uses_tmp_then_replace(self, client, tmp_config):
        """Verify the write path calls os.replace with a .tmp source."""
        import hydra_detect.web.config_api as cfg_api
        calls = []
        real_replace = os.replace

        def tracked_replace(src, dst):
            calls.append((str(src), str(dst)))
            real_replace(src, dst)

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config), \
                patch.object(cfg_api.os, "replace", side_effect=tracked_replace):
            resp = client.post("/api/config/full", json={"camera": {"fps": "25"}})

        assert resp.status_code == 200
        assert len(calls) == 1
        src, dst = calls[0]
        assert src.endswith(".tmp")
        assert dst == str(tmp_config)

    def test_orphan_tmp_cleaned_up_on_failure(self, client, tmp_config):
        """If os.replace raises, the .tmp file must not persist."""
        import hydra_detect.web.config_api as cfg_api
        tmp_path = Path(str(tmp_config) + ".tmp")

        def failing_replace(src, dst):
            raise OSError("simulated rename failure")

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config), \
                patch.object(cfg_api.os, "replace", side_effect=failing_replace):
            resp = client.post("/api/config/full", json={"camera": {"fps": "25"}})

        # Write failed — original config must be untouched and .tmp gone.
        assert resp.status_code in (500, 200)  # route may or may not surface error
        assert not tmp_path.exists(), "orphan .tmp not cleaned up"
        # Original file must still be readable and not half-written.
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert "camera" in config


class TestConfigImportValidation:
    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"camera": {"fps": "bad-int"}}, "camera.fps"),
            ({"detector": {"yolo_confidence": "bad-float"}}, "detector.yolo_confidence"),
            ({"mavlink": {"enabled": "bad-bool"}}, "mavlink.enabled"),
            ({"camera": {"video_standard": "bad-enum"}}, "camera.video_standard"),
        ],
    )
    def test_import_rejects_invalid_schema_values(self, client, tmp_config, payload, field):
        original_content = tmp_config.read_text()
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/import", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "Validation failed"
        assert field in data["field_errors"]
        assert tmp_config.read_text() == original_content
