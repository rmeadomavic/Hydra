"""Kismet replay source — feeds canned JSONL data into the RF hunt pipeline.

Implements the same duck-typed surface as ``KismetClient`` so ``RFHuntController``
can run tabletop demos without a real Kismet instance or SDR hardware.

Selection is made in ``pipeline/facade.py``: if the live Kismet API is
unreachable and ``[rf_homing] replay_path`` points at a valid JSONL file,
the replay source is wired in via the ``client=`` kwarg on
``RFHuntController``.

Fixture schema — one JSON object per line, ``t`` monotonic non-decreasing::

    {"t": 0.0, "bssid": "AA:BB:CC:00:00:01", "ssid": "CAFE-GUEST",
     "rssi": -72, "channel": 6, "freq_mhz": 2437.0,
     "manuf": "TP-Link", "lat": null, "lon": null}

Fields:

- ``t`` (float, required) — seconds from fixture start.
- ``bssid`` (str, required) — MAC ``AA:BB:CC:DD:EE:FF``.
- ``rssi`` (int, required) — dBm, -120 to 0.
- ``ssid`` (str, optional) — may be null for hidden networks.
- ``channel`` (int, optional) — WiFi channel or BLE advertising channel.
- ``freq_mhz`` (float, optional) — central frequency in MHz (SDR mode).
- ``manuf`` (str, optional) — OUI lookup string.
- ``lat`` / ``lon`` (float, optional) — beacon GPS, if known.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

logger = logging.getLogger(__name__)


# Freshness window for RSSI lookups — matches KismetClient.get_sdr_rssi (10 s).
_FRESHNESS_SEC = 10.0


class KismetDataSource(Protocol):
    """Structural contract shared by KismetClient and KismetReplaySource.

    Any object that implements these methods can be passed as ``client=`` to
    ``RFHuntController``. The hunt controller only ever calls these methods —
    nothing else — so the live client and the replay source are fully
    interchangeable.
    """

    def check_connection(self) -> bool:
        ...

    def get_rssi(
        self,
        *,
        mode: str = "wifi",
        bssid: str | None = None,
        freq_mhz: float | None = None,
    ) -> float | None:
        ...

    def get_wifi_rssi(self, bssid: str) -> float | None:
        ...

    def get_sdr_rssi(
        self, target_freq_mhz: float, tolerance_mhz: float = 0.5,
    ) -> float | None:
        ...

    def reset_auth(self) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class _ReplaySample:
    t: float
    bssid: str
    ssid: str | None
    rssi: float
    channel: int | None
    freq_mhz: float | None
    manuf: str | None
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class _DeviceMeta:
    bssid: str
    ssid: str | None
    channel: int | None
    freq_mhz: float | None
    manuf: str | None
    first_seen: float
    last_seen: float


def _coerce_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _coerce_int(val: object) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


class KismetReplaySource:
    """Replays a JSONL fixture as if it were live Kismet data.

    Playback starts on the first call to ``check_connection`` or ``get_rssi``.
    At wall-clock time ``t_wall``, the replay position is
    ``(t_wall - t_start) * speed``. When ``loop=True`` and replay runs past
    the fixture end, playback wraps cleanly so 2-minute demos repeat.

    Args:
        path: Path to the JSONL fixture.
        loop: When True, wrap around at fixture end (default True).
        speed: Playback multiplier. 1.0 = wall-clock, 10.0 = 10× faster
            (default 1.0).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        loop: bool = True,
        speed: float = 1.0,
    ):
        self._path = Path(path)
        self._loop = bool(loop)
        self._speed = max(0.01, float(speed))
        self._samples: list[_ReplaySample] = []
        self._device_index: dict[str, list[_ReplaySample]] = {}
        self._duration: float = 0.0
        self._t_start: float | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._load()

    # -- Loading -----------------------------------------------------------

    def _load(self) -> None:
        if not self._path.is_file():
            raise FileNotFoundError(
                f"Replay fixture not found: {self._path}"
            )
        samples: list[_ReplaySample] = []
        last_t = -1.0
        skipped = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    skipped += 1
                    logger.warning(
                        "Replay fixture %s:%d — bad JSON: %s",
                        self._path, lineno, exc,
                    )
                    continue
                t = _coerce_float(obj.get("t"))
                bssid = obj.get("bssid")
                rssi = _coerce_float(obj.get("rssi"))
                if t is None or bssid is None or rssi is None:
                    skipped += 1
                    continue
                if t < last_t:
                    skipped += 1
                    logger.warning(
                        "Replay fixture %s:%d — t=%.3f decreases from %.3f, skipping",
                        self._path, lineno, t, last_t,
                    )
                    continue
                last_t = t
                sample = _ReplaySample(
                    t=t,
                    bssid=str(bssid).upper(),
                    ssid=obj.get("ssid") if obj.get("ssid") is not None else None,
                    rssi=rssi,
                    channel=_coerce_int(obj.get("channel")),
                    freq_mhz=_coerce_float(obj.get("freq_mhz")),
                    manuf=obj.get("manuf") if obj.get("manuf") is not None else None,
                    lat=_coerce_float(obj.get("lat")),
                    lon=_coerce_float(obj.get("lon")),
                )
                samples.append(sample)

        if not samples:
            raise ValueError(
                f"Replay fixture {self._path} contains no usable samples"
            )

        samples.sort(key=lambda s: s.t)
        self._samples = samples
        self._duration = samples[-1].t
        for sample in samples:
            self._device_index.setdefault(sample.bssid, []).append(sample)

        logger.info(
            "Kismet replay loaded: %s (%d samples, %d devices, %.1f s, "
            "loop=%s, speed=%.1fx, skipped=%d)",
            self._path.name, len(samples), len(self._device_index),
            self._duration, self._loop, self._speed, skipped,
        )

    # -- Clock / position --------------------------------------------------

    def _now_replay(self) -> float:
        """Return current playback time in fixture seconds.

        Starts the clock on first call. With ``loop=True`` the result wraps
        at ``duration``. With ``loop=False`` the clock keeps advancing past
        ``duration`` so freshness checks naturally age the final sample out
        — a non-looping fixture that has played past its end should go
        silent, not pin the last sample indefinitely.
        """
        with self._lock:
            if self._t_start is None:
                self._t_start = time.monotonic()
                return 0.0
            elapsed = (time.monotonic() - self._t_start) * self._speed
            if self._duration <= 0.0:
                return 0.0
            if self._loop:
                return elapsed % self._duration
            return elapsed

    # -- KismetDataSource surface -----------------------------------------

    def check_connection(self) -> bool:
        """Replay is always connected while the fixture is loaded."""
        return not self._closed and bool(self._samples)

    def reset_auth(self) -> None:
        """No-op — replay has no auth state."""
        return None

    def close(self) -> None:
        self._closed = True

    def get_wifi_rssi(self, bssid: str) -> float | None:
        if self._closed or not bssid:
            return None
        now = self._now_replay()
        key = bssid.upper()
        series = self._device_index.get(key)
        if not series:
            return None
        sample = self._latest_before(series, now)
        if sample is None:
            return None
        if now - sample.t > _FRESHNESS_SEC:
            return None
        return float(sample.rssi)

    def get_sdr_rssi(
        self, target_freq_mhz: float, tolerance_mhz: float = 0.5,
    ) -> float | None:
        if self._closed:
            return None
        now = self._now_replay()
        best: float | None = None
        for series in self._device_index.values():
            sample = self._latest_before(series, now)
            if sample is None:
                continue
            if sample.freq_mhz is None:
                continue
            if abs(sample.freq_mhz - target_freq_mhz) > tolerance_mhz:
                continue
            if now - sample.t > _FRESHNESS_SEC:
                continue
            if best is None or sample.rssi > best:
                best = float(sample.rssi)
        return best

    def get_rssi(
        self,
        *,
        mode: str = "wifi",
        bssid: str | None = None,
        freq_mhz: float | None = None,
    ) -> float | None:
        if mode == "wifi" and bssid:
            return self.get_wifi_rssi(bssid)
        if mode == "sdr" and freq_mhz is not None:
            return self.get_sdr_rssi(freq_mhz)
        return None

    def list_devices(
        self, max_age_sec: float = _FRESHNESS_SEC,
    ) -> list[dict]:
        """Return devices seen within ``max_age_sec`` of the current replay time.

        Matches the normalized shape used by ``KismetClient.list_devices`` so the
        web layer can consume either interchangeably.
        """
        if self._closed:
            return []
        now = self._now_replay()
        out: list[dict] = []
        for bssid, series in self._device_index.items():
            sample = self._latest_before(series, now)
            if sample is None:
                continue
            if now - sample.t > max_age_sec:
                continue
            meta = self._device_meta(bssid, series)
            out.append({
                "bssid": bssid,
                "ssid": sample.ssid if sample.ssid is not None else meta.ssid,
                "rssi": float(sample.rssi),
                "channel": sample.channel if sample.channel is not None else meta.channel,
                "freq_mhz": (
                    sample.freq_mhz if sample.freq_mhz is not None else meta.freq_mhz
                ),
                "manuf": sample.manuf if sample.manuf is not None else meta.manuf,
                "first_seen": meta.first_seen,
                "last_seen": sample.t,
                "lat": sample.lat,
                "lon": sample.lon,
            })
        out.sort(key=lambda d: d["rssi"], reverse=True)
        return out

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _latest_before(
        series: list[_ReplaySample], now: float,
    ) -> _ReplaySample | None:
        """Return the most recent sample with ``t <= now`` (series is time-sorted)."""
        # Small linear scan from the end — typical series has <250 samples.
        for sample in reversed(series):
            if sample.t <= now:
                return sample
        return None

    @staticmethod
    def _device_meta(
        bssid: str, series: Iterable[_ReplaySample],
    ) -> _DeviceMeta:
        first = next(iter(series))
        ssid = first.ssid
        channel = first.channel
        freq = first.freq_mhz
        manuf = first.manuf
        first_t = first.t
        last_t = first.t
        for sample in series:
            if sample.t < first_t:
                first_t = sample.t
            if sample.t > last_t:
                last_t = sample.t
            if ssid is None and sample.ssid is not None:
                ssid = sample.ssid
            if channel is None and sample.channel is not None:
                channel = sample.channel
            if freq is None and sample.freq_mhz is not None:
                freq = sample.freq_mhz
            if manuf is None and sample.manuf is not None:
                manuf = sample.manuf
        return _DeviceMeta(
            bssid=bssid,
            ssid=ssid,
            channel=channel,
            freq_mhz=freq,
            manuf=manuf,
            first_seen=first_t,
            last_seen=last_t,
        )

    # -- Diagnostics -------------------------------------------------------

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def device_count(self) -> int:
        return len(self._device_index)

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def __enter__(self) -> KismetReplaySource:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
