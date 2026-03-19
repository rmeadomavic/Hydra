"""Tests for Kismet process lifecycle manager."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from unittest.mock import MagicMock, patch, mock_open

import pytest

from hydra_detect.rf.kismet_manager import KismetManager


class TestKismetManagerInit:
    def test_stores_config(self):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_kismet",
            host="http://localhost:2501",
            log_dir="/tmp/test_logs",
        )
        assert mgr._source == "rtl433-0"
        assert mgr._host == "http://localhost:2501"
        assert mgr.pid is None
        assert mgr.we_own_process is False


class TestKismetManagerStart:
    @patch("hydra_detect.rf.kismet_manager.shutil.which", return_value=None)
    def test_start_fails_if_kismet_not_installed(self, mock_which):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_kismet",
            host="http://localhost:2501",
            log_dir="/tmp/test_logs",
        )
        assert mgr.start() is False

    @patch("hydra_detect.rf.kismet_manager.shutil.which", return_value="/usr/bin/kismet")
    @patch("hydra_detect.rf.kismet_manager.requests.get")
    def test_start_adopts_existing_kismet(self, mock_get, mock_which):
        """If Kismet is already running, adopt it instead of spawning."""
        response = MagicMock()
        response.status_code = 200
        mock_get.return_value = response

        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_kismet",
            host="http://localhost:2501",
            log_dir="/tmp/test_logs",
        )
        assert mgr.start() is True
        assert mgr.we_own_process is False
        assert mgr.pid is None  # didn't spawn

    @patch("builtins.open", new_callable=mock_open)
    @patch("hydra_detect.rf.kismet_manager.os.makedirs")
    @patch("hydra_detect.rf.kismet_manager.subprocess.Popen")
    @patch("hydra_detect.rf.kismet_manager.requests.get")
    @patch("hydra_detect.rf.kismet_manager.shutil.which", return_value="/usr/bin/kismet")
    def test_start_spawns_kismet(self, mock_which, mock_get_check, mock_popen, mock_makedirs, mock_file):
        """If Kismet is not running, spawn it and wait for API."""
        import requests

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise requests.ConnectionError("not running")
            resp = MagicMock()
            resp.status_code = 200
            return resp
        mock_get_check.side_effect = side_effect

        proc = MagicMock()
        proc.pid = 12345
        proc.poll.return_value = None  # still running
        mock_popen.return_value = proc

        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_kismet",
            host="http://localhost:2501",
            log_dir="/tmp/test_logs",
        )
        assert mgr.start(timeout_sec=2.0) is True
        assert mgr.we_own_process is True
        assert mgr.pid == 12345

        # Verify subprocess args
        args = mock_popen.call_args
        cmd = args[0][0]
        assert "kismet" in cmd[0]
        assert "-c" in cmd
        assert "rtl433-0" in cmd
        assert "--no-ncurses" in cmd

    @patch("builtins.open", new_callable=mock_open)
    @patch("hydra_detect.rf.kismet_manager.os.makedirs")
    @patch("hydra_detect.rf.kismet_manager.subprocess.Popen")
    @patch("hydra_detect.rf.kismet_manager.requests.get")
    @patch("hydra_detect.rf.kismet_manager.shutil.which", return_value="/usr/bin/kismet")
    def test_start_creates_directories(self, mock_which, mock_get, mock_popen, mock_makedirs, mock_file):
        import requests
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise requests.ConnectionError("not running")
            resp = MagicMock()
            resp.status_code = 200
            return resp
        mock_get.side_effect = side_effect

        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_cap",
            host="http://localhost:2501",
            log_dir="/tmp/test_log",
        )
        mgr.start(timeout_sec=2.0)
        mock_makedirs.assert_any_call("/tmp/test_cap", exist_ok=True)
        mock_makedirs.assert_any_call("/tmp/test_log", exist_ok=True)


class TestKismetManagerStop:
    def test_stop_kills_owned_process(self):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_kismet",
            host="http://localhost:2501",
            log_dir="/tmp/test_logs",
        )
        proc = MagicMock()
        proc.pid = 999
        proc.poll.return_value = None
        proc.wait.return_value = 0
        mgr._process = proc
        mgr._we_own_process = True

        mgr.stop()
        proc.terminate.assert_called_once()

    def test_stop_does_not_kill_adopted_process(self):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_kismet",
            host="http://localhost:2501",
            log_dir="/tmp/test_logs",
        )
        mgr._we_own_process = False
        mgr._process = None

        mgr.stop()  # should not raise

    def test_stop_sigkill_fallback(self):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test_kismet",
            host="http://localhost:2501",
            log_dir="/tmp/test_logs",
        )
        proc = MagicMock()
        proc.pid = 999
        proc.poll.return_value = None  # still alive after SIGTERM
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="kismet", timeout=5)
        mgr._process = proc
        mgr._we_own_process = True

        mgr.stop(timeout_sec=0.1)
        proc.kill.assert_called_once()


class TestKismetManagerHealth:
    @patch("hydra_detect.rf.kismet_manager.requests.get")
    def test_healthy_when_api_responds(self, mock_get):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test",
            host="http://localhost:2501",
            log_dir="/tmp/test",
        )
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp
        # Simulate adopted process (no subprocess but API up)
        mgr._we_own_process = False
        assert mgr.is_healthy() is True

    @patch("hydra_detect.rf.kismet_manager.requests.get")
    def test_unhealthy_when_process_dead(self, mock_get):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test",
            host="http://localhost:2501",
            log_dir="/tmp/test",
        )
        proc = MagicMock()
        proc.poll.return_value = 1  # exited
        mgr._process = proc
        mgr._we_own_process = True
        assert mgr.is_healthy() is False

    @patch("hydra_detect.rf.kismet_manager.requests.get")
    def test_unhealthy_when_api_down(self, mock_get):
        import requests
        mock_get.side_effect = requests.ConnectionError("refused")
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test",
            host="http://localhost:2501",
            log_dir="/tmp/test",
        )
        assert mgr.is_healthy() is False


class TestKismetManagerRestart:
    @patch.object(KismetManager, "start", return_value=True)
    @patch.object(KismetManager, "stop")
    def test_restart_calls_stop_then_start(self, mock_stop, mock_start):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test",
            host="http://localhost:2501",
            log_dir="/tmp/test",
        )
        assert mgr.restart() is True
        mock_stop.assert_called_once()
        mock_start.assert_called_once()

    @patch.object(KismetManager, "start", return_value=True)
    @patch.object(KismetManager, "stop")
    def test_restart_bails_if_stop_event_set(self, mock_stop, mock_start):
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test",
            host="http://localhost:2501",
            log_dir="/tmp/test",
        )
        evt = threading.Event()
        evt.set()
        assert mgr.restart(stop_event=evt) is False
        mock_stop.assert_called_once()
        mock_start.assert_not_called()


class TestKismetManagerCaptureLimit:
    def test_deletes_oldest_when_over_limit(self, tmp_path):
        cap_dir = tmp_path / "captures"
        cap_dir.mkdir()
        # Create 3 files: 40 KB each = 120 KB total
        for i in range(3):
            f = cap_dir / f"Kismet-{i}.kismet"
            f.write_bytes(b"\x00" * 40_000)
            # Stagger mtimes so ordering is deterministic
            os.utime(f, (1000 + i, 1000 + i))

        mgr = KismetManager(
            source="rtl433-0",
            capture_dir=str(cap_dir),
            host="http://localhost:2501",
            log_dir=str(tmp_path / "logs"),
            max_capture_mb=0.05,  # ~52 KB limit
        )
        mgr._enforce_capture_limit()

        remaining = sorted(cap_dir.iterdir())
        # Should have deleted the two oldest, keeping only Kismet-2.kismet
        assert len(remaining) == 1
        assert remaining[0].name == "Kismet-2.kismet"

    def test_no_delete_when_under_limit(self, tmp_path):
        cap_dir = tmp_path / "captures"
        cap_dir.mkdir()
        f = cap_dir / "Kismet-0.kismet"
        f.write_bytes(b"\x00" * 1000)

        mgr = KismetManager(
            source="rtl433-0",
            capture_dir=str(cap_dir),
            host="http://localhost:2501",
            log_dir=str(tmp_path / "logs"),
            max_capture_mb=1.0,
        )
        mgr._enforce_capture_limit()
        assert len(list(cap_dir.iterdir())) == 1

    def test_unlimited_when_zero(self, tmp_path):
        cap_dir = tmp_path / "captures"
        cap_dir.mkdir()
        for i in range(5):
            (cap_dir / f"Kismet-{i}.kismet").write_bytes(b"\x00" * 10_000)

        mgr = KismetManager(
            source="rtl433-0",
            capture_dir=str(cap_dir),
            host="http://localhost:2501",
            log_dir=str(tmp_path / "logs"),
            max_capture_mb=0,
        )
        mgr._enforce_capture_limit()
        assert len(list(cap_dir.iterdir())) == 5
