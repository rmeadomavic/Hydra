"""Tests for tls.py — self-signed certificate generation."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from hydra_detect.tls import ensure_tls_cert


class TestEnsureTlsCert:
    def test_skips_when_both_files_exist(self, tmp_path):
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_text("existing cert")
        key.write_text("existing key")

        with patch("hydra_detect.tls.subprocess.run") as mock_run:
            assert ensure_tls_cert(str(cert), str(key)) is True
            mock_run.assert_not_called()

    def test_generates_when_missing(self, tmp_path):
        cert = tmp_path / "certs" / "cert.pem"
        key = tmp_path / "certs" / "key.pem"
        with patch("hydra_detect.tls.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert ensure_tls_cert(str(cert), str(key)) is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "openssl"
            assert "-x509" in args

    def test_openssl_not_installed(self, tmp_path):
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        with patch(
            "hydra_detect.tls.subprocess.run",
            side_effect=FileNotFoundError("no openssl"),
        ):
            assert ensure_tls_cert(str(cert), str(key)) is False

    def test_openssl_failure(self, tmp_path):
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        err = subprocess.CalledProcessError(1, "openssl", stderr=b"oops")
        with patch("hydra_detect.tls.subprocess.run", side_effect=err):
            assert ensure_tls_cert(str(cert), str(key)) is False

    def test_timeout(self, tmp_path):
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        err = subprocess.TimeoutExpired(cmd="openssl", timeout=30)
        with patch("hydra_detect.tls.subprocess.run", side_effect=err):
            assert ensure_tls_cert(str(cert), str(key)) is False
