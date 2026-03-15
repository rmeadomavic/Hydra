"""Gradient ascent navigator for RF source localization."""

from __future__ import annotations

import logging
import time

from .search import offset_position
from .signal import RSSISample

logger = logging.getLogger(__name__)


class GradientNavigator:
    """Steers the vehicle toward the strongest RSSI using gradient ascent.

    Algorithm per cycle:
    1. Record RSSI at current position.
    2. Fly *step_m* metres along current bearing.
    3. Record RSSI at new position.
    4. If signal improved → keep this bearing.
    5. If signal dropped → rotate by *rotation_deg* and retry.
    6. If all directions tried → return to best-known position.
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

        self.bearing: float = 0.0
        self.probe_count: int = 0
        self.best_rssi: float = -100.0
        self.best_position: tuple[float, float] = (0.0, 0.0)
        self.samples: list[RSSISample] = []

    def reset(self) -> None:
        self.probe_count = 0
        self.bearing = 0.0

    def record(self, rssi: float, lat: float, lon: float, alt: float) -> None:
        """Record an RSSI measurement with GPS position."""
        self.samples.append(RSSISample(
            rssi_dbm=rssi, lat=lat, lon=lon, alt=alt,
            timestamp=time.monotonic(),
        ))
        if rssi > self.best_rssi:
            self.best_rssi = rssi
            self.best_position = (lat, lon)
            logger.info(
                "New best RSSI: %.1f dBm at %.7f, %.7f", rssi, lat, lon,
            )

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
                return self.best_position[0], self.best_position[1], False
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
