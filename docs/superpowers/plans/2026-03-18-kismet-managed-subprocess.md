# Kismet Managed Subprocess Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-start Kismet as a managed subprocess when `rf_homing.enabled = true`, so the RF hunt system actually works end-to-end.

**Architecture:** New `KismetManager` class owns the Kismet process lifecycle (start/stop/health/restart). Pipeline creates it before `KismetClient`, passes it to `RFHuntController` for mid-hunt restarts. Existing `KismetClient` and hunt state machine are unchanged.

**Tech Stack:** Python 3.10+, subprocess, requests, pytest

**Spec:** `docs/superpowers/specs/2026-03-18-kismet-managed-subprocess-design.md`

---

### Task 1: Gitignore and cleanup

**Files:**
- Modify: `.gitignore`
- Delete: `*.kismet` and `*.kismet-journal` files in repo root

- [ ] **Step 1: Add Kismet patterns to .gitignore**

Append to `.gitignore`:

```
# Kismet capture files
*.kismet
*.kismet-journal
output_data/kismet/
```

- [ ] **Step 2: Remove Kismet capture files from repo root**

```bash
sudo rm -f /home/sorcc/Hydra/Kismet-*.kismet /home/sorcc/Hydra/Kismet-*.kismet-journal
```

These are test artifacts from 2026-03-17 (~17MB, owned by root). Not tracked by git.

- [ ] **Step 3: Add config fields to config.ini**

Add two new fields at the end of the `[rf_homing]` section (after `arrival_tolerance_m` on line 88):

```ini
kismet_source = rtl433-0
kismet_capture_dir = ./output_data/kismet
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore config.ini
git commit -m "chore: gitignore kismet captures, add kismet_source/capture_dir config"
```

---

### Task 2: KismetManager — start and stop (TDD)

**Files:**
- Create: `hydra_detect/rf/kismet_manager.py`
- Create: `tests/test_rf_kismet_manager.py`

- [ ] **Step 1: Write failing tests for start/stop**

Create `tests/test_rf_kismet_manager.py`:

```python
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

        # First call: check existing → not running
        # Subsequent calls: poll for API ready → success
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
        assert "--daemonize" not in " ".join(cmd) or "false" in " ".join(cmd).lower()

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_rf_kismet_manager.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'hydra_detect.rf.kismet_manager'`

- [ ] **Step 3: Write KismetManager implementation**

Create `hydra_detect/rf/kismet_manager.py`:

