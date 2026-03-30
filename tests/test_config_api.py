"""Tests for the full config read/write API endpoints."""

from __future__ import annotations

import configparser
import stat
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
