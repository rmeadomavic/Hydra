"""Gradient ascent navigator for RF source localization."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from .search import offset_position
from .signal import RSSISample

logger = logging.getLogger(__name__)

# Maximum RSSI samples to retain in memory (~80 bytes each).
# At 2 Hz polling this covers ~2.8 hours of continuous hunt.
_MAX_SAMPLES = 20_000


class GradientNavigator:
    """Steers the vehicle toward the strongest RSSI using gradient ascent.

    Algorithm per cycle:
    1. Record RSSI at current position.
    2. Fly *step_m* metres along current bearing.
    3. Record RSSI at new position.
    4. If signal improved -> keep this bearing.
    5. If signal dropped -> rotate by *rotation_deg* and retry.
    6. If all directions tried -> return to best-known position.

    All public attributes and methods are thread-safe. The navigator state
    is read from the web API thread and written from the hunt thread.

    Args:
        step_m: Distance in metres for each gradient probe.
        rotation_deg: Degrees to rotate bearing after signal drops.
        max_probes: Max direction changes before declaring exhaustion.
        improve_threshold_dbm: Minimum dB improvement to count as progress.
        converge_dbm: RSSI level at which to declare convergence.
    """

    def __init__(
        self,
        step_m: float = 5.0,
        rotation_deg: float = 45.0,
        max_probes: int = 8,
        improve_threshold_dbm: float = 2.0,
        converge_dbm: float = -40.0,
    ):
        self._step_m = step_m
        self._rotation_deg = rotation_deg
        self._max_probes = max_probes
        self._improve_threshold = improve_threshold_dbm
        self._converge_dbm = converge_dbm

        self._lock = threading.Lock()
        self.bearing: float = 0.0
        self.probe_count: int = 0
        self.best_rssi: float = -100.0
        self.best_position: tuple[float, float] = (0.0, 0.0)
        self.samples: deque[RSSISample] = deque(maxlen=_MAX_SAMPLES)

    def reset(self) -> None:
        """Reset probe state for re-search. Keeps sample history."""
        with self._lock:
            self.probe_count = 0
            self.bearing = 0.0

    def record(self, rssi: float, lat: float, lon: float, alt: float) -> None:
        """Record an RSSI measurement with GPS position (thread-safe)."""
        sample = RSSISample(
            rssi_dbm=rssi, lat=lat, lon=lon, alt=alt,
            timestamp=time.monotonic(),
        )
        with self._lock:
            self.samples.append(sample)
            if rssi > self.best_rssi:
                self.best_rssi = rssi
                self.best_position = (lat, lon)
                logger.info(
                    "New best RSSI: %.1f dBm at %.7f, %.7f", rssi, lat, lon,
                )

    def get_best_rssi(self) -> float:
        """Return the best RSSI seen so far (thread-safe)."""
        with self._lock:
            return self.best_rssi

    def get_best_position(self) -> tuple[float, float]:
        """Return the (lat, lon) of the best RSSI reading (thread-safe)."""
        with self._lock:
            return self.best_position

    def get_sample_count(self) -> int:
        """Return the number of RSSI samples recorded (thread-safe)."""
        with self._lock:
            return len(self.samples)

    def get_samples_copy(self) -> list[RSSISample]:
        """Return a snapshot of all samples (thread-safe)."""
        with self._lock:
            return list(self.samples)

    def next_probe(
        self,
        current_lat: float,
        current_lon: float,
        current_rssi: float,
        previous_rssi: float,
    ) -> tuple[float, float, bool]:
        """Determine the next position to probe.

        Returns:
            (lat, lon, should_continue).  *should_continue* is False when
            convergence or probe exhaustion is reached.
        """
        if current_rssi >= self._converge_dbm:
            logger.info(
                "CONVERGED — RSSI %.1f dBm >= threshold %.1f",
                current_rssi, self._converge_dbm,
            )
            return current_lat, current_lon, False

        improvement = current_rssi - previous_rssi

        if improvement >= self._improve_threshold:
            # Signal improving — keep going
            self.probe_count = 0
            logger.info(
                "Signal improving (+%.1f dBm), bearing %.0f°",
                improvement, self.bearing,
            )
        elif improvement > -self._improve_threshold:
            # Marginal — keep direction
            logger.debug(
                "Signal marginal (%+.1f dBm), continuing %.0f°",
                improvement, self.bearing,
            )
        else:
            # Signal dropped — try a different direction
            self.probe_count += 1
            if self.probe_count >= self._max_probes:
                logger.warning("All probe directions exhausted")
                with self._lock:
                    bp = self.best_position
                return bp[0], bp[1], False
            self.bearing = (self.bearing + self._rotation_deg) % 360
            logger.info(
                "Signal dropped (%+.1f dBm), rotating to %.0f° "
                "(probe %d/%d)",
                improvement, self.bearing,
                self.probe_count, self._max_probes,
            )

        nlat, nlon = offset_position(
            current_lat, current_lon, self.bearing, self._step_m,
        )
        return nlat, nlon, True
