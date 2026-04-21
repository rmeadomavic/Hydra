"""Tests for system.py — Jetson/Linux hardware introspection helpers.

Real hardware calls (``nvpmodel``, ``/sys/devices/platform/gpu.0/load``) are
mocked.  Tests that require a real Jetson are marked ``@pytest.mark.hardware``.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect import system


# ---------------------------------------------------------------------------
# query_nvpmodel_sync
# ---------------------------------------------------------------------------

class TestQueryNvpmodel:
    def test_sync_parses_output(self):
        fake = MagicMock(stdout="NV Power Mode: MAXN\n", stderr="", returncode=0)
        with patch("hydra_detect.system.subprocess.run", return_value=fake):
            assert system.query_nvpmodel_sync() == "MAXN"

    def test_sync_returns_none_on_missing_binary(self):
        with patch(
            "hydra_detect.system.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            assert system.query_nvpmodel_sync() is None

    def test_sync_returns_none_on_timeout(self):
        with patch(
            "hydra_detect.system.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="nvpmodel", timeout=2),
        ):
            assert system.query_nvpmodel_sync() is None


# ---------------------------------------------------------------------------
# list_power_modes
# ---------------------------------------------------------------------------

class TestListPowerModes:
    def test_parses_verbose_output(self):
        out = (
            "NVPM VERB: POWER_MODEL: ID=0 NAME=15W\n"
            "NVPM VERB: POWER_MODEL: ID=1 NAME=7W\n"
            "NVPM VERB: POWER_MODEL: ID=2 NAME=MAXN\n"
        )
        fake = MagicMock(stdout="", stderr=out, returncode=0)
        with patch("hydra_detect.system.subprocess.run", return_value=fake):
            modes = system.list_power_modes()
        assert {"id": 0, "name": "15W"} in modes
        assert {"id": 2, "name": "MAXN"} in modes
        assert len(modes) == 3

    def test_missing_binary_returns_empty(self):
        with patch(
            "hydra_detect.system.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            assert system.list_power_modes() == []


# ---------------------------------------------------------------------------
# set_power_mode
# ---------------------------------------------------------------------------

class TestSetPowerMode:
    def test_success(self):
        ok = MagicMock(returncode=0, stdout="", stderr="")

        def fake_run(argv, *a, **kw):
            assert argv[0] in ("nvpmodel", "jetson_clocks")
            return ok

        with patch("hydra_detect.system.subprocess.run", side_effect=fake_run):
            r = system.set_power_mode(0)
        assert r["status"] == "ok"
        assert any("Power mode" in a for a in r["actions"])

    def test_nvpmodel_failure(self):
        bad = MagicMock(returncode=1, stdout="", stderr="bad mode")
        with patch("hydra_detect.system.subprocess.run", return_value=bad):
            r = system.set_power_mode(99)
        assert r["status"] == "error"
        assert "bad mode" in r["error"]

    def test_missing_binary(self):
        with patch(
            "hydra_detect.system.subprocess.run",
            side_effect=FileNotFoundError("nvpmodel"),
        ):
            r = system.set_power_mode(0)
        assert r["status"] == "error"


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------

class TestListModels:
    def test_discovers_model_files(self, tmp_path):
        (tmp_path / "yolov8n.pt").write_bytes(b"0" * (1024 * 1024))
        (tmp_path / "yolov8s.engine").write_bytes(b"0" * (2 * 1024 * 1024))
        (tmp_path / "readme.txt").write_text("ignored")

        models = system.list_models(str(tmp_path))
        names = {m["name"] for m in models}
        assert "yolov8n.pt" in names
        assert "yolov8s.engine" in names
        assert "readme.txt" not in names

    def test_deduplicates_by_name(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "model.pt").write_bytes(b"first")
        (dir_b / "model.pt").write_bytes(b"second")

        models = system.list_models(str(dir_a), str(dir_b))
        assert len(models) == 1
        # First directory wins
        assert models[0]["path"].startswith(str(dir_a))

    def test_missing_directory_skipped(self):
        assert system.list_models("/does/not/exist") == []

    def test_reports_size_mb(self, tmp_path):
        (tmp_path / "big.pt").write_bytes(b"0" * (5 * 1024 * 1024))
        models = system.list_models(str(tmp_path))
        assert models[0]["size_mb"] == pytest.approx(5.0, abs=0.1)


# ---------------------------------------------------------------------------
# read_thermal — sysfs access
# ---------------------------------------------------------------------------

class TestReadThermal:
    def test_parses_sysfs_temp(self):
        mock_path = MagicMock()
        mock_path.read_text.return_value = "45678\n"
        with patch("hydra_detect.system.Path", return_value=mock_path):
            t = system.read_thermal("1")
        assert t == pytest.approx(45.7, abs=0.1)

    def test_missing_zone_returns_none(self):
        mock_path = MagicMock()
        mock_path.read_text.side_effect = FileNotFoundError()
        with patch("hydra_detect.system.Path", return_value=mock_path):
            assert system.read_thermal("99") is None
