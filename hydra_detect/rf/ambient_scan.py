"""Bounded ambient-RF sample sink consumed by /api/rf/ambient_scan.

The existing ``rf/`` package performs *active* RSSI hunting against a known
target. This module provides a passive ring buffer for ambient RF
observations (freq/RSSI/modulation tuples) that the Kismet parser or any
future SDR integration can push into. The dashboard polls it for the SDR
sniffer ticker.

Contract:
- ``push_sample(...)`` is cheap and thread-safe — callable from any
  parser thread.
- ``get_samples()`` returns the current snapshot in the API response shape.
- ``_MAXLEN`` caps the deque to keep memory bounded even under a flood.
"""

from __future__ import annotations

import threading
import time
from collections import deque

# Hard cap on retained samples. Older samples are evicted FIFO.
_MAXLEN = 200

# Window over which we report "what was seen recently" — consumed by
# callers to label the dashboard axis. Samples older than the window are
# filtered at read time (the deque itself is still bounded by _MAXLEN).
_DEFAULT_WINDOW_SEC = 60


class AmbientScanBuffer:
    """Thread-safe bounded ring of ambient RF observations.

    Each sample is a dict::

        {"ts": <epoch seconds>,
         "freq_mhz": <float>,
         "rssi_dbm": <float>,
         "modulation": <str>,
         "duration_ms": <float>}

    No external driver is launched by this class — it is purely a sink.
    """

    def __init__(
        self,
        maxlen: int = _MAXLEN,
        window_seconds: int = _DEFAULT_WINDOW_SEC,
    ) -> None:
        self._samples: deque[dict] = deque(maxlen=int(maxlen))
        self._lock = threading.Lock()
        self._window_seconds = int(window_seconds)

    def push_sample(
        self,
        *,
        freq_mhz: float,
        rssi_dbm: float,
        modulation: str = "unknown",
        duration_ms: float = 0.0,
        ts: float | None = None,
    ) -> None:
        """Append one sample. Thread-safe, O(1)."""
        entry = {
            "ts": float(ts) if ts is not None else time.time(),
            "freq_mhz": float(freq_mhz),
            "rssi_dbm": float(rssi_dbm),
            "modulation": str(modulation or "unknown"),
            "duration_ms": float(duration_ms),
        }
        with self._lock:
            self._samples.append(entry)

    def get_samples(self) -> dict:
        """Return the API response shape.

        Evicts samples older than the configured window so the histogram
        stays relevant. The underlying deque hard cap still applies.
        """
        now = time.time()
        cutoff = now - self._window_seconds
        with self._lock:
            # Lazy window eviction — cheap walk from the left.
            while self._samples and self._samples[0]["ts"] < cutoff:
                self._samples.popleft()
            samples = list(self._samples)
        max_rssi = max((s["rssi_dbm"] for s in samples), default=None)
        return {
            "samples": samples,
            "window_seconds": self._window_seconds,
            "max_rssi": max_rssi,
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)
