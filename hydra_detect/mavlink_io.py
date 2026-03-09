"""MAVLink connection, GPS tracking, status-text alerts, and vehicle commands."""

from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MAVLinkIO:
    """Manages a MAVLink connection for alerts and vehicle commands.

    Features:
    - GPS listener thread (GLOBAL_POSITION_INT / GPS_RAW_INT)
    - Per-label alert throttling with configurable interval
    - MGRS coordinate formatting (falls back to lat/lon)
    - STATUSTEXT alerts to GCS
    - Vehicle commands: LOITER mode, ROI targeting
    """

    def __init__(
        self,
        connection_string: str = "/dev/ttyACM0",
        baud: int = 115200,
        source_system: int = 1,
        alert_statustext: bool = True,
        alert_interval_sec: float = 5.0,
        severity: int = 2,
        min_gps_fix: int = 3,
        auto_loiter: bool = False,
        guided_roi: bool = False,
    ):
        self._conn_str = connection_string
        self._baud = baud
        self._source_system = source_system
        self._alert_statustext = alert_statustext
        self._alert_interval = alert_interval_sec
        self._severity = max(0, min(severity, 7))
        self._min_gps_fix = min_gps_fix
        self._auto_loiter = auto_loiter
        self._guided_roi = guided_roi

        self._mav = None
        self._last_alert_times: Dict[str, float] = {}
        self._lock = threading.Lock()

        # GPS state
        self._gps: Dict[str, Any] = {
            "lat": None, "lon": None, "alt": None, "fix": 0,
        }
        self._gps_lock = threading.Lock()
        self._stop_evt = threading.Event()

        # MGRS converter (optional)
        self._mgrs = None
        try:
            import mgrs
            self._mgrs = mgrs.MGRS()
        except ImportError:
            logger.info("mgrs library not installed; using lat/lon format.")

    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """Establish MAVLink connection and start GPS listener."""
        try:
            from pymavlink import mavutil

            logger.info("Connecting MAVLink: %s @ %d baud", self._conn_str, self._baud)
            self._mav = mavutil.mavlink_connection(
                self._conn_str,
                baud=self._baud,
                source_system=self._source_system,
                autoreconnect=True,
            )
            self._mav.wait_heartbeat(timeout=10)
            logger.info(
                "MAVLink heartbeat from system %d component %d",
                self._mav.target_system,
                self._mav.target_component,
            )

            # Request GPS data stream
            from pymavlink.dialects.v20 import common as mavlink2
            self._mav.mav.request_data_stream_send(
                self._mav.target_system,
                self._mav.target_component,
                mavlink2.MAV_DATA_STREAM_POSITION,
                2,  # 2 Hz
                1,  # start
            )

            # Start GPS listener thread
            self._stop_evt.clear()
            threading.Thread(
                target=self._gps_listener, daemon=True, name="mav-gps"
            ).start()

            return True
        except Exception as exc:
            logger.error("MAVLink connection failed: %s", exc)
            return False

    def close(self) -> None:
        """Stop GPS listener and close MAVLink connection."""
        self._stop_evt.set()
        if self._mav is not None:
            self._mav.close()
            self._mav = None

    # ------------------------------------------------------------------
    # GPS listener
    # ------------------------------------------------------------------
    def _gps_listener(self) -> None:
        """Background thread: read GPS messages and update state."""
        while not self._stop_evt.is_set() and self._mav is not None:
            try:
                msg = self._mav.recv_match(
                    type=["GLOBAL_POSITION_INT", "GPS_RAW_INT"],
                    timeout=1,
                )
                if msg is None:
                    continue
                with self._gps_lock:
                    if msg.get_type() == "GLOBAL_POSITION_INT":
                        self._gps["lat"] = msg.lat
                        self._gps["lon"] = msg.lon
                        self._gps["alt"] = msg.alt
                    else:
                        self._gps["fix"] = msg.fix_type
            except Exception as exc:
                logger.warning("GPS listener error: %s", exc)
                time.sleep(0.5)

    def get_gps(self) -> Dict[str, Any]:
        """Return current GPS state (thread-safe copy)."""
        with self._gps_lock:
            return dict(self._gps)

    @property
    def gps_fix_ok(self) -> bool:
        with self._gps_lock:
            return self._gps["fix"] >= self._min_gps_fix

    def get_position_string(self) -> Optional[str]:
        """Return MGRS or lat/lon string if GPS fix is good, else None."""
        with self._gps_lock:
            if self._gps["fix"] < self._min_gps_fix or self._gps["lat"] is None:
                return None
            lat = self._gps["lat"] / 1e7
            lon = self._gps["lon"] / 1e7

        if self._mgrs is not None:
            try:
                return self._mgrs.toMGRS(lat, lon)
            except Exception:
                pass
        return f"{lat:.5f},{lon:.5f}"

    def get_lat_lon(self) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Return (lat, lon, alt) in decimal degrees / metres, or Nones."""
        with self._gps_lock:
            if self._gps["fix"] < self._min_gps_fix or self._gps["lat"] is None:
                return None, None, None
            return (
                self._gps["lat"] / 1e7,
                self._gps["lon"] / 1e7,
                self._gps["alt"] / 1000,
            )

    # ------------------------------------------------------------------
    # STATUSTEXT alerts
    # ------------------------------------------------------------------
    def send_statustext(self, text: str, severity: Optional[int] = None) -> None:
        """Send a STATUSTEXT message to the GCS."""
        if self._mav is None:
            return
        sev = severity if severity is not None else self._severity
        with self._lock:
            try:
                from pymavlink.dialects.v20 import common as mavlink2
                payload = text[:50].ljust(50, '\0').encode('utf-8')
                msg = mavlink2.MAVLink_statustext_message(severity=sev, text=payload)
                msg._header.srcSystem = self._source_system
                msg._header.srcComponent = mavlink2.MAV_COMP_ID_ONBOARD_COMPUTER
                self._mav.mav.send(msg, force_mavlink1=False)
            except Exception as exc:
                logger.warning("Failed to send STATUSTEXT: %s", exc)

    def alert_detection(self, label: str, confidence: float = 0.0) -> None:
        """Rate-limited per-label detection alert with geo-coordinates."""
        now = time.time()

        # Per-label throttling
        last = self._last_alert_times.get(label, 0.0)
        if (now - last) < self._alert_interval:
            logger.debug(
                "Skipping duplicate alert for %s (last %.1fs ago)", label, now - last
            )
            return
        self._last_alert_times[label] = now

        # Build alert message with DTG and coordinates
        dtg = datetime.datetime.utcnow().strftime("%Y%m%d %H%MZ")
        coord = self.get_position_string()

        if coord is not None:
            msg = f"Detection: {label} {dtg} @ {coord}"
        else:
            msg = f"Detection: {label} {dtg}"

        if self._alert_statustext:
            self.send_statustext(msg)
            logger.info("Alert sent: %s", msg)

    # ------------------------------------------------------------------
    # Vehicle commands
    # ------------------------------------------------------------------
    def command_loiter(self) -> None:
        """Switch vehicle to LOITER mode (HOLD for Rover)."""
        if self._mav is None or not self._auto_loiter:
            return
        try:
            mode_map = self._mav.mode_mapping()
            # Try LOITER first (Copter), then HOLD (Rover)
            for mode_name in ("LOITER", "HOLD"):
                if mode_name in mode_map:
                    self._mav.set_mode_apm(mode_map[mode_name])
                    logger.info("Vehicle set to %s mode.", mode_name)
                    return
            logger.warning("No LOITER/HOLD mode found in mode mapping.")
        except Exception as exc:
            logger.warning("Failed to set LOITER: %s", exc)

    def set_roi(self, lat: float, lon: float, alt: float = 0.0) -> None:
        """Point camera gimbal at a GPS coordinate via MAV_CMD_DO_SET_ROI."""
        if self._mav is None or not self._guided_roi:
            return
        try:
            from pymavlink import mavutil

            self._mav.mav.command_long_send(
                self._mav.target_system,
                self._mav.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_ROI_LOCATION,
                0,  # confirmation
                0, 0, 0, 0,  # params 1-4 unused
                lat, lon, alt,
            )
            logger.info("ROI set to %.6f, %.6f", lat, lon)
        except Exception as exc:
            logger.warning("Failed to set ROI: %s", exc)

    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._mav is not None
