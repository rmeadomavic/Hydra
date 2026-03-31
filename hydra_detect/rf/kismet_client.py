"""Kismet REST API client for RSSI polling."""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)


class KismetClient:
    """Polls Kismet's REST API for signal strength data.

    Supports two modes:
    - WiFi: query by BSSID (MAC address) — requires monitor-mode WiFi adapter
    - SDR: query by frequency — uses RTL-SDR (e.g. NESDR Smart R860) via rtl_433

    Kismet API: https://www.kismetwireless.net/docs/api/

    Can be used as a context manager for automatic session cleanup::

        with KismetClient(host="http://localhost:2501") as client:
            rssi = client.get_wifi_rssi("AA:BB:CC:DD:EE:FF")

    Args:
        host: Kismet REST API base URL (must start with http:// or https://).
        user: Kismet API username.
        password: Kismet API password.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        host: str = "http://localhost:2501",
        user: str = "",
        password: str = "",
        timeout: float = 2.0,
    ):
        host = host.strip()
        if not host.startswith(("http://", "https://")):
            raise ValueError(
                f"Kismet host must be an HTTP(S) URL, got: {host!r}"
            )
        self._host = host.rstrip("/")
        self._timeout = timeout
        self._user = user
        self._password = password
        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"
        self._authenticated = False

    def __enter__(self) -> KismetClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _ensure_auth(self) -> bool:
        """Establish a Kismet session.

        Kismet 2025+ uses cookie-based sessions. We POST to
        /session/check_session with basic auth to get a session cookie,
        then use that cookie for all subsequent requests. Falls back to
        persistent basic auth for older Kismet versions.
        """
        if self._authenticated:
            return True
        try:
            # Try cookie session auth (Kismet 2025+)
            r = self._session.get(
                f"{self._host}/session/check_session",
                auth=(self._user, self._password),
                timeout=self._timeout,
            )
            if r.status_code == 200:
                # Session cookie is now stored in self._session
                self._authenticated = True
                logger.debug("Kismet session established via cookie auth")
                return True
            # Fall back to persistent basic auth (older Kismet)
            self._session.auth = (self._user, self._password)
            r2 = self._session.get(
                f"{self._host}/system/status.json",
                timeout=self._timeout,
            )
            if r2.status_code == 200:
                self._authenticated = True
                logger.debug("Kismet connected via basic auth (legacy)")
                return True
            logger.error("Kismet auth failed (status %d)", r2.status_code)
            return False
        except requests.RequestException as exc:
            logger.error("Kismet auth error: %s", exc)
            return False

    def check_connection(self) -> bool:
        """Return True if Kismet API is reachable and authenticated."""
        try:
            if not self._ensure_auth():
                return False
            r = self._session.get(
                f"{self._host}/system/status.json", timeout=self._timeout,
            )
            if r.status_code == 200:
                logger.debug("Kismet API connected at %s", self._host)
                return True
            # Session may have expired — retry auth once
            self._authenticated = False
            if not self._ensure_auth():
                return False
            r = self._session.get(
                f"{self._host}/system/status.json", timeout=self._timeout,
            )
            if r.status_code == 200:
                logger.debug("Kismet API reconnected at %s", self._host)
                return True
            logger.error("Kismet API returned %d", r.status_code)
            return False
        except requests.RequestException as exc:
            logger.error("Cannot reach Kismet API at %s: %s", self._host, exc)
            return False

    def reset_auth(self) -> None:
        """Clear cached auth/session state so the next request re-authenticates."""
        self._authenticated = False
        self._session.auth = None
        self._session.cookies.clear()

    # -- WiFi RSSI by BSSID ------------------------------------------------

    def get_wifi_rssi(self, bssid: str) -> float | None:
        """Get last signal strength (dBm) for a WiFi device by MAC address.

        Returns None if the device is not currently seen.
        """
        if not self._ensure_auth():
            logger.warning("Kismet auth failed — skipping WiFi RSSI query")
            return None
        try:
            r = self._session.get(
                f"{self._host}/devices/by-mac/{bssid}/devices.json",
                timeout=self._timeout,
            )
            if r.status_code != 200:
                return None
            devices = r.json()
            if not devices:
                return None
            signal = devices[0].get("kismet.device.base.signal", {})
            rssi = signal.get("kismet.common.signal.last_signal")
            if rssi is not None and rssi != 0:
                return float(rssi)
            return None
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.debug("Kismet WiFi query error: %s", exc)
            return None

    # -- SDR RSSI by frequency ---------------------------------------------

    def get_sdr_rssi(
        self, target_freq_mhz: float, tolerance_mhz: float = 0.5,
    ) -> float | None:
        """Get signal strength for an SDR-detected device near *target_freq_mhz*.

        The NESDR Smart (R860) + LANA WB feed Kismet via rtl_433.
        Covers ~25 MHz-1750 MHz (433 MHz ISM, 915 MHz Crossfire, etc.).

        Kismet reports frequency in Hz (e.g. 915000000 for 915 MHz).
        Values are normalised to MHz before comparison.
        """
        if not self._ensure_auth():
            logger.warning("Kismet auth failed — skipping SDR RSSI query")
            return None
        try:
            r = self._session.get(
                f"{self._host}/devices/views/all/devices.json",
                params={
                    "KISMET": '{"fields": ['
                    '"kismet.device.base.signal/kismet.common.signal.last_signal",'
                    '"kismet.device.base.frequency",'
                    '"kismet.device.base.last_time"'
                    ']}'
                },
                timeout=self._timeout,
            )
            if r.status_code != 200:
                return None
            devices = r.json()
            best: float | None = None
            stale_cutoff = time.time() - 10  # ignore entries not seen in last 10s
            for dev in devices:
                # Skip stale entries — SDR can retain devices seen minutes ago.
                # last_time of 0 means not provided by this Kismet version — treat as fresh.
                last_time = dev.get("kismet.device.base.last_time", 0)
                if last_time != 0 and last_time < stale_cutoff:
                    continue
                freq = dev.get("kismet.device.base.frequency", 0)
                # Kismet reports frequency in Hz. Normalise to MHz.
                # Guard: values < 10_000 are already in MHz (manual config).
                if freq > 10_000:
                    freq_mhz = freq / 1e6
                else:
                    freq_mhz = float(freq)
                if abs(freq_mhz - target_freq_mhz) > tolerance_mhz:
                    continue
                signal = dev.get("kismet.device.base.signal", {})
                rssi = signal.get("kismet.common.signal.last_signal")
                if rssi and (best is None or rssi > best):
                    best = float(rssi)
            return best
        except (requests.RequestException, ValueError) as exc:
            logger.debug("Kismet SDR query error: %s", exc)
            return None

    # -- Unified getter ----------------------------------------------------

    def get_rssi(
        self,
        *,
        mode: str = "wifi",
        bssid: str | None = None,
        freq_mhz: float | None = None,
    ) -> float | None:
        """Unified RSSI getter — dispatches based on mode."""
        if mode == "wifi" and bssid:
            return self.get_wifi_rssi(bssid)
        if mode == "sdr" and freq_mhz is not None:
            return self.get_sdr_rssi(freq_mhz)
        return None

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()
