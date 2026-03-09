"""MAVLink connection, GPS tracking, status-text alerts, and vehicle commands."""

from __future__ import annotations

import datetime
import logging
import math
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

        # GPS state (lat/lon/alt in MAVLink int format, hdg in centidegrees)
        self._gps: Dict[str, Any] = {
            "lat": None, "lon": None, "alt": None, "fix": 0, "hdg": None,
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
                        self._gps["hdg"] = msg.hdg  # centidegrees
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
        if self._mav is None:
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

    def clear_roi(self) -> None:
        """Clear any active ROI / gimbal lock."""
        if self._mav is None:
            return
        try:
            from pymavlink import mavutil

            self._mav.mav.command_long_send(
                self._mav.target_system,
                self._mav.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_ROI_NONE,
                0, 0, 0, 0, 0, 0, 0, 0,
            )
            logger.info("ROI cleared.")
        except Exception as exc:
            logger.warning("Failed to clear ROI: %s", exc)

    # ------------------------------------------------------------------
    # Keep-in-frame: yaw the vehicle to center the target in camera
    # ------------------------------------------------------------------
    def adjust_yaw(self, error_x: float, yaw_rate_max: float = 30.0) -> None:
        """Adjust vehicle yaw to center a target in the camera frame.

        Args:
            error_x: Normalised horizontal error from frame center.
                     -1.0 = target is at left edge, +1.0 = right edge, 0 = centered.
            yaw_rate_max: Maximum yaw rate in degrees/second.
        """
        if self._mav is None:
            return

        # Proportional yaw correction: positive error = target is right = yaw right
        yaw_rate = error_x * yaw_rate_max

        # Clamp
        yaw_rate = max(-yaw_rate_max, min(yaw_rate_max, yaw_rate))

        # Dead zone: don't send tiny corrections
        if abs(error_x) < 0.05:
            return

        try:
            from pymavlink import mavutil

            # CONDITION_YAW: param1=target_angle, param2=yaw_speed, param3=direction, param4=relative
            # We use relative yaw: small incremental adjustments each frame
            direction = 1 if yaw_rate >= 0 else -1  # 1=CW, -1=CCW
            angle = abs(yaw_rate) * 0.1  # Small step per call (~100ms frame interval)
            self._mav.mav.command_long_send(
                self._mav.target_system,
                self._mav.target_component,
                mavutil.mavlink.MAV_CMD_CONDITION_YAW,
                0,  # confirmation
                angle,            # param1: target angle (degrees)
                abs(yaw_rate),    # param2: yaw speed (deg/s)
                direction,        # param3: direction (1=CW, -1=CCW)
                1,                # param4: 1=relative, 0=absolute
                0, 0, 0,
            )
        except Exception as exc:
            logger.warning("Yaw adjust failed: %s", exc)

    # ------------------------------------------------------------------
    # Strike: navigate to estimated target position
    # ------------------------------------------------------------------
    def get_heading_deg(self) -> Optional[float]:
        """Return vehicle heading in degrees (0-360), or None."""
        with self._gps_lock:
            if self._gps["hdg"] is not None:
                return self._gps["hdg"] / 100.0
        return None

    def command_guided_to(self, lat: float, lon: float, alt: Optional[float] = None) -> bool:
        """Switch to GUIDED mode and navigate to a GPS coordinate.

        Args:
            lat: Target latitude (decimal degrees).
            lon: Target longitude (decimal degrees).
            alt: Target altitude in metres. None = maintain current altitude.

        Returns:
            True if command was sent successfully.
        """
        if self._mav is None:
            return False
        try:
            from pymavlink import mavutil

            # Switch to GUIDED mode
            mode_map = self._mav.mode_mapping()
            if "GUIDED" in mode_map:
                self._mav.set_mode_apm(mode_map["GUIDED"])
            else:
                logger.warning("GUIDED mode not available in mode mapping.")
                return False

            # Use current altitude if not specified
            if alt is None:
                _, _, cur_alt = self.get_lat_lon()
                alt = cur_alt if cur_alt is not None else 0.0

            # Send position target
            self._mav.mav.set_position_target_global_int_send(
                0,  # time_boot_ms (not used)
                self._mav.target_system,
                self._mav.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                0b0000111111111000,  # type_mask: use only lat/lon/alt
                int(lat * 1e7),     # lat_int
                int(lon * 1e7),     # lon_int
                alt,                # alt (metres)
                0, 0, 0,            # vx, vy, vz (ignored)
                0, 0, 0,            # afx, afy, afz (ignored)
                0, 0,               # yaw, yaw_rate (ignored)
            )
            logger.info("GUIDED to %.6f, %.6f alt=%.1fm", lat, lon, alt)
            self.send_statustext(f"STRIKE: GUIDED to {lat:.5f},{lon:.5f}", severity=2)
            return True

        except Exception as exc:
            logger.error("GUIDED command failed: %s", exc)
            return False

    def estimate_target_position(
        self,
        error_x: float,
        approach_distance_m: float = 20.0,
        camera_hfov_deg: float = 60.0,
    ) -> Optional[tuple[float, float]]:
        """Estimate a target's GPS position from its camera frame offset.

        Uses vehicle GPS + heading + target's horizontal offset to compute
        a bearing, then projects a waypoint at the given approach distance.

        Args:
            error_x: Normalised horizontal offset (-1.0 to +1.0).
            approach_distance_m: Distance in metres to project the waypoint.
            camera_hfov_deg: Camera horizontal field of view in degrees.

        Returns:
            (lat, lon) tuple or None if GPS/heading unavailable.
        """
        lat, lon, _ = self.get_lat_lon()
        heading = self.get_heading_deg()
        if lat is None or heading is None:
            return None

        # Compute target bearing: vehicle heading + camera offset
        angle_offset = error_x * (camera_hfov_deg / 2.0)
        bearing_deg = (heading + angle_offset) % 360.0
        bearing_rad = math.radians(bearing_deg)

        # Project point at approach_distance along bearing (flat earth approx, good for <1km)
        d = approach_distance_m
        R = 6371000.0  # Earth radius in metres
        lat_rad = math.radians(lat)

        dlat = (d * math.cos(bearing_rad)) / R
        dlon = (d * math.sin(bearing_rad)) / (R * math.cos(lat_rad))

        target_lat = lat + math.degrees(dlat)
        target_lon = lon + math.degrees(dlon)

        return (target_lat, target_lon)

    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._mav is not None
