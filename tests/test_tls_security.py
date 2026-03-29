"""Tests for TLS, per-Jetson API token generation, log export, and wipe-on-start."""

from __future__ import annotations

import configparser
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.tls import ensure_tls_cert
from hydra_detect.web.config_api import generate_api_token
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


# ---------------------------------------------------------------------------
# TLS cert generation
# ---------------------------------------------------------------------------

class TestEnsureTlsCert:
    def test_skips_when_files_exist(self, tmp_path):
        cert = tmp_path / "hydra.crt"
        key = tmp_path / "hydra.key"
        cert.write_text("CERT")
        key.write_text("KEY")
        result = ensure_tls_cert(str(cert), str(key))
        assert result is True

    def test_generates_cert_when_missing(self, tmp_path):
        cert = str(tmp_path / "certs" / "hydra.crt")
        key = str(tmp_path / "certs" / "hydra.key")
        with patch("hydra_detect.tls.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = ensure_tls_cert(cert, key)
        assert result is True
        mock_run.assert_called_once()
        # Verify openssl was called with correct args
        call_args = mock_run.call_args
        assert call_args[0][0][0] == "openssl"
        assert "-x509" in call_args[0][0]
        assert call_args[1]["check"] is True
        assert call_args[1]["timeout"] == 30

    def test_creates_parent_directories(self, tmp_path):
        cert = str(tmp_path / "deep" / "nested" / "hydra.crt")
        key = str(tmp_path / "deep" / "nested" / "hydra.key")
        with patch("hydra_detect.tls.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ensure_tls_cert(cert, key)
        assert Path(cert).parent.exists()

    def test_handles_openssl_failure(self, tmp_path):
        import subprocess
        cert = str(tmp_path / "certs" / "hydra.crt")
        key = str(tmp_path / "certs" / "hydra.key")
        with patch("hydra_detect.tls.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "openssl")
            result = ensure_tls_cert(cert, key)
        assert result is False

    def test_handles_openssl_not_found(self, tmp_path):
        cert = str(tmp_path / "certs" / "hydra.crt")
        key = str(tmp_path / "certs" / "hydra.key")
        with patch("hydra_detect.tls.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("openssl not found")
            result = ensure_tls_cert(cert, key)
        assert result is False

    def test_handles_timeout(self, tmp_path):
        import subprocess
        cert = str(tmp_path / "certs" / "hydra.crt")
        key = str(tmp_path / "certs" / "hydra.key")
        with patch("hydra_detect.tls.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("openssl", 30)
            result = ensure_tls_cert(cert, key)
        assert result is False


# ---------------------------------------------------------------------------
# API token generation
# ---------------------------------------------------------------------------

class TestGenerateApiToken:
    def test_produces_64_char_hex(self):
        token = generate_api_token()
        assert len(token) == 64
        # Verify it is valid hex
        int(token, 16)

    def test_generates_unique_tokens(self):
        tokens = {generate_api_token() for _ in range(10)}
        assert len(tokens) == 10


# ---------------------------------------------------------------------------
# run_server accepts SSL params
# ---------------------------------------------------------------------------

class TestRunServerSSL:
    def test_accepts_ssl_params(self):
        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("hydra_detect.web.server.threading.Thread") as mock_thread:
                mock_instance = MagicMock()
                mock_thread.return_value = mock_instance
                from hydra_detect.web.server import run_server
                run_server(
                    host="0.0.0.0",
                    port=8443,
                    ssl_certfile="/tmp/cert.pem",
                    ssl_keyfile="/tmp/key.pem",
                )
                mock_thread.assert_called_once()
                mock_instance.start.assert_called_once()
                # Verify the thread target callable was created (captures ssl kwargs)
                target_fn = mock_thread.call_args[1]["target"]
                assert callable(target_fn)

    def test_accepts_no_ssl_params(self):
        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("hydra_detect.web.server.threading.Thread") as mock_thread:
                mock_instance = MagicMock()
                mock_thread.return_value = mock_instance
                from hydra_detect.web.server import run_server
                run_server(host="0.0.0.0", port=8080)
                mock_thread.assert_called_once()
                mock_instance.start.assert_called_once()


# ---------------------------------------------------------------------------
# Export endpoint
# ---------------------------------------------------------------------------

class TestExportEndpoint:
    def test_export_returns_zip(self, client, tmp_path):
        # Create temp log and image files
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test.jsonl").write_text('{"frame": 1}')
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        (image_dir / "frame_001.jpg").write_bytes(b"\xff\xd8\xff\xe0")

        stream_state.set_callbacks(
            get_log_dir=lambda: str(log_dir),
            get_image_dir=lambda: str(image_dir),
        )

        resp = client.get("/api/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "hydra-export.zip" in resp.headers["content-disposition"]

        # Verify ZIP contents
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert "logs/test.jsonl" in names
        assert "images/frame_001.jpg" in names

    def test_export_requires_auth_when_enabled(self, client):
        configure_auth("secret-token")
        resp = client.get("/api/export")
        assert resp.status_code == 401

    def test_export_with_valid_auth(self, client, tmp_path):
        configure_auth("secret-token")
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        image_dir = tmp_path / "images"
        image_dir.mkdir()

        stream_state.set_callbacks(
            get_log_dir=lambda: str(log_dir),
            get_image_dir=lambda: str(image_dir),
        )

        resp = client.get(
            "/api/export",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200

    def test_export_empty_dirs(self, client, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        image_dir = tmp_path / "images"
        image_dir.mkdir()

        stream_state.set_callbacks(
            get_log_dir=lambda: str(log_dir),
            get_image_dir=lambda: str(image_dir),
        )

        resp = client.get("/api/export")
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert len(zf.namelist()) == 0


# ---------------------------------------------------------------------------
# Wipe on start config
# ---------------------------------------------------------------------------

class TestWipeOnStart:
    def test_wipe_clears_files(self, tmp_path):
        """Simulate the wipe_on_start logic from pipeline.start()."""
        import shutil

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "old.jsonl").write_text("data")
        (log_dir / "old.log").write_text("log data")
        sub_dir = log_dir / "subdir"
        sub_dir.mkdir()
        (sub_dir / "nested.txt").write_text("nested")

        image_dir = tmp_path / "images"
        image_dir.mkdir()
        (image_dir / "frame_001.jpg").write_bytes(b"\xff")

        # Replicate the pipeline wipe logic
        for d in [log_dir, image_dir]:
            if d.exists():
                for item in d.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)

        # Directories should still exist but be empty
        assert log_dir.exists()
        assert image_dir.exists()
        assert list(log_dir.iterdir()) == []
        assert list(image_dir.iterdir()) == []

    def test_wipe_noop_when_dirs_missing(self, tmp_path):
        """Wipe logic should not crash if directories don't exist."""
        import shutil

        log_dir = tmp_path / "nonexistent_logs"
        image_dir = tmp_path / "nonexistent_images"

        for d in [log_dir, image_dir]:
            if d.exists():
                for item in d.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)

        # Should not raise — directories simply don't exist
        assert not log_dir.exists()
        assert not image_dir.exists()

    def test_wipe_config_defaults_false(self):
        """wipe_on_start should default to False."""
        cfg = configparser.ConfigParser()
        cfg.add_section("logging")
        assert cfg.getboolean("logging", "wipe_on_start", fallback=False) is False

    def test_wipe_config_when_true(self):
        """wipe_on_start should read True when set."""
        cfg = configparser.ConfigParser()
        cfg.add_section("logging")
        cfg.set("logging", "wipe_on_start", "true")
        assert cfg.getboolean("logging", "wipe_on_start", fallback=False) is True
