"""Send CAMERA_TRACKING_GEO_STATUS for GCS map integration."""

from __future__ import annotations

import logging
import math
import time
from .mavlink_io import MAVLinkIO
from .tracker import TrackingResult

logger = logging.getLogger(__name__)


class GeoTracker:
    """Encodes and sends CAMERA_TRACKING_GEO_STATUS (message ID 275)."""

    def __init__(
        self,
        mavlink_io: MAVLinkIO,
        camera_hfov_deg: float = 60.0,
        min_interval: float = 2.0,
    ):
        self._mav = mavlink_io
        self._hfov = camera_hfov_deg
        self._last_send = 0.0
        self._min_interval = min_interval

    def send(
        self,
        tracks: TrackingResult,
        alert_classes: set[str] | None,
        locked_track_id: int | None,
    ) -> None:
        """Pick the best target and send its geo position to the GCS."""
        now = time.monotonic()
        if (now - self._last_send) < self._min_interval:
            return

        # Pick target: locked track first, then highest-confidence in alert filter
        target = None
        if locked_track_id is not None:
            target = tracks.find(locked_track_id)

        if target is None:
            best = None
            for t in tracks:
                if alert_classes is not None and t.label not in alert_classes:
                    continue
                if best is None or t.confidence > best.confidence:
                    best = t
            target = best

        if target is None:
            return

        # Estimate distance from altitude + camera geometry
        _, _, alt = self._mav.get_lat_lon()
        if alt is None or alt <= 0:
            return

        # Rough ground distance: alt / tan(vfov/2)
        # Assume vfov ~ hfov * 0.75 (4:3 aspect ratio approximation)
        vfov_rad = math.radians(self._hfov * 0.75) / 2.0
        if vfov_rad <= 0:
            return
        est_distance = alt / math.tan(vfov_rad)

        # Compute bearing from frame position
        frame_cx = (target.x1 + target.x2) / 2.0
        error_x = (frame_cx - 320.0) / 320.0  # Normalise to -1..+1

        pos = self._mav.estimate_target_position(
            error_x, approach_distance_m=est_distance, camera_hfov_deg=self._hfov,
        )
        if pos is None:
            return

        target_lat, target_lon = pos
        self._last_send = now
        is_locked = (locked_track_id is not None and target.track_id == locked_track_id)

        self._send_message(target_lat, target_lon, alt, is_locked)

    def _send_message(
        self, lat: float, lon: float, alt: float, is_locked: bool,
    ) -> None:
        """Encode and send CAMERA_TRACKING_GEO_STATUS."""
        if self._mav._mav is None:
            return
        try:
            from pymavlink.dialects.v20 import common as mavlink2

            status = 1 if is_locked else 2  # ACTIVE vs SEARCHING
            nan = float("nan")

            msg = mavlink2.MAVLink_camera_tracking_geo_status_message(
                tracking_status=status,
                lat=int(lat * 1e7),
                lon=int(lon * 1e7),
                alt=alt,
                h_acc=nan,
                v_acc=nan,
                vel_n=nan,
                vel_e=nan,
                vel_d=nan,
                hdg=nan,
                vel_acc=nan,
                dist=nan,
                hdg_acc=nan,
            )
            self._mav._mav.mav.send(msg, force_mavlink1=False)
            logger.debug("GEO_STATUS sent: %.6f, %.6f (locked=%s)", lat, lon, is_locked)
        except Exception as exc:
            logger.warning("Failed to send CAMERA_TRACKING_GEO_STATUS: %s", exc)
