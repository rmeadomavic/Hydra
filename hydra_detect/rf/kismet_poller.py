"""Kismet REST poller → AmbientScanBuffer.

The Cockpit SDR spectrum tile on the ops dashboard is driven by
``/api/rf/ambient_scan``, which in turn reads from an
:class:`hydra_detect.rf.ambient_scan.AmbientScanBuffer` registered via
``server.set_rf_ambient_scan(buf)``. This module is the other half of
that pipe: it polls Kismet's ``/devices/views/all/devices.json`` every
``poll_interval_sec`` and pushes one sample per fresh device into the
buffer.

Design contract:

* stdlib only — uses ``urllib.request`` so this module pulls in no new
  dependencies beyond what the wider repo already ships.
* Graceful degrade — an unreachable Kismet, bad credentials, or
  malformed JSON never raises; we log a throttled warning and keep
  retrying on the next cycle.
* Bounded — at most ``max_samples_per_cycle`` pushes per cycle, to
  protect the buffer from a wide-band-scan burst that may surface
  hundreds of devices at once.
* Stateless — the buffer is the only store. The poller holds no sample
  history of its own.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any
from urllib import error as urlerror, parse as urlparse, request as urlrequest

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "http://localhost:2501"
_DEFAULT_POLL_INTERVAL_SEC = 0.5
_DEFAULT_TIMEOUT_SEC = 2.0
_DEFAULT_MAX_SAMPLES_PER_CYCLE = 50

# Devices not seen by Kismet within this many seconds are ignored — a
# stale entry in /devices.json means the emitter is no longer audible.
_STALE_CUTOFF_SEC = 10.0

# Never poll faster than this — protects Kismet + our own CPU budget.
_MIN_POLL_INTERVAL_SEC = 0.1

# How often (in failed cycles) to re-surface a warning while the error
# persists. Prevents log spam when Kismet is simply not running.
_ERROR_LOG_EVERY_N = 60

# Fields we ask Kismet for. Keeping this tight reduces JSON size and
# avoids pulling device-level history we do not consume.
_FIELDS_FILTER = (
    '{"fields":['
    '"kismet.device.base.frequency",'
    '"kismet.device.base.last_time",'
    '"kismet.device.base.first_time",'
    '"kismet.device.base.phyname",'
    '"kismet.device.base.signal/kismet.common.signal.last_signal"'
    ']}'
)


def _modulation_from(phyname: str, freq_mhz: float) -> str:
    """Best-effort modulation label from Kismet's phyname + frequency."""
    phy = (phyname or "").lower()
    if "ieee802.11" in phy or "wifi" in phy:
        if 2400.0 <= freq_mhz <= 2500.0:
            return "wifi_2g"
        if 5150.0 <= freq_mhz <= 5900.0:
            return "wifi_5g"
        return "wifi"
    if "rtl433" in phy or "rtl_433" in phy:
        if 420.0 <= freq_mhz <= 440.0:
            return "ism_433"
        if 900.0 <= freq_mhz <= 930.0:
            return "ism_915"
        return "rtl433"
    if "bluetooth" in phy or "btle" in phy or "ble" in phy:
        return "bt"
    if 5600.0 <= freq_mhz <= 5900.0:
        return "fpv_raceband"
    if phy:
        return phy
    return "unknown"


def _normalise_freq_mhz(raw: Any) -> float | None:
    """Convert Kismet's freq field to MHz, tolerating Hz/kHz/MHz inputs."""
    try:
        freq = float(raw)
    except (TypeError, ValueError):
        return None
    if freq <= 0:
        return None
    if freq > 10_000_000:
        return freq / 1e6
    if freq > 10_000:
        return freq / 1e3
    return freq


def _parse_devices(payload: Any, max_samples: int) -> list[dict]:
    """Turn a Kismet /devices.json response into sample dicts.

    Stale devices (``last_time`` older than ``_STALE_CUTOFF_SEC``) and
    devices without a real measured frequency or non-zero RSSI are
    skipped. All downstream consumers can assume every sample carries a
    physical measurement — no synthetic or placeholder values.
    """
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    stale_cutoff = time.time() - _STALE_CUTOFF_SEC
    for dev in payload:
        if not isinstance(dev, dict):
            continue
        last_time = dev.get("kismet.device.base.last_time") or 0
        if last_time and last_time < stale_cutoff:
            continue
        freq_mhz = _normalise_freq_mhz(
            dev.get("kismet.device.base.frequency"),
        )
        if freq_mhz is None:
            continue
        sig = dev.get("kismet.device.base.signal") or {}
        rssi_raw = sig.get("kismet.common.signal.last_signal")
        if rssi_raw is None or rssi_raw == 0:
            continue
        try:
            rssi_dbm = float(rssi_raw)
        except (TypeError, ValueError):
            continue
        phy = str(dev.get("kismet.device.base.phyname") or "")
        first_time = dev.get("kismet.device.base.first_time") or 0
        duration_ms = 0.0
        if first_time and last_time and last_time >= first_time:
            duration_ms = float(last_time - first_time) * 1000.0
        out.append({
            "freq_mhz": freq_mhz,
            "rssi_dbm": rssi_dbm,
            "modulation": _modulation_from(phy, freq_mhz),
            "duration_ms": duration_ms,
        })
        if len(out) >= max_samples:
            break
    return out