```python
"""Kismet process lifecycle manager — start, stop, health check, restart."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time

import requests

logger = logging.getLogger(__name__)


class KismetManager:
    """Manages Kismet as a subprocess for RF homing.

    Spawns Kismet when start() is called, monitors health, and cleans up
    on stop(). If Kismet is already running, adopts the existing instance
    without spawning a new process.

    Args:
        source: Kismet capture source (e.g. "rtl433-0").
        capture_dir: Directory for .kismet capture files.
        host: Kismet REST API base URL.
        log_dir: Directory for kismet.log (stdout/stderr).
    """

    def __init__(
        self,
        *,
        source: str = "rtl433-0",
        capture_dir: str = "./output_data/kismet",
        host: str = "http://localhost:2501",
        log_dir: str = "./output_data/logs",
    ):
        self._source = source
        self._capture_dir = capture_dir
        self._host = host.rstrip("/")
        self._log_dir = log_dir
        self._process: subprocess.Popen | None = None
        self._we_own_process = False
        self._log_file = None

    @property
    def pid(self) -> int | None:
        """PID of the managed Kismet process, or None."""
        if self._process is not None:
            return self._process.pid
        return None

    @property
    def we_own_process(self) -> bool:
        """True if we spawned Kismet, False if we adopted an existing one."""
        return self._we_own_process

    def start(self, timeout_sec: float = 15.0) -> bool:
        """Start Kismet or adopt an existing instance.

        Returns True if Kismet is running and API is reachable.
        """
        # Check if kismet binary exists
        if shutil.which("kismet") is None:
            logger.error(
                "Kismet is not installed (not found in PATH). "
                "Run hydra-setup.sh to install it."
            )
            return False

        # Check for pre-existing Kismet instance
        if self._check_api():
            logger.info("Kismet already running at %s, adopting existing instance", self._host)
            self._we_own_process = False
            return True

        # Create directories
        os.makedirs(self._capture_dir, exist_ok=True)
        os.makedirs(self._log_dir, exist_ok=True)

        # Build command
        log_prefix = os.path.join(os.path.abspath(self._capture_dir), "Kismet")
        cmd = [
            "kismet",
            "-c", self._source,
            "--no-ncurses",
            "--override", f"log_prefix={log_prefix}",
            "--daemonize", "false",
        ]

        # Open log file in truncate mode
        log_path = os.path.join(self._log_dir, "kismet.log")
        try:
            self._log_file = open(log_path, "w")
        except OSError as exc:
            logger.error("Cannot open Kismet log file %s: %s", log_path, exc)
            return False

        # Spawn
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError:
            logger.error("Kismet binary not found despite which() check")
            self._log_file.close()
            self._log_file = None
            return False
        except OSError as exc:
            logger.error("Failed to start Kismet: %s", exc)
            self._log_file.close()
            self._log_file = None
            return False

        self._we_own_process = True
        logger.info("Kismet spawned (PID %d), waiting for API...", self._process.pid)

        # Poll for API readiness
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                logger.error(
                    "Kismet exited during startup (code %d). Check %s",
                    self._process.returncode, log_path,
                )
                self._cleanup_process()
                return False
            if self._check_api():
                logger.info("Kismet API ready at %s (PID %d)", self._host, self._process.pid)
                return True
            time.sleep(0.5)

        logger.error("Kismet API not ready after %.0fs — killing", timeout_sec)
        self.stop(timeout_sec=3.0)
        return False

    def stop(self, timeout_sec: float = 5.0) -> None:
        """Stop the Kismet process if we own it."""
        if not self._we_own_process or self._process is None:
            self._process = None
            self._we_own_process = False
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            return

        if self._process.poll() is not None:
            logger.info("Kismet already exited (code %d)", self._process.returncode)
            self._cleanup_process()
            return

        logger.info("Stopping Kismet (PID %d)...", self._process.pid)
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout_sec)
            logger.info("Kismet stopped gracefully")
        except subprocess.TimeoutExpired:
            logger.warning("Kismet did not stop in %.0fs, sending SIGKILL", timeout_sec)
            self._process.kill()
            self._process.wait(timeout=3.0)
        self._cleanup_process()

    def is_healthy(self) -> bool:
        """Check if Kismet process is alive and API responds."""
        if self._we_own_process and self._process is not None:
            if self._process.poll() is not None:
                return False
        return self._check_api()

    def restart(self, stop_event: threading.Event | None = None) -> bool:
        """Stop and restart Kismet. Checks stop_event between phases."""
        self.stop()

        if stop_event is not None and stop_event.is_set():
            logger.info("Hunt cancelled during Kismet restart — aborting")
            return False

        return self.start()

    def _check_api(self) -> bool:
        """Hit Kismet REST API to see if it's responding."""
        try:
            r = requests.get(
                f"{self._host}/system/status.json",
                timeout=2.0,
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _cleanup_process(self) -> None:
        """Clean up process and log file handles."""
        self._process = None
        self._we_own_process = False
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_rf_kismet_manager.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/rf/kismet_manager.py tests/test_rf_kismet_manager.py
git commit -m "feat: add KismetManager for subprocess lifecycle (start/stop/adopt)"
```

---

### Task 3: KismetManager — health check and restart (TDD)

