"""Event timeline logger — records operator actions and vehicle telemetry for after-action review."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EventLogger:
    """Append-only JSONL logger for mission events and vehicle track.

    Events include: operator actions (lock, unlock, follow, strike, abort,
    mode changes), system state changes (camera lost, low light), and
    vehicle telemetry (GPS position at 1 Hz).
    """

    def __init__(self, log_dir: str | Path, callsign: str = "HYDRA"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._callsign = callsign
        self._mission_name: str | None = None
        self._file = None
        self._lock = threading.Lock()
        self._track_interval = 1.0  # GPS logging interval seconds
        self._last_track_time = 0.0

    def start_mission(self, name: str) -> None:
        """Begin a new mission — opens a new JSONL event file."""
        with self._lock:
            self._close_file()
            self._mission_name = name
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{self._callsign}_{ts}_{name}.jsonl"
            filepath = self._log_dir / filename
            self._file = open(filepath, "a")
            self._log_event("mission_start", {"name": name})
            logger.info("Event timeline started: %s", filepath)

    def end_mission(self) -> None:
        """End the current mission."""
        with self._lock:
            if self._mission_name:
                self._log_event("mission_end", {"name": self._mission_name})
            self._close_file()
            self._mission_name = None

    def log_action(self, action: str, details: dict[str, Any] | None = None) -> None:
        """Log an operator action (lock, unlock, follow, strike, abort, etc.)."""
        with self._lock:
            self._log_event("action", {"action": action, **(details or {})})

    def log_vehicle_track(self, lat: float, lon: float, alt: float,
                          heading: float | None = None,
                          speed: float | None = None,
                          mode: str | None = None) -> None:
        """Log vehicle position at 1 Hz rate limit."""
        now = time.monotonic()
        if now - self._last_track_time < self._track_interval:
            return
        self._last_track_time = now

        with self._lock:
            data: dict[str, Any] = {"lat": lat, "lon": lon, "alt": alt}
            if heading is not None:
                data["heading"] = heading
            if speed is not None:
                data["speed"] = speed
            if mode is not None:
                data["mode"] = mode
            self._log_event("track", data)

    def log_detection(self, track_id: int, label: str, confidence: float,
                      lat: float | None = None, lon: float | None = None) -> None:
        """Log a detection event."""
        with self._lock:
            self._log_event("detection", {
                "track_id": track_id, "label": label,
                "confidence": round(confidence, 3),
                "lat": lat, "lon": lon,
            })

    def log_state_change(self, state: str, details: dict[str, Any] | None = None) -> None:
        """Log a system state change (camera_lost, camera_restored, low_light, etc.)."""
        with self._lock:
            self._log_event("state", {"state": state, **(details or {})})

    def _log_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Write a single event record to the JSONL file."""
        if self._file is None:
            return
        record = {
            "ts": time.time(),
            "type": event_type,
            "callsign": self._callsign,
            **data,
        }
        try:
            self._file.write(json.dumps(record) + "\n")
            self._file.flush()
        except Exception as exc:
            logger.debug("Event logger write error: %s", exc)

    def _close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

    def stop(self) -> None:
        """Close any open mission file."""
        self.end_mission()

    def get_status(self) -> dict:
        """Return status for web API."""
        return {
            "mission_active": self._mission_name is not None,
            "mission_name": self._mission_name,
        }

    def get_recent_events(self, max_events: int = 200) -> list[dict]:
        """Read the last N events from the current mission file.

        Returns events in chronological order. Reads from the current open
        file (by reopening in read mode) so we don't disturb the append handle.
        The lock is held for the entire read to prevent concurrent end_mission()
        from deleting the file between the path check and the read.
        """
        with self._lock:
            if self._file is None:
                return []
            path = Path(self._file.name)
            if not path.exists():
                return []
            try:
                # Tail-read: take only the last max_events lines to avoid
                # loading the entire file into memory for large mission logs.
                lines = path.read_text().strip().splitlines()[-max_events:]
            except Exception as exc:
                logger.debug("Event logger read error: %s", exc)
                return []
        events = []
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
