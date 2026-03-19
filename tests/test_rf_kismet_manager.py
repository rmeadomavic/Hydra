"""Tests for Kismet process lifecycle manager."""

from __future__ import annotations

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

    @patch("hydra_detect.rf.kismet_manager.os.makedirs")
    @patch("hydra_detect.rf.kismet_manager.subprocess.Popen")
    @patch("hydra_detect.rf.kismet_manager.requests.get")
    @patch("hydra_detect.rf.kismet_manager.shutil.which", return_value="/usr/bin/kismet")
    def test_start_spawns_kismet(self, mock_which, mock_get_check, mock_popen, mock_makedirs):
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

    @patch("hydra_detect.rf.kismet_manager.os.makedirs")
    @patch("hydra_detect.rf.kismet_manager.subprocess.Popen")
    @patch("hydra_detect.rf.kismet_manager.requests.get")
    @patch("hydra_detect.rf.kismet_manager.shutil.which", return_value="/usr/bin/kismet")
    def test_start_creates_directories(self, mock_which, mock_get, mock_popen, mock_makedirs):
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
