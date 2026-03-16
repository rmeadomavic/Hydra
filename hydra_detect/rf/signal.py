"""RSSI signal filtering and analysis."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class RSSISample:
    """Single RSSI measurement tagged with GPS position."""

    rssi_dbm: float
    lat: float
    lon: float
    alt: float
    timestamp: float  # time.monotonic()


class RSSIFilter:
    """Sliding-window average to smooth noisy RSSI readings.

    WiFi / RF signal strength jumps due to multipath, antenna orientation,
    ground reflections, etc.  A simple moving average damps the noise enough
    for gradient ascent without adding latency from a Kalman filter.
    """

    def __init__(self, window_size: int = 10):
        self._window: deque[float] = deque(maxlen=window_size)

    def add(self, rssi: float) -> float:
        """Push a sample and return the smoothed value."""
        self._window.append(rssi)
        return self.average

    @property
    def average(self) -> float:
        if not self._window:
            return -100.0
        return sum(self._window) / len(self._window)

    @property
    def trend(self) -> float:
        """Positive = getting stronger, negative = weaker."""
        if len(self._window) < 4:
            return 0.0
        items = list(self._window)
        half = len(items) // 2
        first = sum(items[:half]) / half
        second = sum(items[half:]) / (len(items) - half)
        return second - first

    def reset(self) -> None:
        self._window.clear()
