"""Approach controller — Follow, Drop, Strike, and Pixel-Lock modes for target engagement."""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass

from .guidance import GuidanceConfig, GuidanceController

logger = logging.getLogger(__name__)
audit_log = logging.getLogger("hydra.audit")


class ApproachMode(enum.Enum):
    IDLE = "idle"
    FOLLOW = "follow"
    DROP = "drop"
    STRIKE = "strike"
    PIXEL_LOCK = "pixel_lock"


@dataclass
class ApproachConfig:
    """Tunables loaded from config.ini at pipeline init."""

    # Follow mode
    follow_speed_min: float = 2.0
    follow_speed_max: float = 10.0
    follow_distance_m: float = 15.0
    follow_yaw_rate_max: float = 30.0

    # Drop mode
    drop_channel: int | None = None
    drop_pwm_release: int = 1900
    drop_pwm_hold: int = 1100
    drop_duration: float = 1.0
    drop_distance_m: float = 3.0

    # Strike mode
    arm_channel: int | None = None
    arm_pwm_armed: int = 1900
    arm_pwm_safe: int = 1100
    hw_arm_channel: int | None = None

    # Pixel-lock guidance
    guidance_cfg: GuidanceConfig | None = None

    # Shared
    camera_hfov_deg: float = 60.0
    abort_mode: str = "LOITER"  # Mode to switch to on abort
    waypoint_interval: float = 0.5  # Min seconds between waypoint sends


