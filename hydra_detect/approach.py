"""Unified approach controller — drives vehicle toward a tracked target."""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mavlink_io import MAVLinkIO
    from .tracker import TrackedObject

logger = logging.getLogger(__name__)


class ApproachMode(Enum):
    IDLE = "idle"
    FOLLOW = "follow"
    DROP = "drop"
    STRIKE = "strike"


class ApproachMethod(Enum):
    GPS_WAYPOINT = "gps_waypoint"
    RC_OVERRIDE = "rc_override"
    HYBRID = "hybrid"


@dataclass
class ApproachConfig:
    """Configuration for approach behavior."""

    method: ApproachMethod = ApproachMethod.GPS_WAYPOINT
    follow_speed_max: float = 3.0       # m/s max speed in follow
    follow_min_distance: float = 5.0    # metres — don't close further
    update_hz: float = 2.0              # waypoint update rate
    speed_scale_far: float = 1.0        # speed multiplier when far
    speed_scale_near: float = 0.3       # speed multiplier when close
    near_threshold_px: float = 0.4      # bbox area ratio considered "near"


class ApproachController:
    """Drives the vehicle toward a locked target using configurable methods.

    The approach controller is the execution layer — it handles the HOW of
    getting to a target.  The autonomous controller handles the WHAT and WHEN
    (safety gates, qualification).  Pipeline wires them together.
    """

    def __init__(
        self,
        mavlink: MAVLinkIO,
        config: ApproachConfig | None = None,
    ):
        self._mavlink = mavlink
        self._cfg = config or ApproachConfig()
        self._mode = ApproachMode.IDLE
        self._target_track_id: int | None = None
        self._lock = threading.Lock()

        # State for speed control
        self._last_bbox_area: float = 0.0    # normalised bbox area (0-1)
        self._last_update: float = 0.0
        self._pre_approach_mode: str | None = None  # vehicle mode before approach

        # Stats
        self._waypoints_sent: int = 0
        self._active_since: float | None = None

    @property
    def mode(self) -> ApproachMode:
        return self._mode

    @property
    def active(self) -> bool:
        return self._mode != ApproachMode.IDLE

    def start_follow(self, track_id: int) -> bool:
        """Begin following a tracked target.  Returns True if started."""
        with self._lock:
            if self._mode != ApproachMode.IDLE:
                logger.warning(
                    "Approach already active in %s mode", self._mode.value,
                )
                return False

            # Save current vehicle mode for restoration on abort
            self._pre_approach_mode = self._mavlink.get_vehicle_mode()
            self._target_track_id = track_id
            self._mode = ApproachMode.FOLLOW
            self._waypoints_sent = 0
            self._active_since = time.monotonic()
            self._last_update = 0.0

            logger.info("Follow mode STARTED for track #%d", track_id)
            return True

    def abort(self) -> None:
        """Stop all approach activity and safe the vehicle."""
        with self._lock:
            if self._mode == ApproachMode.IDLE:
                return

            prev_mode = self._mode
            self._mode = ApproachMode.IDLE
            self._target_track_id = None

            # Restore vehicle mode or go to LOITER/HOLD
            try:
                self._mavlink.set_mode("LOITER")
            except Exception:
                pass

            logger.info(
                "Approach ABORTED from %s mode — vehicle set to LOITER",
                prev_mode.value,
            )

    def update(
        self,
        track: TrackedObject | None,
        frame_width: int,
        frame_height: int,
    ) -> None:
        """Called every frame with the current track state.

        If track is None, the target is lost — hold position.
        """
        if self._mode == ApproachMode.IDLE:
            return

        now = time.monotonic()
        interval = 1.0 / self._cfg.update_hz

        if now - self._last_update < interval:
            return
        self._last_update = now

        if self._mode == ApproachMode.FOLLOW:
            self._update_follow(track, frame_width, frame_height)

    def _update_follow(
        self,
        track: TrackedObject | None,
        fw: int,
        fh: int,
    ) -> None:
        """Update follow mode — send waypoint toward target."""
        if track is None:
            # Target lost — hold position
            logger.debug("Follow: target lost, holding position")
            return

        # Compute normalised centre offset for bearing estimation
        cx, cy = track.center
        error_x = (cx - fw / 2.0) / (fw / 2.0)  # -1..+1

        # Estimate target position using existing MAVLink helper
        target_pos = self._mavlink.estimate_target_position(error_x)
        if target_pos is None:
            return

        target_lat, target_lon = target_pos

        # Speed control based on bounding box size
        bbox_area = (
            (track.x2 - track.x1) * (track.y2 - track.y1)
        ) / (fw * fh)
        self._last_bbox_area = bbox_area

        # Scale speed: large bbox = close = slow, small bbox = far = fast
        if bbox_area > self._cfg.near_threshold_px:
            speed = self._cfg.follow_speed_max * self._cfg.speed_scale_near
        else:
            # Linear interpolation between near and far speeds
            t = min(1.0, bbox_area / self._cfg.near_threshold_px)
            scale = (
                self._cfg.speed_scale_far
                + t * (self._cfg.speed_scale_near - self._cfg.speed_scale_far)
            )
            speed = self._cfg.follow_speed_max * scale

        # Check minimum distance (using bbox as proxy)
        if bbox_area > 0.5:  # Very close — more than half the frame
            logger.debug(
                "Follow: target very close (bbox=%.2f), holding", bbox_area,
            )
            return

        # Send speed command
        try:
            self._mavlink.command_do_change_speed(speed)
        except Exception as exc:
            logger.debug("Follow: speed command failed: %s", exc)

        # Send waypoint
        try:
            self._mavlink.command_guided_to(target_lat, target_lon)
            self._waypoints_sent += 1
        except Exception as exc:
            logger.warning("Follow: guided waypoint failed: %s", exc)

    def get_status(self) -> dict:
        """Return current approach status for web API."""
        return {
            "mode": self._mode.value,
            "active": self.active,
            "target_track_id": self._target_track_id,
            "method": self._cfg.method.value,
            "waypoints_sent": self._waypoints_sent,
            "bbox_area": round(self._last_bbox_area, 3),
            "active_since": self._active_since,
        }
