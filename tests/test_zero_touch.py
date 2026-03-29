"""Tests for zero-touch boot and student experience features (PR 11)."""

from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
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
    config["camera"] = {"source": "auto", "width": "640", "height": "480"}
    config["detector"] = {"yolo_model": "yolov8n.pt", "yolo_confidence": "0.45"}
    config["mavlink"] = {"connection_string": "/dev/ttyTHS1", "baud": "921600"}
    config["tak"] = {"callsign": "HYDRA-1", "enabled": "true"}
    config["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": ""}
    config["tracker"] = {"track_thresh": "0.5"}
    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        config.write(f)
    return path


@pytest.fixture
def tmp_factory(tmp_config):
    """Create a factory defaults file next to the config."""
    factory_path = Path(str(tmp_config) + ".factory")
    config = configparser.ConfigParser()
    config["camera"] = {"source": "auto", "width": "640", "height": "480"}
    config["detector"] = {"yolo_model": "yolov8n.pt", "yolo_confidence": "0.45"}
    config["mavlink"] = {"connection_string": "/dev/ttyTHS1", "baud": "921600"}
    config["tak"] = {"callsign": "HYDRA-FACTORY", "enabled": "true"}
    config["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": ""}
    config["tracker"] = {"track_thresh": "0.5"}
    with open(factory_path, "w") as f:
        config.write(f)
    return factory_path


# ── Setup Page ────────────────────────────────────────────────

class TestSetupPage:
    def test_setup_page_returns_200(self, client):
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert "Setup" in resp.text

    def test_setup_page_contains_form_elements(self, client):
        resp = client.get("/setup")
        assert "setup-camera" in resp.text
        assert "setup-serial" in resp.text
        assert "setup-vehicle" in resp.text
        assert "setup-team" in resp.text


# ── Setup Devices API ─────────────────────────────────────────

class TestSetupDevices:
    def test_devices_returns_cameras_and_serial(self, client):
        with patch("glob.glob") as mock_glob:
            def glob_side_effect(pattern):
                if "video" in pattern:
                    return ["/dev/video0", "/dev/video1"]
                if "tty" in pattern:
                    return ["/dev/ttyACM0", "/dev/ttyTHS1", "/dev/ttyS0"]
                return []
            mock_glob.side_effect = glob_side_effect
            resp = client.get("/api/setup/devices")

        assert resp.status_code == 200
        data = resp.json()
        assert "cameras" in data
        assert "serial_ports" in data
        assert len(data["cameras"]) == 2
        # ttyS0 should be filtered out
        assert len(data["serial_ports"]) == 2
        assert data["serial_ports"][0]["path"] == "/dev/ttyACM0"

    def test_devices_empty_when_none_found(self, client):
        with patch("glob.glob", return_value=[]):
            resp = client.get("/api/setup/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cameras"] == []
        assert data["serial_ports"] == []


# ── Setup Save API ─────────────────────────────────────────────

class TestSetupSave:
    def test_save_writes_config(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/setup/save", json={
                "camera_source": "/dev/video2",
                "serial_port": "/dev/ttyACM0",
                "vehicle_type": "usv",
                "team_number": "3",
                "callsign": "",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "saved"
        assert data["callsign"] == "HYDRA-3-USV"

        # Verify config was written
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["source"] == "/dev/video2"
        assert config["mavlink"]["connection_string"] == "/dev/ttyACM0"
        assert config["tak"]["callsign"] == "HYDRA-3-USV"

    def test_save_with_explicit_callsign(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/setup/save", json={
                "camera_source": "auto",
                "serial_port": "/dev/ttyTHS1",
                "vehicle_type": "drone",
                "team_number": "1",
                "callsign": "MY-CUSTOM-NAME",
            })
        assert resp.status_code == 200
        assert resp.json()["callsign"] == "MY-CUSTOM-NAME"

    def test_save_triggers_restart_callback(self, client, tmp_config):
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/setup/save", json={
                "camera_source": "auto",
                "serial_port": "/dev/ttyTHS1",
            })
        assert resp.status_code == 200
        restart_cb.assert_called_once()

    def test_save_rejects_invalid_vehicle_type(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/setup/save", json={
                "camera_source": "auto",
                "serial_port": "/dev/ttyTHS1",
                "vehicle_type": "submarine",
            })
        assert resp.status_code == 400

    def test_save_rejects_oversized_input(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/setup/save", json={
                "camera_source": "x" * 201,
                "serial_port": "/dev/ttyTHS1",
            })
        assert resp.status_code == 400


# ── Factory Reset ─────────────────────────────────────────────

class TestFactoryReset:
    def test_factory_reset_restores_defaults(self, client, tmp_config, tmp_factory):
        # First modify the config
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            client.post("/api/config/full", json={"tak": {"callsign": "MODIFIED"}})
            # Now factory reset
            resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify factory defaults are restored
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["tak"]["callsign"] == "HYDRA-FACTORY"

    def test_factory_reset_no_factory_file(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 404
        assert "No factory defaults" in resp.json()["error"]

    def test_factory_reset_creates_backup(self, client, tmp_config, tmp_factory):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 200
        assert Path(str(tmp_config) + ".bak").exists()

    def test_factory_reset_triggers_restart(self, client, tmp_config, tmp_factory):
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 200
        restart_cb.assert_called_once()

    def test_factory_reset_requires_auth_when_enabled(self, client, tmp_config, tmp_factory):
        configure_auth("secret-token")
        resp = client.post("/api/config/factory-reset")
        assert resp.status_code == 401


# ── Restore Backup ────────────────────────────────────────────

class TestRestoreBackup:
    def test_restore_backup_works(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            # Make a change (creates backup)
            client.post("/api/config/full", json={"tak": {"callsign": "CHANGED"}})
            # Restore backup
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["tak"]["callsign"] == "HYDRA-1"

    def test_restore_backup_no_backup(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 404


# ── Config Export ─────────────────────────────────────────────

class TestConfigExport:
    def test_export_returns_config_dict(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "camera" in data
        assert "mavlink" in data
        assert data["camera"]["source"] == "auto"

    def test_export_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("secret-token")
        resp = client.get("/api/config/export")
        assert resp.status_code == 401


# ── Config Import ─────────────────────────────────────────────

class TestConfigImport:
    def test_import_writes_config(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/import", json={
                "camera": {"source": "/dev/video4"},
                "tak": {"callsign": "IMPORTED"},
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "imported"

        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["source"] == "/dev/video4"
        assert config["tak"]["callsign"] == "IMPORTED"

    def test_import_returns_restart_required(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/import", json={
                "camera": {"source": "/dev/video4"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "restart_required" in data
        assert any("source" in f for f in data["restart_required"])

    def test_import_rejects_invalid_json(self, client, tmp_config):
        resp = client.post("/api/config/import", content=b"not json",
                          headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_import_rejects_oversized_body(self, client, tmp_config):
        huge = {"camera": {"source": "x" * 70000}}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/import", json=huge)
        assert resp.status_code == 413

    def test_import_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("secret-token")
        resp = client.post("/api/config/import", json={"camera": {"source": "auto"}})
        assert resp.status_code == 401


# ── Systemd Service File ─────────────────────────────────────

class TestServiceFile:
    def test_service_has_auto_start_directives(self):
        service_path = Path(__file__).parent.parent / "scripts" / "hydra-detect.service"
        content = service_path.read_text()
        assert "WantedBy=multi-user.target" in content
        assert "Restart=" in content
        assert "RestartSec=" in content
        assert "After=network.target" in content