class ApproachController:
    """Manages Follow, Drop, and Strike approach modes.

    Thread safety: all public methods acquire ``_lock`` before mutating state.
    The ``update()`` method is called once per frame from the pipeline hot loop;
    it must be fast and never block.
    """

    def __init__(self, mavlink, cfg: ApproachConfig):
        self._mavlink = mavlink
        self._cfg = cfg
        self._lock = threading.Lock()

        # State
        self._mode: ApproachMode = ApproachMode.IDLE
        self._running: bool = False
        self._target_track_id: int | None = None
        self._pre_approach_mode: str | None = None
        self._active_since: float = 0.0
        self._waypoints_sent: int = 0
        self._last_wp_time: float = 0.0

        # Drop state
        self._drop_complete: bool = False
        self._target_lat: float = 0.0
        self._target_lon: float = 0.0

        # Pixel-lock guidance controller
        self._guidance = GuidanceController(cfg.guidance_cfg)
        self._last_vel_log_time: float = 0.0

    # ------------------------------------------------------------------
    # Public — start modes
    # ------------------------------------------------------------------

    def start_follow(self, track_id: int) -> bool:
        """Begin follow approach — continuous tracking with speed scaling."""
        with self._lock:
            if self._mode != ApproachMode.IDLE:
                return False
            self._pre_approach_mode = self._mavlink.get_vehicle_mode()
            self._target_track_id = track_id
            self._mode = ApproachMode.FOLLOW
            self._running = True
            self._active_since = time.monotonic()
            self._waypoints_sent = 0
            logger.info("Follow mode STARTED for track #%d", track_id)
            return True

    def start_drop(self, track_id: int, target_lat: float, target_lon: float) -> bool:
        """Begin drop approach to a GPS position."""
        with self._lock:
            if self._mode != ApproachMode.IDLE:
                return False
            self._pre_approach_mode = self._mavlink.get_vehicle_mode()
            self._target_track_id = track_id
            self._target_lat = target_lat
            self._target_lon = target_lon
            self._mode = ApproachMode.DROP
            self._running = True
            self._active_since = time.monotonic()
            self._drop_complete = False
            self._waypoints_sent = 0

            # Send initial waypoint
            self._mavlink.command_guided_to(target_lat, target_lon)
            self._waypoints_sent += 1
            logger.info(
                "Drop mode STARTED for track #%d at %.5f, %.5f",
                track_id, target_lat, target_lon,
            )
            audit_log.info(
                "APPROACH DROP START: track_id=%d lat=%.5f lon=%.5f",
                track_id, target_lat, target_lon,
            )
            return True

    def start_strike(self, track_id: int) -> bool:
        """Begin strike approach — continuous tracking at max speed with arm."""
        with self._lock:
            if self._mode != ApproachMode.IDLE:
                return False
            self._pre_approach_mode = self._mavlink.get_vehicle_mode()
            self._target_track_id = track_id
            self._mode = ApproachMode.STRIKE
            self._running = True
            self._active_since = time.monotonic()
            self._waypoints_sent = 0

            # Arm software trigger
            if self._cfg.arm_channel:
                self._mavlink.set_servo(
                    self._cfg.arm_channel, self._cfg.arm_pwm_armed,
                )
                logger.info(
                    "Strike software ARM engaged: ch=%d pwm=%d",
                    self._cfg.arm_channel, self._cfg.arm_pwm_armed,
                )

            logger.info("Strike mode STARTED for track #%d", track_id)
            audit_log.info("APPROACH STRIKE START: track_id=%d", track_id)
            return True

    def start_pixel_lock(self, track_id: int) -> bool:
        """Begin pixel-lock approach — continuous velocity-based visual servoing."""
        with self._lock:
            if self._mode != ApproachMode.IDLE:
                return False
            self._pre_approach_mode = self._mavlink.get_vehicle_mode()
            self._target_track_id = track_id
            self._mode = ApproachMode.PIXEL_LOCK
            self._running = True
            self._active_since = time.monotonic()
            self._waypoints_sent = 0

        # Switch to GUIDED mode for velocity commands
        try:
            ok = self._mavlink.set_mode("GUIDED")
            if not ok:
                logger.warning(
                    "Pixel-lock: GUIDED mode switch failed — "
                    "vehicle may not respond to velocity commands"
                )
        except Exception as exc:
            logger.warning("Pixel-lock: GUIDED mode switch error: %s", exc)

        self._guidance.start()
        logger.info("Pixel-lock mode STARTED for track #%d", track_id)
        audit_log.info("APPROACH PIXEL_LOCK START: track_id=%d", track_id)
        return True

    # ------------------------------------------------------------------
    # Public — update (called once per frame from pipeline)
    # ------------------------------------------------------------------

    def update(self, track, frame_w: int, frame_h: int) -> None:
        """Update the active approach with current tracking data.

        Args:
            track: The TrackedObject for the locked target, or None if lost.
            frame_w: Frame width in pixels.
            frame_h: Frame height in pixels.
        """
        with self._lock:
            mode = self._mode
            if mode == ApproachMode.IDLE:
                return

        if mode == ApproachMode.FOLLOW:
            self._update_follow(track, frame_w, frame_h)
        elif mode == ApproachMode.DROP:
            self._update_drop(track, frame_w, frame_h)
        elif mode == ApproachMode.STRIKE:
            self._update_strike(track, frame_w, frame_h)
        elif mode == ApproachMode.PIXEL_LOCK:
            self._update_pixel_lock(track, frame_w, frame_h)

    # ------------------------------------------------------------------
    # Public — abort
    # ------------------------------------------------------------------

    def abort(self) -> None:
        """Abort the current approach and safe all channels."""
        with self._lock:
            prev_mode = self._mode
            self._mode = ApproachMode.IDLE
            self._running = False
            track_id = self._target_track_id
            self._target_track_id = None

        if prev_mode == ApproachMode.IDLE:
            return

        # Stop guidance controller if pixel-lock was active
        if prev_mode == ApproachMode.PIXEL_LOCK:
            self._guidance.stop()
            # Send zero velocity to brake
            try:
                self._mavlink.send_velocity_ned(0, 0, 0, 0)
            except Exception:
                pass

        # Safe the arm channel
        if self._cfg.arm_channel:
            try:
                self._mavlink.set_servo(
                    self._cfg.arm_channel, self._cfg.arm_pwm_safe,
                )
            except Exception:
                pass

        # Safe the drop channel
        if self._cfg.drop_channel:
            try:
                self._mavlink.set_servo(
                    self._cfg.drop_channel, self._cfg.drop_pwm_hold,
                )
            except Exception:
                pass

        # Switch to abort mode (LOITER/HOLD)
        try:
            self._mavlink.set_mode(self._cfg.abort_mode)
        except Exception:
            pass

        logger.warning(
            "Approach ABORTED: was %s for track #%s",
            prev_mode.value, track_id,
        )
        audit_log.info(
            "APPROACH ABORT: mode=%s track_id=%s", prev_mode.value, track_id,
        )

    # ------------------------------------------------------------------
    # Public — status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current approach state for the web API."""
        with self._lock:
            mode = self._mode
            track_id = self._target_track_id
            since = self._active_since
            wp = self._waypoints_sent
            drop_done = self._drop_complete

        result: dict = {
            "mode": mode.value,
            "track_id": track_id,
            "active": mode != ApproachMode.IDLE,
            "waypoints_sent": wp,
        }

        if mode != ApproachMode.IDLE:
            result["elapsed_sec"] = round(time.monotonic() - since, 1)

        if mode == ApproachMode.DROP:
            result["drop_complete"] = drop_done
            result["target_lat"] = self._target_lat
            result["target_lon"] = self._target_lon

        if mode == ApproachMode.STRIKE:
            result["software_arm"] = self._cfg.arm_channel is not None
            result["hardware_arm_status"] = self.get_hardware_arm_status()

        if mode == ApproachMode.PIXEL_LOCK:
            result["track_lost"] = self._guidance.track_lost

        return result

    @property
    def active(self) -> bool:
        with self._lock:
            return self._mode != ApproachMode.IDLE

    @property
    def mode(self) -> ApproachMode:
        with self._lock:
            return self._mode

    @property
    def target_track_id(self) -> int | None:
        with self._lock:
            return self._target_track_id

    @property
    def drop_complete(self) -> bool:
        with self._lock:
            return self._drop_complete

    # ------------------------------------------------------------------
    # Hardware arm status
    # ------------------------------------------------------------------

    def get_hardware_arm_status(self) -> bool | None:
        """Read hardware arm switch from RC channel.

        Returns True if armed, False if safe, None if unavailable.
        """
        if self._cfg.hw_arm_channel is None:
            return None
        try:
            rc = self._mavlink.get_rc_channels()
            if rc and self._cfg.hw_arm_channel <= len(rc):
                pwm = rc[self._cfg.hw_arm_channel - 1]
                if pwm is None or pwm == 0 or pwm == 65535:
                    return None
                return pwm > 1500  # Armed if above center
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Internal — mode update logic
    # ------------------------------------------------------------------

    def _update_follow(self, track, fw: int, fh: int) -> None:
        """Update follow mode — adjust yaw and send waypoints."""
        if track is None:
            logger.debug("Follow: target lost, holding position")
            return

        now = time.monotonic()
        if (now - self._last_wp_time) < self._cfg.waypoint_interval:
            return

        # Estimate target position from camera frame offset
        cx = (track.x1 + track.x2) / 2.0
        error_x = (cx - fw / 2.0) / (fw / 2.0)  # -1..+1

        target_pos = self._mavlink.estimate_target_position(
            error_x,
            self._cfg.follow_distance_m,
            self._cfg.camera_hfov_deg,
        )
        if target_pos is None:
            return

        target_lat, target_lon = target_pos

        # Speed scaling based on how centered the target is
        centering = 1.0 - abs(error_x)
        speed = self._cfg.follow_speed_min + centering * (
            self._cfg.follow_speed_max - self._cfg.follow_speed_min
        )
        try:
            self._mavlink.command_do_change_speed(speed)
        except Exception:
            pass

        try:
            self._mavlink.command_guided_to(target_lat, target_lon)
            self._last_wp_time = now
            with self._lock:
                self._waypoints_sent += 1
        except Exception as exc:
            logger.warning("Follow: guided waypoint failed: %s", exc)

    def _update_drop(self, track, fw: int, fh: int) -> None:
        """Update drop mode — check distance, fire servo when close."""
        with self._lock:
            if self._drop_complete:
                return

        # Get current vehicle position
        pos = self._mavlink.get_lat_lon()
        if pos is None or pos[0] is None:
            return

        lat, lon, alt = pos
        from .autonomous import haversine_m

        dist = haversine_m(lat, lon, self._target_lat, self._target_lon)

        if dist <= self._cfg.drop_distance_m:
            # Fire drop servo
            if self._cfg.drop_channel:
                self._mavlink.set_servo(
                    self._cfg.drop_channel, self._cfg.drop_pwm_release,
                )
                logger.info("DROP RELEASED at %.1fm from target", dist)
                audit_log.info(
                    "DROP RELEASE: dist=%.1fm lat=%.5f lon=%.5f",
                    dist, lat, lon,
                )

                # Revert servo after pulse duration
                ch = self._cfg.drop_channel
                hold_pwm = self._cfg.drop_pwm_hold
                duration = self._cfg.drop_duration

                def _revert():
                    time.sleep(duration)
                    self._mavlink.set_servo(ch, hold_pwm)

                threading.Thread(
                    target=_revert, daemon=True, name="drop-revert",
                ).start()

            with self._lock:
                self._drop_complete = True

    def _update_strike(self, track, fw: int, fh: int) -> None:
        """Update strike mode — continuous approach at max speed."""
        if track is None:
            logger.debug("Strike: target lost, holding")
            return

        # Safety gate: check hardware arm if configured
        # Treat None (unknown) as unsafe — fail closed
        if self._cfg.hw_arm_channel is not None:
            hw_armed = self.get_hardware_arm_status()
            if hw_armed is not True:  # False or None both mean unsafe
                logger.warning("Strike: hardware arm not confirmed — aborting")
                self.abort()
                return

        now = time.monotonic()
        if (now - self._last_wp_time) < self._cfg.waypoint_interval:
            return

        # Estimate target position
        cx = (track.x1 + track.x2) / 2.0
        error_x = (cx - fw / 2.0) / (fw / 2.0)

        target_pos = self._mavlink.estimate_target_position(
            error_x,
            self._cfg.follow_distance_m,
            self._cfg.camera_hfov_deg,
        )
        if target_pos is None:
            return

        target_lat, target_lon = target_pos

        # Max speed — double the follow max for strike
        try:
            self._mavlink.command_do_change_speed(
                self._cfg.follow_speed_max * 2,
            )
        except Exception:
            pass

        # Continuous waypoint update
        try:
            self._mavlink.command_guided_to(target_lat, target_lon)
            self._last_wp_time = now
            with self._lock:
                self._waypoints_sent += 1
        except Exception as exc:
            logger.warning("Strike: guided waypoint failed: %s", exc)

    def _update_pixel_lock(self, track, fw: int, fh: int) -> None:
        """Update pixel-lock mode — continuous velocity-based visual servoing."""
        if track is not None:
            cx = (track.x1 + track.x2) / 2.0
            cy = (track.y1 + track.y2) / 2.0
            error_x = (cx - fw / 2.0) / (fw / 2.0)
            error_y = (cy - fh / 2.0) / (fh / 2.0)
            bbox_area = (track.x2 - track.x1) * (track.y2 - track.y1)
            frame_area = fw * fh
            bbox_ratio = bbox_area / frame_area if frame_area > 0 else 0.0
        else:
            error_x = None
            error_y = None
            bbox_ratio = None

        cmd = self._guidance.update(error_x, error_y, bbox_ratio)

        # Enforce minimum altitude floor — prevent descent below threshold.
        # In NED, positive vz = descend.  Clamp to zero if near floor.
        vz = cmd.vz
        if vz > 0 and self._cfg.guidance_cfg is not None:
            min_alt = self._cfg.guidance_cfg.min_altitude_m
            pos = self._mavlink.get_lat_lon()
            if pos is not None:
                _, _, cur_alt = pos
                if cur_alt is not None and cur_alt <= min_alt:
                    vz = 0.0

        try:
            self._mavlink.send_velocity_ned(cmd.vx, cmd.vy, vz, cmd.yaw_rate)
            with self._lock:
                self._waypoints_sent += 1
        except Exception as exc:
            logger.warning("Pixel-lock: velocity command failed: %s", exc)

        # Periodic audit log (every 2 seconds) for post-sortie tuning
        now = time.monotonic()
        if now - self._last_vel_log_time >= 2.0:
            self._last_vel_log_time = now
            audit_log.info(
                "PIXEL_LOCK VEL: vx=%.2f vy=%.2f vz=%.2f yaw=%.1f",
                cmd.vx, cmd.vy, vz, cmd.yaw_rate,
            )

        # Auto-abort if track lost beyond timeout
        if self._guidance.track_lost:
            logger.warning("Pixel-lock: track lost beyond timeout — aborting")
            self.abort()