class KismetPoller:
    """Daemon-thread poller that drains Kismet → AmbientScanBuffer.

    Typical wiring in ``__main__.py``::

        buffer = AmbientScanBuffer()
        server.set_rf_ambient_scan(buffer)
        poller = KismetPoller(
            buffer,
            host="http://localhost:2501",
            user="kismet", password="kismet",
        )
        poller.start()  # returns False if host is empty — no crash
    """

    def __init__(
        self,
        buffer: Any,
        *,
        host: str,
        user: str = "",
        password: str = "",
        poll_interval_sec: float = _DEFAULT_POLL_INTERVAL_SEC,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        max_samples_per_cycle: int = _DEFAULT_MAX_SAMPLES_PER_CYCLE,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._buffer = buffer
        self._host = (host or "").strip().rstrip("/")
        self._user = user or ""
        self._password = password or ""
        self._interval = max(
            _MIN_POLL_INTERVAL_SEC, float(poll_interval_sec),
        )
        self._timeout = float(timeout_sec)
        self._max_samples = max(1, int(max_samples_per_cycle))
        self._stop_event = stop_event or threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_errors = 0

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    @property
    def host(self) -> str:
        return self._host

    def start(self) -> bool:
        """Spawn the daemon polling thread.

        Returns ``False`` (and logs a warning) if the host URL is empty
        or the thread is already running.
        """
        if not self._host:
            logger.warning(
                "KismetPoller not started — empty host URL",
            )
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="kismet-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "KismetPoller started — %s every %.2fs (cap %d samples/cycle)",
            self._host, self._interval, self._max_samples,
        )
        return True

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the thread to exit and wait briefly."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def poll_once(self) -> int:
        """Run one poll cycle. Returns the number of samples pushed.

        Exposed for tests and for on-demand operators. Never raises.
        """
        if not self._host:
            return 0
        payload = self._fetch_devices()
        if payload is None:
            return 0
        samples = _parse_devices(payload, self._max_samples)
        pushed = 0
        for s in samples:
            try:
                self._buffer.push_sample(
                    freq_mhz=s["freq_mhz"],
                    rssi_dbm=s["rssi_dbm"],
                    modulation=s["modulation"],
                    duration_ms=s["duration_ms"],
                )
                pushed += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("AmbientScanBuffer push rejected: %s", exc)
        return pushed

    # -- Internals -------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self._interval)
        logger.info("KismetPoller stopped")

    def _fetch_devices(self) -> Any | None:
        query = urlparse.urlencode({"KISMET": _FIELDS_FILTER})
        url = f"{self._host}/devices/views/all/devices.json?{query}"
        req = urlrequest.Request(url, method="GET")
        if self._user or self._password:
            token = base64.b64encode(
                f"{self._user}:{self._password}".encode("utf-8"),
            ).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urlrequest.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read()
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except urlerror.URLError as exc:
            self._log_fetch_error("connection", exc)
            return None
        except (json.JSONDecodeError, UnicodeError) as exc:
            self._log_fetch_error("parse", exc)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._log_fetch_error("unknown", exc)
            return None
        if self._consecutive_errors > 0:
            logger.info(
                "KismetPoller recovered after %d error(s)",
                self._consecutive_errors,
            )
            self._consecutive_errors = 0
        return payload

    def _log_fetch_error(self, kind: str, exc: Exception) -> None:
        self._consecutive_errors += 1
        n = self._consecutive_errors
        if n == 1 or n % _ERROR_LOG_EVERY_N == 0:
            logger.warning(
                "KismetPoller %s error (#%d): %s", kind, n, exc,
            )
        else:
            logger.debug(
                "KismetPoller %s error (#%d): %s", kind, n, exc,
            )