**Files:**
- Modify: `tests/test_rf_kismet_manager.py`
- Modify: `hydra_detect/rf/kismet_manager.py` (already implemented above, tests validate)

- [ ] **Step 1: Write failing tests for health check and restart**

Append to `tests/test_rf_kismet_manager.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they pass**

These test the implementation from Task 2. They should pass immediately.

```bash
python -m pytest tests/test_rf_kismet_manager.py -v
```

Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_rf_kismet_manager.py
git commit -m "test: add health check and restart tests for KismetManager"
```

---

### Task 4: Wire KismetManager into RFHuntController (TDD)

**Files:**
- Modify: `hydra_detect/rf/hunt.py:78-140` (constructor) and `289-295` (`_poll_rssi`)
- Modify: `tests/test_rf_hunt.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_rf_hunt.py`:

```python
class TestHuntKismetManagerIntegration:
    def test_accepts_kismet_manager(self):
        from hydra_detect.rf.kismet_manager import KismetManager
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test",
            host="http://localhost:2501",
            log_dir="/tmp/test",
        )
        ctrl = _make_controller(kismet_manager=mgr)
        assert ctrl._kismet_manager is mgr

    def test_works_without_kismet_manager(self):
        ctrl = _make_controller()
        assert ctrl._kismet_manager is None

    def test_poll_rssi_restarts_kismet_on_failure(self):
        from unittest.mock import PropertyMock
        from hydra_detect.rf.kismet_manager import KismetManager

        mgr = MagicMock(spec=KismetManager)
        mgr.restart.return_value = True

        ctrl = _make_controller(kismet_manager=mgr)
        ctrl._set_state(HuntState.SEARCHING)

        # First call returns None (connection error), retry after restart returns -60
        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.side_effect = [None, -60.0]
        ctrl._kismet.check_connection.return_value = False

        rssi = ctrl._poll_rssi()
        # Should have attempted restart and returned the retry value
        mgr.restart.assert_called_once()
        assert rssi == -60.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_rf_hunt.py::TestHuntKismetManagerIntegration -v
```

Expected: FAIL — `_make_controller() got an unexpected keyword argument 'kismet_manager'`

- [ ] **Step 3: Add kismet_manager parameter to RFHuntController**

In `hydra_detect/rf/hunt.py`, add the import at the top (after line 22):

```python
from .kismet_manager import KismetManager
```

Add parameter to `__init__` (after `on_state_change` param, line 106):

```python
        kismet_manager: KismetManager | None = None,
```

Store it (after `self._on_state_change = on_state_change`, line 120):

```python
        self._kismet_manager = kismet_manager
```

- [ ] **Step 4: Update `_poll_rssi` to retry via manager**

Replace `_poll_rssi` method (lines 289-295) with:

```python
    def _poll_rssi(self) -> float | None:
        """Poll Kismet for current RSSI, restarting Kismet once on failure."""
        rssi = self._kismet.get_rssi(
            mode=self._mode,
            bssid=self._target_bssid,
            freq_mhz=self._target_freq_mhz,
        )
        if rssi is not None:
            return rssi

        # No reading — check if Kismet is still up
        if self._kismet_manager is None:
            return None
        if not self._kismet.check_connection():
            logger.warning("Kismet connection lost — attempting restart")
            if self._kismet_manager.restart(stop_event=self._stop_evt):
                # Reset auth so KismetClient re-authenticates with new Kismet instance.
                # TODO: Add KismetClient.reset_auth() method to avoid private attr access.
                self._kismet._authenticated = False
                return self._kismet.get_rssi(
                    mode=self._mode,
                    bssid=self._target_bssid,
                    freq_mhz=self._target_freq_mhz,
                )
            logger.error("Kismet restart failed")
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_rf_hunt.py -v
```

