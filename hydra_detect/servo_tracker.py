"""Pixel-lock servo controller — maps camera error to PWM output."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class ServoTracker:
    """Maps a locked target's pixel offset to servo PWM via MAVLink.

    Pan servo: proportional mapping from error_x to PWM.
    Strike servo: pulse on/off on strike command.
    """

    def __init__(
        self,
        mavlink,
        *,
        pan_channel: int = 1,
        pan_pwm_center: int = 1500,
        pan_pwm_range: int = 500,
        pan_invert: bool = False,
        pan_dead_zone: float = 0.05,
        pan_smoothing: float = 0.3,
        strike_channel: int = 2,
        strike_pwm_fire: int = 1900,
        strike_pwm_safe: int = 1100,
        strike_duration: float = 0.5,
        replaces_yaw: bool = False,
    ):
        self._mavlink = mavlink

        self._pan_channel = pan_channel
        self._pan_center = pan_pwm_center
        self._pan_range = pan_pwm_range
        self._pan_invert = pan_invert
        self._pan_dead_zone = max(0.0, pan_dead_zone)
        self._pan_alpha = max(0.01, min(1.0, pan_smoothing))

        self._strike_channel = strike_channel
        self._strike_pwm_fire = strike_pwm_fire
        self._strike_pwm_safe = strike_pwm_safe
        self._strike_duration = strike_duration

        self._replaces_yaw = replaces_yaw

        self._smoothed: float = 0.0
        self._last_pwm: int = pan_pwm_center
        self._strike_active = threading.Event()
        self._tracking = False
        self._last_error_x: float = 0.0

        self._mavlink.set_servo(self._strike_channel, self._strike_pwm_safe)
        self._mavlink.set_servo(self._pan_channel, self._pan_center)

    def update(self, error_x: float) -> None:
        """Update pan servo from pixel-lock error. Called every frame."""
        self._tracking = True
        self._last_error_x = error_x

        self._smoothed = (self._pan_alpha * error_x
                          + (1.0 - self._pan_alpha) * self._smoothed)

        if abs(self._smoothed) < self._pan_dead_zone:
            pwm = self._pan_center
        else:
            offset = self._smoothed * self._pan_range
            if self._pan_invert:
                offset = -offset
            pwm = round(self._pan_center + offset)

        pwm = max(500, min(2500, pwm))

        if pwm == self._last_pwm:
            return

        self._last_pwm = pwm
        self._mavlink.set_servo(self._pan_channel, pwm)

    def fire_strike(self) -> None:
        """Actuate strike servo (fire -> safe after duration)."""
        if self._strike_active.is_set():
            logger.info("Strike servo already active — ignoring.")
            return

        self._strike_active.set()
        self._mavlink.set_servo(
            self._strike_channel, self._strike_pwm_fire)
        logger.info(
            "Strike servo FIRED: ch=%d pwm=%d",
            self._strike_channel, self._strike_pwm_fire)

        def _revert():
            time.sleep(self._strike_duration)
            self._mavlink.set_servo(
                self._strike_channel, self._strike_pwm_safe)
            self._strike_active.clear()

        threading.Thread(
            target=_revert, daemon=True, name="strike-revert").start()

    def safe(self) -> None:
        """Return all servos to safe positions. Resets EMA state."""
        self._smoothed = 0.0
        self._last_pwm = self._pan_center
        self._tracking = False
        self._mavlink.set_servo(self._pan_channel, self._pan_center)
        self._mavlink.set_servo(
            self._strike_channel, self._strike_pwm_safe)

    def get_status(self) -> dict:
        """Return current state for web API."""
        return {
            "enabled": True,
            "tracking": self._tracking,
            "pan_channel": self._pan_channel,
            "pan_pwm": (self._last_pwm
                        if self._last_pwm is not None
                        else self._pan_center),
            "strike_channel": self._strike_channel,
            "strike_active": self._strike_active.is_set(),
            "error_x": round(self._last_error_x, 3),
            "smoothing_alpha": self._pan_alpha,
            "replaces_yaw": self._replaces_yaw,
        }

    @property
    def replaces_yaw(self) -> bool:
        """If True, pipeline should skip adjust_yaw()."""
        return self._replaces_yaw
