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
    smoothing: float = 0.4      # EMA alpha (higher = less smoothing)
    target_bbox_ratio: float = 0.15  # desired target-fills-frame fraction
    lost_track_timeout_s: float = 2.0  # seconds before declaring track lost
    min_altitude_m: float = 5.0  # vz clamped to prevent ground collision

    # Forward predictor: alpha-beta tracker on the EMA-smoothed bbox center,
    # projecting position forward by loop_delay_ms to compensate for
    # camera + inference + MAVLink + ArduPilot + ESC pipeline delay
    # (measured 100-130 ms steady state on Orin Nano + Pixhawk).
    loop_delay_ms: float = 100.0
    predictor_enabled: bool = True
    predictor_alpha: float = 0.5    # alpha-beta position gain (0..1)
    predictor_beta: float = 0.05    # alpha-beta velocity gain (0..1)


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
        self._alpha: float = self._cfg.smoothing
        # Track loss timer
        self._last_track_time: float = 0.0
        self._active: bool = False
        # Forward predictor (alpha-beta tracker) state
        self._pred_x: float = 0.0
        self._pred_y: float = 0.0
        self._pred_vx: float = 0.0
        self._pred_vy: float = 0.0
        self._pred_initialized: bool = False
        self._last_pred_time: float = 0.0

    def start(self) -> None:
        """Begin guidance — call when pixel-lock mode is engaged."""
        self._smooth_ex = 0.0
        self._smooth_ey = 0.0
        self._last_track_time = time.monotonic()
        self._active = True
        self._reset_predictor()

    def stop(self) -> None:
        """Stop guidance — returns zero velocity on next update."""
        self._active = False

    def _reset_predictor(self) -> None:
        self._pred_x = 0.0
        self._pred_y = 0.0
        self._pred_vx = 0.0
        self._pred_vy = 0.0
        self._pred_initialized = False
        self._last_pred_time = 0.0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def predicted_error(self) -> tuple[float, float] | None:
        """Predicted (ex, ey) at t = now + loop_delay_ms, or None if the
        predictor has not seen a valid frame since the last reset."""
        if not self._pred_initialized:
            return None
        lead_s = self._cfg.loop_delay_ms / 1000.0
        return (
            self._pred_x + self._pred_vx * lead_s,
            self._pred_y + self._pred_vy * lead_s,
        )

    @property
    def predicted_velocity(self) -> tuple[float, float]:
        """Current (vx, vy) estimate in error-units per second."""
        return (self._pred_vx, self._pred_vy)

    def update(
        self,
        error_x: float | None,
        error_y: float | None,
        bbox_ratio: float | None,
        now_s: float | None = None,
    ) -> VelocityCommand:
        """Compute velocity command from current pixel errors.

        Args:
            error_x: Horizontal offset from centre (-1..+1), or None if lost.
            error_y: Vertical offset from centre (-1..+1), or None if lost.
            bbox_ratio: Target bbox area / frame area (0..1), or None if lost.
            now_s: Optional monotonic-clock timestamp in seconds. When None
                (production path), time.monotonic() is used. Tests pass an
                explicit clock so predictor convergence is deterministic.

        Returns:
            VelocityCommand with body-frame velocities.  Returns zero
            velocity if guidance is inactive or track is lost.
        """
        if not self._active:
            return VelocityCommand()

        now = now_s if now_s is not None else time.monotonic()

        # Track lost
        if error_x is None or error_y is None or bbox_ratio is None:
            # Reset the predictor so a re-acquired track does not inherit
            # stale velocity carried across the gap.
            self._reset_predictor()
            if (now - self._last_track_time) >= self._cfg.lost_track_timeout_s:
                return VelocityCommand()  # Hold position (zero velocity)
            # Within timeout — send zero velocity (brake, don't drift)
            return VelocityCommand()

        self._last_track_time = now

        # EMA smoothing on raw input.
        self._smooth_ex = self._alpha * error_x + (1 - self._alpha) * self._smooth_ex
        self._smooth_ey = self._alpha * error_y + (1 - self._alpha) * self._smooth_ey

        cfg = self._cfg

        # Forward predictor on the smoothed series. Outputs the EMA-smoothed
        # error projected by loop_delay_ms; if disabled, falls through to the
        # raw smoothed value (legacy behavior).
        if cfg.predictor_enabled:
            ex_used, ey_used = self._predict(now, self._smooth_ex, self._smooth_ey)
        else:
            ex_used, ey_used = self._smooth_ex, self._smooth_ey

        # Apply deadzone
        ex = _deadzone(ex_used, cfg.deadzone)
        ey = _deadzone(ey_used, cfg.deadzone)

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

    def _predict(
        self,
        now: float,
        smooth_ex: float,
        smooth_ey: float,
    ) -> tuple[float, float]:
        """Alpha-beta tracker step. Returns the smoothed error projected
        forward by loop_delay_ms.

        Standard discrete alpha-beta filter:
            x_pred  - x + v * dt
            r       - z - x_pred
            x       - x_pred + alpha * r
            v       - v + (beta / dt) * r
            output  - x + v * lead

        On the first valid frame after start() or a track loss, the predictor
        seeds from the measurement with zero velocity and emits no lead.
        """
        cfg = self._cfg
        if not self._pred_initialized:
            self._pred_x = smooth_ex
            self._pred_y = smooth_ey
            self._pred_vx = 0.0
            self._pred_vy = 0.0
            self._last_pred_time = now
            self._pred_initialized = True
            return smooth_ex, smooth_ey

        dt = now - self._last_pred_time
        lead_s = cfg.loop_delay_ms / 1000.0

        if dt <= 0.0:
            # Clock anomaly or two updates in the same tick — skip the filter
            # step but still project from existing state.
            return (
                self._pred_x + self._pred_vx * lead_s,
                self._pred_y + self._pred_vy * lead_s,
            )

        # Predict to the current time using prior velocity.
        x_pred = self._pred_x + self._pred_vx * dt
        y_pred = self._pred_y + self._pred_vy * dt

        # Residuals against the new measurement.
        rx = smooth_ex - x_pred
        ry = smooth_ey - y_pred

        # Filter step.
        self._pred_x = x_pred + cfg.predictor_alpha * rx
        self._pred_y = y_pred + cfg.predictor_alpha * ry
        self._pred_vx = self._pred_vx + (cfg.predictor_beta / dt) * rx
        self._pred_vy = self._pred_vy + (cfg.predictor_beta / dt) * ry
        self._last_pred_time = now

        # Project forward by loop_delay.
        return (
            self._pred_x + self._pred_vx * lead_s,
            self._pred_y + self._pred_vy * lead_s,
        )


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
