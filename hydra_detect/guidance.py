"""Velocity-based visual servoing controller.

Maps pixel tracking errors to body-frame velocity commands for continuous
target pursuit.  This module is pure math — no MAVLink or hardware
dependencies — so it can be extracted for lightweight platforms (e.g.
Raspberry Pi Zero 2W with colour/shape tracking).
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class GuidanceConfig:
    """Tunables loaded from config.ini [guidance] section."""

    fwd_gain: float = 2.0       # m/s per unit approach ratio
    lat_gain: float = 1.5       # m/s per unit lateral error
    vert_gain: float = 1.0      # m/s per unit vertical error
    yaw_gain: float = 30.0      # deg/s per unit horizontal error

    max_fwd_speed: float = 5.0   # m/s
    max_lat_speed: float = 2.0  # m/s
    max_vert_speed: float = 1.5  # m/s
    max_yaw_rate: float = 45.0  # deg/s

    deadzone: float = 0.05      # ignore error below this magnitude
    target_bbox_ratio: float = 0.15  # desired target-fills-frame fraction
    lost_track_timeout_s: float = 2.0  # seconds before declaring track lost
    min_altitude_m: float = 5.0  # vz clamped to prevent ground collision


@dataclass
class VelocityCommand:
    """Body-frame velocity output from the guidance controller."""

    vx: float = 0.0   # forward (m/s)
    vy: float = 0.0   # lateral (m/s, positive right)
    vz: float = 0.0   # vertical (m/s, positive down in NED)
    yaw_rate: float = 0.0  # deg/s


class GuidanceController:
    """Proportional visual servoing controller.

    Call ``update()`` once per frame with current tracking state.  Returns
    a ``VelocityCommand`` to send to the flight controller.

    The controller is intentionally stateless except for an EMA smoother
    and a track-loss timer.  This keeps it lightweight and extractable.
    """

    def __init__(self, cfg: GuidanceConfig | None = None):
        self._cfg = cfg or GuidanceConfig()
        # EMA-smoothed errors (reduces jitter)
        self._smooth_ex: float = 0.0
        self._smooth_ey: float = 0.0
        self._alpha: float = 0.4  # EMA factor (higher = less smoothing)
        # Track loss timer
        self._last_track_time: float = 0.0
        self._active: bool = False

    def start(self) -> None:
        """Begin guidance — call when pixel-lock mode is engaged."""
        self._smooth_ex = 0.0
        self._smooth_ey = 0.0
        self._last_track_time = time.monotonic()
        self._active = True

    def stop(self) -> None:
        """Stop guidance — returns zero velocity on next update."""
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def update(
        self,
        error_x: float | None,
        error_y: float | None,
        bbox_ratio: float | None,
    ) -> VelocityCommand:
        """Compute velocity command from current pixel errors.

        Args:
            error_x: Horizontal offset from centre (-1..+1), or None if lost.
            error_y: Vertical offset from centre (-1..+1), or None if lost.
            bbox_ratio: Target bbox area / frame area (0..1), or None if lost.

        Returns:
            VelocityCommand with body-frame velocities.  Returns zero
            velocity if guidance is inactive or track is lost.
        """
        if not self._active:
            return VelocityCommand()

        now = time.monotonic()

        # Track lost
        if error_x is None or error_y is None or bbox_ratio is None:
            if (now - self._last_track_time) >= self._cfg.lost_track_timeout_s:
                return VelocityCommand()  # Hold position (zero velocity)
            # Within timeout — send zero velocity (brake, don't drift)
            return VelocityCommand()

        self._last_track_time = now

        # EMA smoothing
        self._smooth_ex = self._alpha * error_x + (1 - self._alpha) * self._smooth_ex
        self._smooth_ey = self._alpha * error_y + (1 - self._alpha) * self._smooth_ey

        cfg = self._cfg

        # Apply deadzone
        ex = _deadzone(self._smooth_ex, cfg.deadzone)
        ey = _deadzone(self._smooth_ey, cfg.deadzone)

        # Forward velocity: approach until target fills target_bbox_ratio
        approach_ratio = max(0.0, min(1.0, 1.0 - bbox_ratio / cfg.target_bbox_ratio))
        vx = cfg.fwd_gain * approach_ratio

        # Lateral: strafe to centre horizontally
        vy = cfg.lat_gain * ex

        # Vertical: climb/descend to centre vertically
        # In NED, positive vz is DOWN.  If target is above centre (ey < 0),
        # we want to climb (vz < 0).  So vz = gain * ey works directly:
        # ey negative (target above) → vz negative (climb).
        vz = cfg.vert_gain * ey

        # Yaw: rotate to face target
        yaw_rate = cfg.yaw_gain * ex

        # Clamp to safety limits
        vx = _clamp(vx, 0.0, cfg.max_fwd_speed)
        vy = _clamp(vy, -cfg.max_lat_speed, cfg.max_lat_speed)
        vz = _clamp(vz, -cfg.max_vert_speed, cfg.max_vert_speed)
        yaw_rate = _clamp(yaw_rate, -cfg.max_yaw_rate, cfg.max_yaw_rate)

        return VelocityCommand(vx=vx, vy=vy, vz=vz, yaw_rate=yaw_rate)

    @property
    def track_lost(self) -> bool:
        """True if the track has been lost beyond the timeout threshold."""
        if not self._active:
            return False
        elapsed = time.monotonic() - self._last_track_time
        return elapsed >= self._cfg.lost_track_timeout_s


# ------------------------------------------------------------------
# Helpers (module-level, no state)
# ------------------------------------------------------------------

def _deadzone(value: float, threshold: float) -> float:
    """Return zero if |value| < threshold, else value."""
    if abs(value) < threshold:
        return 0.0
    return value


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))
