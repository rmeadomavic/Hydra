"""Kismet process lifecycle manager — start, stop, health check, restart."""

from __future__ import annotations

import logging
import os
import signal
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
        user: Kismet API username (needed for health checks on Kismet 2025+).
        password: Kismet API password.
        log_dir: Directory for kismet.log (stdout/stderr).
        max_capture_mb: Max disk usage for capture files in MB (0 = unlimited).
    """

    def __init__(
        self,
        *,
        source: str = "rtl433-0",
        capture_dir: str = "./output_data/kismet",
        host: str = "http://localhost:2501",
        user: str = "kismet",
        password: str = "kismet",
        log_dir: str = "./output_data/logs",
        max_capture_mb: float = 100.0,
    ):
        self._source = source
        self._capture_dir = capture_dir
        self._host = host.rstrip("/")
        self._user = user
        self._password = password
        self._log_dir = log_dir
        self._max_capture_bytes = int(max_capture_mb * 1_048_576)
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
        if shutil.which("kismet") is None:
            logger.error(
                "Kismet is not installed (not found in PATH). "
                "Run hydra-setup.sh to install it."
            )
            return False

        if self._check_api():
            logger.info("Kismet already running at %s, adopting existing instance", self._host)
            self._we_own_process = False
            return True

        os.makedirs(self._capture_dir, exist_ok=True)
        os.makedirs(self._log_dir, exist_ok=True)

        self._enforce_capture_limit()

        cmd = [
            "kismet",
            "-c", self._source,
            "--no-ncurses",
            "--log-prefix", os.path.abspath(self._capture_dir),
        ]

        log_path = os.path.join(self._log_dir, "kismet.log")
        try:
            self._log_file = open(log_path, "w")
        except OSError as exc:
            logger.error("Cannot open Kismet log file %s: %s", log_path, exc)
            return False

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # own process group for clean shutdown
            )
        except FileNotFoundError:
            logger.error("Kismet binary not found despite which() check")
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            return False
        except OSError as exc:
            logger.error("Failed to start Kismet: %s", exc)
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            return False

        self._we_own_process = True
        logger.info("Kismet spawned (PID %d), waiting for API...", self._process.pid)

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
        # Kill entire process group (Kismet + child capture processes like rtl_433)
        try:
            pgid = os.getpgid(self._process.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            self._process.terminate()
        try:
            self._process.wait(timeout=timeout_sec)
            logger.info("Kismet stopped gracefully")
        except subprocess.TimeoutExpired:
            logger.warning("Kismet did not stop in %.0fs, sending SIGKILL", timeout_sec)
            try:
                pgid = os.getpgid(self._process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self._process.kill()
            try:
                self._process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                logger.error("Kismet did not die after SIGKILL — giving up")
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

    def _enforce_capture_limit(self) -> None:
        """Delete oldest .kismet files until total size is under the limit."""
        if self._max_capture_bytes <= 0:
            return
        try:
            files = []
            for name in os.listdir(self._capture_dir):
                if name.endswith((".kismet", ".kismet-journal")):
                    path = os.path.join(self._capture_dir, name)
                    files.append((os.path.getmtime(path), os.path.getsize(path), path))
        except OSError:
            return

        total = sum(size for _, size, _ in files)
        if total <= self._max_capture_bytes:
            return

        # Sort oldest first
        files.sort()
        for mtime, size, path in files:
            if total <= self._max_capture_bytes:
                break
            try:
                os.remove(path)
                total -= size
                logger.info("Removed old capture: %s (%.1f MB)", path, size / 1_048_576)
            except OSError as exc:
                logger.warning("Could not remove %s: %s", path, exc)

    def _check_api(self) -> bool:
        """Hit Kismet REST API to see if it's responding.

        Kismet 2025+ requires authentication on all endpoints, so we
        send basic auth credentials.  Falls back to unauthenticated
        request for older versions that allow anonymous status access.
        """
        try:
            r = requests.get(
                f"{self._host}/system/status.json",
                auth=(self._user, self._password),
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
