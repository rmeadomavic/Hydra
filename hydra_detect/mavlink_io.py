"""MAVLink connection, status-text alerts, and vehicle commands."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class MAVLinkIO:
    """Manages a MAVLink connection for alerts and vehicle commands."""

    def __init__(
        self,
        connection_string: str = "udpin:0.0.0.0:14550",
        alert_statustext: bool = True,
        alert_interval_sec: float = 5.0,
        auto_loiter: bool = False,
        guided_roi: bool = False,
    ):
        self._conn_str = connection_string
        self._alert_statustext = alert_statustext
        self._alert_interval = alert_interval_sec
        self._auto_loiter = auto_loiter
        self._guided_roi = guided_roi

        self._mav = None
        self._last_alert_time: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """Establish MAVLink connection."""
        try:
            from pymavlink import mavutil

            logger.info("Connecting MAVLink: %s", self._conn_str)
            self._mav = mavutil.mavlink_connection(self._conn_str)
            self._mav.wait_heartbeat(timeout=10)
            logger.info(
                "MAVLink heartbeat from system %d component %d",
                self._mav.target_system,
                self._mav.target_component,
            )
            return True
        except Exception as exc:
            logger.error("MAVLink connection failed: %s", exc)
            return False

    def close(self) -> None:
        """Close MAVLink connection."""
        if self._mav is not None:
            self._mav.close()
            self._mav = None

    # ------------------------------------------------------------------
    def send_statustext(self, text: str, severity: int = 6) -> None:
        """Send a STATUSTEXT message to the GCS.

        Severity 6 = MAV_SEVERITY_INFO.
        """
        if self._mav is None:
            return
        with self._lock:
            try:
                self._mav.mav.statustext_send(severity, text.encode("utf-8")[:50])
            except Exception as exc:
                logger.warning("Failed to send STATUSTEXT: %s", exc)

    def alert_detection(self, label: str, count: int) -> None:
        """Rate-limited detection alert via STATUSTEXT."""
        now = time.time()
        if now - self._last_alert_time < self._alert_interval:
            return
        self._last_alert_time = now

        msg = f"HYDRA: {count}x {label} detected"
        if self._alert_statustext:
            self.send_statustext(msg)
            logger.info("Alert sent: %s", msg)

    # ------------------------------------------------------------------
    def command_loiter(self) -> None:
        """Switch vehicle to LOITER mode."""
        if self._mav is None or not self._auto_loiter:
            return
        try:
            from pymavlink import mavutil

            self._mav.set_mode_apm(self._mav.mode_mapping()["LOITER"])
            logger.info("Vehicle set to LOITER mode.")
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
