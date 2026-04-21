"""Emit Kismet-detected RF devices as TAK/CoT markers.

Runs as a small background poller that pulls the current device list from
the Kismet data source (live or replay), applies a filter based on the
``[rf_homing] tak_export_mode`` setting, and forwards CoT events through
the shared ``TakOutput`` instance. No new network socket — reuses the
existing TAK multicast + unicast path.

Modes:
    off       — emit nothing (default)
    target    — emit only the current hunt target
    strong    — emit every device with RSSI ≥ ``strong_dbm`` (default -60)
    all       — emit everything in the device feed
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from ..tak.cot_builder import build_rf_device_marker
from ..tak.type_mapping import get_rf_cot_type

logger = logging.getLogger(__name__)


# Poll/emit cadence — keep modest to avoid saturating TAK downlinks. The
# existing YOLO detection pipeline publishes ~1 Hz; RF devices are similar.
_EMIT_INTERVAL_SEC = 2.0
# CoT stale interval — TAK clients drop markers after this unless refreshed.
_STALE_SEC = 10.0


class RfTakEmitter:
    """Background emitter that forwards RF devices to TakOutput.

    The emitter is inert until ``start()`` is called. It pulls devices via
    the supplied callback (typically ``self._get_rf_devices`` on the
    pipeline facade) and a MAVLink accessor for the survey platform's own
    GPS fix — RF devices are tagged at the platform's current position
    unless Kismet already provided GPS for the device itself.
    """

    def __init__(
        self,
        tak_output,
        get_devices: Callable[[], dict],
        get_self_position: Callable[[], tuple],
        *,
        callsign: str = "HYDRA",
        mode: str = "off",
        strong_dbm: float = -60.0,
    ):
        self._tak = tak_output
        self._get_devices = get_devices
        self._get_self_position = get_self_position
        self._callsign = callsign
        self._mode = mode if mode in ("off", "target", "strong", "all") else "off"
        self._strong_dbm = float(strong_dbm)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._emitted_uids: set[str] = set()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode in ("off", "target", "strong", "all"):
            self._mode = mode
            logger.info("RF TAK export mode: %s", mode)

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="rf-tak-emit",
        )
        self._thread.start()
        logger.info("RF TAK emitter started (mode=%s)", self._mode)
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # -- Core ---------------------------------------------------------------

    def _run_loop(self) -> None:
        while not self._stop_evt.wait(_EMIT_INTERVAL_SEC):
            if self._mode == "off":
                continue
            try:
                self._emit_one_cycle()
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("RF TAK emit cycle failed: %s", exc)

    def _emit_one_cycle(self) -> None:
        payload = self._get_devices()
        devices = payload.get("devices", []) if isinstance(payload, dict) else []
        if not devices:
            return
        filtered = self._filter_devices(devices)
        if not filtered:
            return
        self_lat, self_lon, self_alt = self._fetch_self_position()
        if self_lat is None or self_lon is None:
            # No way to place markers without a known origin — skip the cycle.
            return
        hae = float(self_alt) if self_alt is not None else 0.0
        for dev in filtered:
            cot = self._build_cot_for_device(dev, self_lat, self_lon, hae)
            if cot is None:
                continue
            self._tak.emit_cot(cot)

    def _filter_devices(self, devices: list[dict]) -> list[dict]:
        if self._mode == "all":
            return devices
        if self._mode == "target":
            return [d for d in devices if d.get("is_target")]
        if self._mode == "strong":
            return [
                d for d in devices
                if isinstance(d.get("rssi"), (int, float))
                and d["rssi"] >= self._strong_dbm
            ]
        return []

    def _build_cot_for_device(
        self, dev: dict, self_lat: float, self_lon: float, hae: float,
    ) -> bytes | None:
        bssid = dev.get("bssid") or ""
        freq = dev.get("freq_mhz")
        if not bssid and freq is None:
            return None
        # Prefer device's own GPS when Kismet provided it.
        lat = dev.get("lat")
        lon = dev.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            lat, lon = self_lat, self_lon
        kind = _device_kind(dev)
        cot_type = (
            get_rf_cot_type("target") if dev.get("is_target")
            else get_rf_cot_type(kind)
        )
        uid = f"HYDRA-RF-{bssid or freq}"
        self._emitted_uids.add(uid)
        rssi = float(dev.get("rssi", -100.0))
        callsign = dev.get("ssid") or bssid or (f"{freq}MHz" if freq else "RF")
        return build_rf_device_marker(
            uid=uid,
            callsign=str(callsign)[:32],
            cot_type=cot_type,
            lat=float(lat),
            lon=float(lon),
            hae=hae,
            rssi_dbm=rssi,
            ssid=dev.get("ssid"),
            bssid=bssid or None,
            freq_mhz=freq,
            stale_seconds=_STALE_SEC,
        )

    def _fetch_self_position(self) -> tuple:
        try:
            pos = self._get_self_position()
        except Exception:
            return (None, None, None)
        if not isinstance(pos, tuple) or len(pos) < 2:
            return (None, None, None)
        lat = pos[0] if len(pos) > 0 else None
        lon = pos[1] if len(pos) > 1 else None
        alt = pos[2] if len(pos) > 2 else None
        return (lat, lon, alt)


def _device_kind(dev: dict) -> str:
    """Best-effort kind classification for CoT type mapping."""
    freq = dev.get("freq_mhz")
    if isinstance(freq, (int, float)):
        if 2400 <= freq <= 2500 or 5150 <= freq <= 5850:
            # BLE advertising lives at 2402/2426/2480 — conservative split.
            if 2400 <= freq <= 2483 and (dev.get("channel") in (37, 38, 39)):
                return "ble"
            return "wifi"
        if 300 <= freq <= 1000:
            return "rtl433"
    return "wifi"
