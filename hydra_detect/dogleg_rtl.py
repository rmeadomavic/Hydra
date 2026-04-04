"""Dogleg RTL — tactical return path that obscures the launch point."""

from __future__ import annotations

import logging
import math
import time
import threading

logger = logging.getLogger(__name__)


def compute_dogleg_waypoint(
    current_lat: float,
    current_lon: float,
    home_lat: float,
    home_lon: float,
    offset_distance_m: float = 200.0,
    offset_bearing: str = "perpendicular",
) -> tuple[float, float]:
    """Compute a dogleg waypoint between current position and home.

    Args:
        current_lat/lon: Current vehicle position
        home_lat/lon: Home/launch position
        offset_distance_m: How far offset from the direct line
        offset_bearing: "perpendicular" or a compass bearing in degrees

    Returns:
        (lat, lon) of the dogleg waypoint
    """
    # Bearing from current to home
    lat1 = math.radians(current_lat)
    lon1 = math.radians(current_lon)
    lat2 = math.radians(home_lat)
    lon2 = math.radians(home_lon)

    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing_to_home = math.atan2(x, y)

    # Perpendicular offset (90 degrees clockwise)
    if offset_bearing == "perpendicular":
        offset_rad = bearing_to_home + math.pi / 2
    else:
        try:
            offset_rad = math.radians(float(offset_bearing))
        except (ValueError, TypeError):
            logger.warning("Invalid dogleg bearing '%s', using perpendicular", offset_bearing)
            offset_rad = bearing_to_home + math.pi / 2

    # Compute offset point
    R = 6371000  # Earth radius metres
    d = offset_distance_m / R

    # Midpoint between current and home
    mid_lat = (current_lat + home_lat) / 2
    mid_lon = (current_lon + home_lon) / 2
    mid_lat_rad = math.radians(mid_lat)
    mid_lon_rad = math.radians(mid_lon)

    # Project from midpoint along offset bearing
    wp_lat = math.asin(
        math.sin(mid_lat_rad) * math.cos(d) +
        math.cos(mid_lat_rad) * math.sin(d) * math.cos(offset_rad)
    )
    wp_lon = mid_lon_rad + math.atan2(
        math.sin(offset_rad) * math.sin(d) * math.cos(mid_lat_rad),
        math.cos(d) - math.sin(mid_lat_rad) * math.sin(wp_lat)
    )

    return (math.degrees(wp_lat), math.degrees(wp_lon))


class DoglegRTL:
    """Execute a dogleg return to launch via an offset waypoint."""

    def __init__(
        self,
        mavlink,
        home_lat: float,
        home_lon: float,
        offset_distance_m: float = 200.0,
        offset_bearing: str = "perpendicular",
        climb_altitude_m: float = 50.0,
    ):
        self._mavlink = mavlink
        self._home_lat = home_lat
        self._home_lon = home_lon
        self._offset_distance_m = offset_distance_m
        self._offset_bearing = offset_bearing
        self._climb_alt = climb_altitude_m
        self._phase = "idle"  # idle -> climb -> offset -> home -> done
        self._lock = threading.Lock()

    def execute(self) -> bool:
        """Begin dogleg RTL sequence. Returns True if started."""
        pos = self._mavlink.get_lat_lon()
        if pos is None or pos[0] is None:
            logger.error("DoglegRTL: no GPS position")
            return False

        lat, lon, alt = pos

        # Compute offset waypoint
        wp_lat, wp_lon = compute_dogleg_waypoint(
            lat, lon,
            self._home_lat, self._home_lon,
            self._offset_distance_m,
            self._offset_bearing,
        )

        # Execute sequence in background thread
        def _run():
            try:
                # Climb to configured altitude before proceeding to offset
                if self._climb_alt > 0:
                    cur = self._mavlink.get_lat_lon()
                    if cur and cur[0] is not None:
                        with self._lock:
                            self._phase = "climb"
                        logger.info("DoglegRTL: climbing to %.0fm", self._climb_alt)
                        self._mavlink.command_guided_to(
                            cur[0], cur[1], alt=self._climb_alt,
                        )
                        # Poll altitude until climb target reached (max 25s)
                        for _ in range(50):  # 25s max (50 * 0.5s)
                            if (hasattr(self, "_stop_evt")
                                    and self._stop_evt
                                    and self._stop_evt.is_set()):
                                return
                            cur = self._mavlink.get_lat_lon()
                            if cur and cur[2] is not None and cur[2] >= self._climb_alt * 0.9:
                                break
                            time.sleep(0.5)

                with self._lock:
                    self._phase = "offset"
                logger.info("DoglegRTL: flying to offset point (%.5f, %.5f)", wp_lat, wp_lon)
                self._mavlink.command_guided_to(wp_lat, wp_lon)

                # Wait for vehicle to reach offset (poll position)
                from .autonomous import haversine_m
                for _ in range(120):  # Max 60 seconds at 0.5s intervals
                    time.sleep(0.5)
                    pos = self._mavlink.get_lat_lon()
                    if pos and pos[0] is not None:
                        dist = haversine_m(pos[0], pos[1], wp_lat, wp_lon)
                        if dist < 10:
                            break
                else:
                    logger.warning(
                        "DoglegRTL: offset waypoint not reached within 60s -- proceeding to RTL"
                    )

                # Now fly home via SMART_RTL
                with self._lock:
                    self._phase = "home"
                logger.info("DoglegRTL: heading home (%.5f, %.5f)", self._home_lat, self._home_lon)
                self._mavlink.set_mode("SMART_RTL")

            except Exception as exc:
                logger.error("DoglegRTL failed: %s", exc, exc_info=True)
                try:
                    self._mavlink.set_mode("RTL")
                except Exception:
                    logger.error("DoglegRTL: fallback RTL command also failed", exc_info=True)
            finally:
                with self._lock:
                    self._phase = "done"

        threading.Thread(target=_run, daemon=True, name="dogleg-rtl").start()
        return True

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase
