"""Event timeline logger.

Records operator actions and vehicle telemetry for after-action review.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sanitize mission names for use in filenames — strip path separators and
# control characters so a poorly-chosen name can't escape the log directory.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_name(name: str) -> str:
    """Strip filesystem-hostile characters from a mission name.

    Strict policy: only ``[A-Za-z0-9_-]`` survives. Anything else collapses
    to a single underscore. We deliberately drop ``.`` entirely (no
    ``..`` traversal in the filename, no leading-dot hidden files) — the
    mission name on disk is decoration; the JSONL itself records the
    original name verbatim inside ``mission_start``.
    """
    cleaned = _SAFE_NAME_RE.sub("_", name).strip("_-")
    return cleaned[:64] or "mission"


class EventLogger:
    """Append-only JSONL logger for mission events and vehicle track.

    Events include: operator actions (lock, unlock, follow, strike, abort,
    mode changes), system state changes (camera lost, low light), and
    vehicle telemetry (GPS position at 1 Hz).

    Each mission carries a UUID-v4 ``mission_id`` that is stamped on every
    event record (including ``mission_start`` and ``mission_end``). The id
    is generated server-side by ``start_mission()`` and propagated to the
    detection log so detection rows and event rows can be joined per-sortie.
    """

    # Default size of the in-memory recent-events ring buffer. Kept in sync
    # with get_recent_events(max_events=200) to avoid a silent truncation
    # when callers use the default.
    _RECENT_DEFAULT = 200

    def __init__(self, log_dir: str | Path, callsign: str = "HYDRA"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._callsign = callsign
        self._mission_name: str | None = None
        self._mission_id: str | None = None
        self._mission_start_ts: float | None = None
        self._mission_log_path: Path | None = None
        self._file = None
        self._lock = threading.Lock()
        self._track_interval = 1.0  # GPS logging interval seconds
        self._last_track_time = 0.0
        # In-memory ring buffer of the last N events, populated alongside
        # disk writes. Dashboard polls (get_recent_events) read this instead
        # of re-opening the mission log — avoids blocking the pipeline write
        # path on disk I/O while still keeping the JSONL file as the
        # system of record for after-action review / verify_log.py.
        self._recent: deque[dict] = deque(maxlen=self._RECENT_DEFAULT)

    def start_mission(self, name: str, mission_id: str | None = None) -> str:
        """Begin a new mission — opens a new JSONL event file.

        Args:
            name: Operator-supplied sortie name (sanitized for the filename).
            mission_id: Optional pre-generated UUID. Generated server-side if
                not supplied. Callers that need to coordinate the id across
                subsystems (detection logger, web stats) should pass the same
                id they hand to those subsystems.

        Returns:
            The mission_id stamped on every event in this mission.
        """
        with self._lock:
            self._close_file()
            self._recent.clear()
            self._mission_name = name
            self._mission_id = mission_id or str(uuid.uuid4())
            self._mission_start_ts = time.time()
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe = _safe_name(name)
            filename = f"{self._callsign}_{ts}_{safe}.jsonl"
            filepath = self._log_dir / filename
            self._mission_log_path = filepath
            self._file = open(filepath, "a")
            self._log_event("mission_start", {
                "name": name,
                "mission_id": self._mission_id,
            })
            logger.info(
                "Event timeline started: %s (mission_id=%s)",
                filepath, self._mission_id,
            )
            return self._mission_id

    def end_mission(self) -> None:
        """End the current mission."""
        with self._lock:
            if self._mission_name:
                self._log_event("mission_end", {
                    "name": self._mission_name,
                    "mission_id": self._mission_id,
                })
            self._close_file()
            self._mission_name = None
            self._mission_id = None
            self._mission_start_ts = None
            self._mission_log_path = None
            # Keep _recent populated until the next start_mission so the
            # dashboard can still display the tail of the just-ended mission.

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
        """Write a single event record to the JSONL file and ring buffer."""
        if self._file is None:
            return
        record: dict[str, Any] = {
            "ts": time.time(),
            "type": event_type,
            "callsign": self._callsign,
            "mission_id": self._mission_id,
            **data,
        }
        # Append to in-memory ring buffer first — even if the disk write
        # fails, the dashboard still sees the event. The deque itself is
        # bounded and the caller already holds self._lock, so this is O(1).
        self._recent.append(record)
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
        with self._lock:
            mission_log = (
                self._mission_log_path.name if self._mission_log_path else None
            )
            return {
                "mission_active": self._mission_name is not None,
                "mission_name": self._mission_name,
                "mission_id": self._mission_id,
                "mission_start_ts": self._mission_start_ts,
                "mission_log": mission_log,
            }

    def get_mission_id(self) -> str | None:
        """Return the active mission_id, or None when idle."""
        with self._lock:
            return self._mission_id

    def get_mission_log_path(self) -> Path | None:
        """Return path to the JSONL file for the active mission, or None."""
        with self._lock:
            return self._mission_log_path

    def get_recent_events(self, max_events: int = 200) -> list[dict]:
        """Return the last N events from the in-memory ring buffer.

        The dashboard polls this on every refresh; reading from the ring
        buffer avoids holding ``self._lock`` across a disk read (which was
        blocking the pipeline's log_action / log_detection calls during
        long missions, since the mission JSONL file grows unboundedly).

        Events are chronological (oldest first). ``max_events`` is capped
        at the ring buffer size (``_RECENT_DEFAULT``) — larger requests are
        silently truncated. The persisted JSONL remains the system of
        record for after-action review; use ``verify_log.py`` to audit it.
        """
        with self._lock:
            # list(deque) + slice is O(N) on a bounded N — trivial compared
            # to the disk read this replaces.
            return list(self._recent)[-max_events:]