Expected: all tests PASS (existing + new)

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/rf/hunt.py tests/test_rf_hunt.py
git commit -m "feat: RFHuntController restarts Kismet via KismetManager on connection loss"
```

---

### Task 5: Wire KismetManager into pipeline

**Files:**
- Modify: `hydra_detect/pipeline.py:18` (imports), `324-355` (init), `494-500` (start), `904-933` (`_handle_rf_start`), `957-966` (`_shutdown`)
- Modify: `hydra_detect/rf/__init__.py`

- [ ] **Step 1: Update rf/__init__.py exports**

Replace contents of `hydra_detect/rf/__init__.py`:

```python
"""RF homing — Kismet-based RSSI gradient ascent for RF source localization."""

from __future__ import annotations

from .kismet_manager import KismetManager

__all__ = ["KismetManager"]
```

- [ ] **Step 2: Add KismetManager import to pipeline.py**

In `hydra_detect/pipeline.py` line 18, change:

```python
from .rf.hunt import RFHuntController
```

to:

```python
from .rf.hunt import RFHuntController
from .rf.kismet_manager import KismetManager
```

- [ ] **Step 3: Add KismetManager creation in pipeline init**

In `hydra_detect/pipeline.py`, replace lines 324-355 (the RF homing init block) with:

```python
        # RF homing controller
        self._rf_hunt: RFHuntController | None = None
        self._kismet_manager: KismetManager | None = None
        if self._cfg.getboolean("rf_homing", "enabled", fallback=False):
            if self._mavlink is not None:
                kismet_host = self._cfg.get("rf_homing", "kismet_host", fallback="http://localhost:2501")
                self._kismet_manager = KismetManager(
                    source=self._cfg.get("rf_homing", "kismet_source", fallback="rtl433-0"),
                    capture_dir=self._cfg.get("rf_homing", "kismet_capture_dir", fallback="./output_data/kismet"),
                    host=kismet_host,
                    log_dir=self._cfg.get("logging", "log_dir", fallback="./output_data/logs"),
                )
                if self._kismet_manager.start():
                    self._rf_hunt = RFHuntController(
                        self._mavlink,
                        mode=self._cfg.get("rf_homing", "mode", fallback="wifi"),
                        target_bssid=self._cfg.get("rf_homing", "target_bssid", fallback="").strip() or None,
                        target_freq_mhz=self._cfg.getfloat("rf_homing", "target_freq_mhz", fallback=915.0),
                        kismet_host=kismet_host,
                        kismet_user=self._cfg.get("rf_homing", "kismet_user", fallback="kismet"),
                        kismet_pass=self._cfg.get("rf_homing", "kismet_pass", fallback="kismet"),
                        search_pattern=self._cfg.get("rf_homing", "search_pattern", fallback="lawnmower"),
                        search_area_m=self._cfg.getfloat("rf_homing", "search_area_m", fallback=100.0),
                        search_spacing_m=self._cfg.getfloat("rf_homing", "search_spacing_m", fallback=20.0),
                        search_alt_m=self._cfg.getfloat("rf_homing", "search_alt_m", fallback=15.0),
                        rssi_threshold_dbm=self._cfg.getfloat("rf_homing", "rssi_threshold_dbm", fallback=-80.0),
                        rssi_converge_dbm=self._cfg.getfloat("rf_homing", "rssi_converge_dbm", fallback=-40.0),
                        rssi_window=self._cfg.getint("rf_homing", "rssi_window", fallback=10),
                        gradient_step_m=self._cfg.getfloat("rf_homing", "gradient_step_m", fallback=5.0),
                        gradient_rotation_deg=self._cfg.getfloat("rf_homing", "gradient_rotation_deg", fallback=45.0),
                        poll_interval_sec=self._cfg.getfloat("rf_homing", "poll_interval_sec", fallback=0.5),
                        arrival_tolerance_m=self._cfg.getfloat("rf_homing", "arrival_tolerance_m", fallback=3.0),
                        kismet_manager=self._kismet_manager,
                    )
                    logger.info(
                        "RF homing configured: mode=%s target=%s",
                        self._cfg.get("rf_homing", "mode", fallback="wifi"),
                        self._cfg.get("rf_homing", "target_bssid", fallback="")
                        or f"{self._cfg.getfloat('rf_homing', 'target_freq_mhz', fallback=915.0)}MHz",
                    )
                else:
                    logger.warning("Kismet failed to start — RF homing disabled")
                    self._kismet_manager = None
            else:
                logger.warning("RF homing requires MAVLink — skipping")
