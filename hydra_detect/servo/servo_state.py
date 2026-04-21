"""Thread-safe servo state holder consumed by /api/servo/status.

The live pipeline can push pan/tilt telemetry into this holder from whatever
controls the servo (see ``hydra_detect/servo_tracker.py`` for the existing
pan-only tracker). The dashboard reads it via ``get_api_status()``. The
state is intentionally read-only from the API perspective — no external
driver is introduced by this module.

When no controller is pushing updates the default shape is returned, which
the frontend renders as an "idle" servo panel.
"""

from __future__ import annotations

import threading
import time


class ServoState:
    """Thread-safe, read-oriented snapshot of the servo stack.

    The fields mirror the ``/api/servo/status`` JSON shape:

    - ``enabled``: True when a controller has claimed the servo channel.
    - ``pan_deg`` / ``tilt_deg``: current commanded angle in degrees.
    - ``pan_limit_min`` / ``pan_limit_max`` / ``tilt_limit_min`` /
      ``tilt_limit_max``: software travel limits.
    - ``scanning``: True when the controller is sweeping vs. holding a lock.
    - ``locked_track_id``: the track the servo is currently centered on,
      or None.

    Concurrency: a single ``threading.Lock`` protects all writes. Reads
    return a shallow copy to callers so downstream mutation is safe.
    """

    def __init__(
        self,
        *,
        pan_limit_min: float = -90.0,
        pan_limit_max: float = 90.0,
        tilt_limit_min: float = -30.0,
        tilt_limit_max: float = 60.0,
    ) -> None:
        self._lock = threading.Lock()
        self._enabled = False
        self._pan_deg = 0.0
        self._tilt_deg = 0.0
        self._pan_limit_min = float(pan_limit_min)
        self._pan_limit_max = float(pan_limit_max)
        self._tilt_limit_min = float(tilt_limit_min)
        self._tilt_limit_max = float(tilt_limit_max)
        self._scanning = False
        self._locked_track_id: int | None = None
        self._last_update: float = 0.0

    def update(
        self,
        *,
        enabled: bool | None = None,
        pan_deg: float | None = None,
        tilt_deg: float | None = None,
        scanning: bool | None = None,
        locked_track_id: int | None = None,
    ) -> None:
        """Apply a partial state update. Any arg left as None is preserved.

        Intended to be called from the pipeline thread or from an external
        controller loop. Cheap (single lock acquisition, no allocation).
        """
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if pan_deg is not None:
                self._pan_deg = float(pan_deg)
            if tilt_deg is not None:
                self._tilt_deg = float(tilt_deg)
            if scanning is not None:
                self._scanning = bool(scanning)
            if locked_track_id is not None:
                # None means "don't touch". Use clear_lock() to unset.
                self._locked_track_id = int(locked_track_id)
            self._last_update = time.time()

    def clear_lock(self) -> None:
        """Drop the current track lock without disturbing pan/tilt."""
        with self._lock:
            self._locked_track_id = None
            self._last_update = time.time()

    def set_limits(
        self,
        *,
        pan_limit_min: float | None = None,
        pan_limit_max: float | None = None,
        tilt_limit_min: float | None = None,
        tilt_limit_max: float | None = None,
    ) -> None:
        """Update software travel limits."""
        with self._lock:
            if pan_limit_min is not None:
                self._pan_limit_min = float(pan_limit_min)
            if pan_limit_max is not None:
                self._pan_limit_max = float(pan_limit_max)
            if tilt_limit_min is not None:
                self._tilt_limit_min = float(tilt_limit_min)
            if tilt_limit_max is not None:
                self._tilt_limit_max = float(tilt_limit_max)

    def get_api_status(self) -> dict:
        """Return a snapshot dict matching the /api/servo/status shape."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "pan_deg": round(self._pan_deg, 2),
                "tilt_deg": round(self._tilt_deg, 2),
                "pan_limit_min": self._pan_limit_min,
                "pan_limit_max": self._pan_limit_max,
                "tilt_limit_min": self._tilt_limit_min,
                "tilt_limit_max": self._tilt_limit_max,
                "scanning": self._scanning,
                "locked_track_id": self._locked_track_id,
                "last_update": self._last_update,
            }