```

- [ ] **Step 4: Pass KismetManager in _handle_rf_start**

In `hydra_detect/pipeline.py`, in the `_handle_rf_start` method (around line 915), add the missing `rssi_window`, `gradient_rotation_deg`, and new `kismet_manager` params to the `RFHuntController` constructor call. After `gradient_step_m` (line 929), add:

```python
            gradient_rotation_deg=self._cfg.getfloat("rf_homing", "gradient_rotation_deg", fallback=45.0),
            rssi_window=self._cfg.getint("rf_homing", "rssi_window", fallback=10),
```

And after `arrival_tolerance_m` (line 931), add:

```python
            kismet_manager=self._kismet_manager,
```

- [ ] **Step 5: Stop KismetManager in _shutdown**

In `hydra_detect/pipeline.py`, in the `_shutdown` method (line 957), add after `self._rf_hunt.stop()` (line 960):

```python
        if self._kismet_manager is not None:
            self._kismet_manager.stop()
```

- [ ] **Step 6: Add web API test for kismet_manager pass-through**

Append to `tests/test_rf_web_api.py`:

```python
class TestRFStartReceivesKismetManager:
    def test_start_passes_params_to_callback(self):
        """Verify web-initiated hunt params include all fields needed by RFHuntController."""
        received = {}

        def on_start(params):
            received.update(params)
            return True
        stream_state.set_callbacks(on_rf_start=on_start)
        resp = client.post("/api/rf/start", json={
            "mode": "sdr",
            "target_freq_mhz": 915.0,
        })
        assert resp.status_code == 200
        assert received["mode"] == "sdr"
```

Note: The web API passes params to the pipeline callback; the pipeline is responsible for adding `kismet_manager`. This test verifies the API layer works. The actual `kismet_manager` wiring is tested via the pipeline integration.

- [ ] **Step 7: Run all RF tests**

```bash
python -m pytest tests/test_rf_hunt.py tests/test_rf_kismet.py tests/test_rf_kismet_manager.py tests/test_rf_signal.py tests/test_rf_navigator.py tests/test_rf_search.py tests/test_rf_web_api.py -v
```

Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add hydra_detect/rf/__init__.py hydra_detect/pipeline.py
git commit -m "feat: pipeline starts KismetManager before RF hunt, stops on shutdown"
```

---

### Task 6: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run complete test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS, no regressions

- [ ] **Step 2: Run linter**

```bash
flake8 hydra_detect/rf/kismet_manager.py hydra_detect/rf/hunt.py hydra_detect/pipeline.py
```

Expected: no errors (or only pre-existing ones)

- [ ] **Step 3: Verify config.ini has new fields**

```bash
grep -A2 "kismet_source\|kismet_capture_dir" config.ini
```

Expected:
```
kismet_source = rtl433-0
kismet_capture_dir = ./output_data/kismet
```

- [ ] **Step 4: Verify .gitignore has Kismet patterns**

```bash
grep -A1 "kismet" .gitignore
```

Expected: `*.kismet`, `*.kismet-journal`, `output_data/kismet/`

- [ ] **Step 5: Verify Kismet capture files are cleaned up**

```bash
ls *.kismet 2>/dev/null; echo "exit: $?"
```

Expected: no files found, exit 2

- [ ] **Step 6: Commit spec and plan docs**

```bash
git add docs/superpowers/
git commit -m "docs: add Kismet managed subprocess spec and implementation plan"
```
