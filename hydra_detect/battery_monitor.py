"""Battery monitoring for vehicle MAVLink SYS_STATUS.

Tracks vehicle battery voltage and remaining percentage from MAVLink
``SYS_STATUS``, computes a level (``OK`` / ``LOW`` / ``CRITICAL`` /
``UNKNOWN``), and emits STATUSTEXT alerts on level transitions.

Hysteresis: alerts fire only on level transitions (e.g. ``OK`` → ``LOW``).
A single transition produces a single STATUSTEXT — no per-cycle spam.
Recovery transitions (e.g. ``CRITICAL`` → ``OK``) emit a recovery message
once and then go silent.

Stale data: if no SYS_STATUS has been seen within ``stale_after_sec``
seconds, the level resolves to ``UNKNOWN`` and no alerts fire.

NOTE: This represents the *vehicle* battery seen by the FC. The Jetson
companion computer may run from a separate power source. Surface what
MAVLink reports; do not pretend it covers Jetson power.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Level constants — exposed in /api/stats and instructor view.
LEVEL_OK = "OK"
LEVEL_LOW = "LOW"
LEVEL_CRITICAL = "CRITICAL"
LEVEL_UNKNOWN = "UNKNOWN"

# Severity used for the one-time "BATT MONITOR UNCALIBRATED" STATUSTEXT.
# Boots once when the FC starts reporting voltage but battery_remaining is
# still the MAVLink "unknown" sentinel — flags to the operator that the
# percent-driven alert path will stay silent on this unit until BATT_CAPACITY
# is configured and the battery monitor is calibrated.
_SEVERITY_UNCALIBRATED = 4  # WARNING — visible in Mission Planner, not alarming

# MAVLink severity used by STATUSTEXT alerts. ArduPilot mapping:
#   2 = MAV_SEVERITY_CRITICAL  (red in Mission Planner)
#   4 = MAV_SEVERITY_WARNING   (amber)
#   6 = MAV_SEVERITY_INFO      (green)
_SEVERITY_LOW = 4       # WARNING
_SEVERITY_CRITICAL = 2  # CRITICAL
_SEVERITY_RECOVERED = 6  # INFO


@dataclass
class BatteryState:
    """Snapshot of vehicle battery state. All fields may be ``None``."""

    voltage_v: Optional[float] = None
    remaining_pct: Optional[int] = None
    level: str = LEVEL_UNKNOWN
    last_update_ts: float = 0.0  # monotonic seconds; 0 = never
    source: str = "mavlink"
    # True when SYS_STATUS has arrived but battery_remaining stays at the
    # MAVLink "unknown" sentinel — i.e. the FC is talking but BATT_CAPACITY
    # is unset / the battery monitor is uncalibrated, so the percent-driven
    # alert path will never fire on this unit. Distinguishes "monitor is
    # silent" from "monitor is fine and the cell is healthy."
    uncalibrated: bool = False

    def to_api(self) -> dict:
        """Return the dict shape exposed on /api/stats."""
        return {
            "voltage_v": self.voltage_v,
            "remaining_pct": self.remaining_pct,
            "level": self.level,
            "source": self.source,
            "uncalibrated": self.uncalibrated,
        }


class BatteryMonitor:
    """Compute battery level with hysteresis and emit STATUSTEXT on changes.

    Args:
        low_threshold_pct: Below this, level becomes ``LOW``. Inclusive.
        critical_threshold_pct: Below this, level becomes ``CRITICAL``.
            Must be < ``low_threshold_pct``.
        callsign: Prefix for STATUSTEXT messages, e.g. ``HYDRA-2-USV``.
            Truncated to 16 chars to leave room for the body within
            MAVLink's 50-char STATUSTEXT cap.
        send_statustext: Callable ``(text: str, severity: int) -> None``
            invoked for each transition. Pass ``None`` for a silent
            monitor (used by tests / dashboards-only deployments).
        stale_after_sec: If no update arrives within this window, the
            current level resolves to ``UNKNOWN``. 0 disables.
        critical_reissue_sec: Re-emit a CRITICAL alert this often even
            without a level change. 0 disables. Helps the operator
            notice if they tuned out the first alert.
        enabled: Master switch. When False, ``update_from_sys_status``
            is a no-op and ``get_state`` returns an empty/UNKNOWN state.
    """

    def __init__(
        self,
        *,
        low_threshold_pct: int = 20,
        critical_threshold_pct: int = 10,
        callsign: str = "HYDRA",
        send_statustext: Optional[Callable[[str, int], None]] = None,
        stale_after_sec: float = 30.0,
        critical_reissue_sec: float = 0.0,
        enabled: bool = True,
    ):
        if critical_threshold_pct >= low_threshold_pct:
            raise ValueError(
                f"critical_threshold_pct ({critical_threshold_pct}) must be "
                f"< low_threshold_pct ({low_threshold_pct})"
            )
        self._low = max(0, min(100, low_threshold_pct))
        self._crit = max(0, min(100, critical_threshold_pct))
        self._callsign = (callsign or "HYDRA")[:16]
        self._send = send_statustext
        self._stale = max(0.0, float(stale_after_sec))
        # PR #211 R1-4 from docs/adversarial/211.md: clamp the CRITICAL
        # reissue cadence to a safety floor so a misconfigured value (e.g.
        # 1.0s) cannot saturate the MAVLink STATUSTEXT path. The floor is
        # comfortably above the mavlink_io _global_max_per_sec ceiling of
        # 2.0/s — at one reissue per 10s the battery path contributes
        # 0.1/s, well under the cap even with detection traffic competing.
        reissue_floor_sec = 10.0
        requested_reissue = max(0.0, float(critical_reissue_sec))
        if 0.0 < requested_reissue < reissue_floor_sec:
            logger.warning(
                "battery: critical_reissue_sec=%.2f below safety floor "
                "%.1fs; clamping to floor to prevent STATUSTEXT spam.",
                requested_reissue, reissue_floor_sec,
            )
            self._reissue = reissue_floor_sec
        else:
            self._reissue = requested_reissue
        self._enabled = enabled

        self._lock = threading.Lock()
        self._state = BatteryState()
        # Track the last alert emitted so we only fire on transitions.
        # ``None`` means we have not emitted any alert yet.
        self._last_alert_level: Optional[str] = None
        self._last_alert_ts: float = 0.0
        # One-time flag: True after we have fired the "BATT MONITOR
        # UNCALIBRATED" STATUSTEXT. Prevents the alert from re-emitting
        # on every SYS_STATUS tick while the FC is still uncalibrated.
        self._uncalibrated_alert_sent: bool = False

    # -- Configuration -------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def low_threshold_pct(self) -> int:
        return self._low

    @property
    def critical_threshold_pct(self) -> int:
        return self._crit

    @property
    def callsign(self) -> str:
        return self._callsign

    @property
    def critical_reissue_sec(self) -> float:
        """Effective reissue cadence after R1-4 floor clamp (may differ
        from the configured value if it was below the safety floor)."""
        return self._reissue

    def set_callsign(self, callsign: str) -> None:
        with self._lock:
            self._callsign = (callsign or "HYDRA")[:16]

    # -- State ingestion -----------------------------------------------
    def update_from_sys_status(
        self,
        voltage_battery: int,
        battery_remaining: int,
        now: Optional[float] = None,
    ) -> None:
        """Ingest a SYS_STATUS message.

        Args:
            voltage_battery: ``msg.voltage_battery`` in millivolts.
                ``0xFFFF`` (65535) is the MAVLink "unknown" sentinel.
            battery_remaining: ``msg.battery_remaining`` in percent
                (0-100). ``-1`` is the MAVLink "unknown" sentinel.
            now: Monotonic time. Defaults to ``time.monotonic()``.
                Test hook only.
        """
        if not self._enabled:
            return

        now = now if now is not None else time.monotonic()
        voltage_v: Optional[float] = None
        remaining_pct: Optional[int] = None

        if voltage_battery != 0xFFFF and voltage_battery >= 0:
            voltage_v = round(voltage_battery / 1000.0, 2)
        if battery_remaining != -1 and 0 <= battery_remaining <= 100:
            remaining_pct = int(battery_remaining)

        # Detect "FC is reporting but battery is uncalibrated" — voltage
        # present + percent sentinel. Fire the one-time UNCALIBRATED
        # STATUSTEXT on the first such SYS_STATUS so the operator knows
        # the percent-driven alert path will stay silent on this unit.
        uncalibrated = (voltage_v is not None) and (remaining_pct is None)

        # Mid-session calibration recovery: if a real percent arrives after
        # we've been in uncalibrated state, clear both the sticky state
        # flag and the one-time-alert latch. A future calibration loss
        # (FC reboot, BATT_CAPACITY reset) then re-fires the warning
        # rather than staying silent. (PR #211 review pass — was a UX bug
        # in the originally-shipped fix.)
        recovered_to_calibrated = (
            remaining_pct is not None and self._state.uncalibrated
        )

        uncalibrated_alert: Optional[tuple[str, int]] = None
        with self._lock:
            self._state.voltage_v = voltage_v
            self._state.remaining_pct = remaining_pct
            self._state.last_update_ts = now
            if recovered_to_calibrated:
                self._state.uncalibrated = False
                self._uncalibrated_alert_sent = False
                logger.info(
                    "battery: calibrated SYS_STATUS recovered (pct=%d) — "
                    "UNCALIBRATED state cleared, alert latch reset.",
                    remaining_pct,
                )
            else:
                self._state.uncalibrated = uncalibrated
            level = self._compute_level_locked(now)
            self._state.level = level
            transition = self._maybe_emit_alert_locked(level, now)
            if uncalibrated and not self._uncalibrated_alert_sent:
                self._uncalibrated_alert_sent = True
                uncalibrated_alert = (
                    f"{self._callsign}: BATT MONITOR UNCALIBRATED",
                    _SEVERITY_UNCALIBRATED,
                )

        if uncalibrated_alert is not None and self._send is not None:
            text, severity = uncalibrated_alert
            try:
                self._send(text, severity)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("battery STATUSTEXT send failed: %s", exc)

        if transition is not None:
            text, severity = transition
            if self._send is not None:
                try:
                    self._send(text, severity)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("battery STATUSTEXT send failed: %s", exc)

    # -- Read-side -----------------------------------------------------
    def get_state(self, now: Optional[float] = None) -> BatteryState:
        """Return a snapshot. Recomputes ``level`` against staleness."""
        if not self._enabled:
            return BatteryState()
        now = now if now is not None else time.monotonic()
        with self._lock:
            level = self._compute_level_locked(now)
            self._state.level = level
            # uncalibrated is sticky once detected — even after the read-side
            # staleness check would flip level to UNKNOWN, the dashboard
            # should still surface "this unit is uncalibrated" so the
            # operator does not interpret silence as health.
            uncal = self._state.uncalibrated
            return BatteryState(
                voltage_v=self._state.voltage_v,
                remaining_pct=self._state.remaining_pct,
                level=level,
                last_update_ts=self._state.last_update_ts,
                source=self._state.source,
                uncalibrated=uncal,
            )

    def get_level(self, now: Optional[float] = None) -> str:
        return self.get_state(now).level

    def tick(self, now: Optional[float] = None) -> None:
        """Re-evaluate level for staleness / re-issue, without a new msg.

        The pipeline can call this on its slow loop to fire CRITICAL
        re-issues and to flip the level to UNKNOWN when SYS_STATUS
        stops arriving. Safe to skip — read-side ``get_state`` also
        recomputes level.
        """
        if not self._enabled:
            return
        now = now if now is not None else time.monotonic()
        with self._lock:
            level = self._compute_level_locked(now)
            self._state.level = level
            transition = self._maybe_emit_alert_locked(level, now)
        if transition is not None and self._send is not None:
            text, severity = transition
            try:
                self._send(text, severity)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("battery STATUSTEXT send failed: %s", exc)

    # -- Internals -----------------------------------------------------
    def _compute_level_locked(self, now: float) -> str:
        """Caller holds ``self._lock``."""
        last = self._state.last_update_ts
        # Never seen a message → UNKNOWN.
        if last <= 0.0:
            return LEVEL_UNKNOWN
        # Stale data → UNKNOWN.
        if self._stale > 0.0 and (now - last) > self._stale:
            return LEVEL_UNKNOWN
        # No remaining-pct data even though we got a message recently.
        # We don't alert on voltage alone — voltage thresholds vary by
        # chemistry (LiPo 4S vs 6S vs LiHV) and surfacing UNKNOWN is
        # safer than picking a wrong cutoff.
        pct = self._state.remaining_pct
        if pct is None:
            return LEVEL_UNKNOWN
        if pct <= self._crit:
            return LEVEL_CRITICAL
        if pct <= self._low:
            return LEVEL_LOW
        return LEVEL_OK

    def _maybe_emit_alert_locked(
        self, level: str, now: float,
    ) -> Optional[tuple[str, int]]:
        """Decide if we should emit STATUSTEXT for this level. Caller
        holds ``self._lock``. Returns ``(text, severity)`` or None."""
        # Never alert on UNKNOWN — ambiguous, don't spam.
        if level == LEVEL_UNKNOWN:
            return None

        last_level = self._last_alert_level
        is_transition = last_level != level
        is_reissue = (
            self._reissue > 0.0
            and level == LEVEL_CRITICAL
            and last_level == LEVEL_CRITICAL
            and (now - self._last_alert_ts) >= self._reissue
        )

        if not is_transition and not is_reissue:
            return None

        pct = self._state.remaining_pct
        text, severity = self._format_alert(level, pct)
        self._last_alert_level = level
        self._last_alert_ts = now
        return text, severity

    def _format_alert(self, level: str, pct: Optional[int]) -> tuple[str, int]:
        """Build the STATUSTEXT body and pick a severity."""
        prefix = self._callsign
        pct_str = f"{pct}%" if pct is not None else "??%"
        if level == LEVEL_CRITICAL:
            return f"{prefix}: BATT CRITICAL {pct_str}", _SEVERITY_CRITICAL
        if level == LEVEL_LOW:
            return f"{prefix}: BATT LOW {pct_str}", _SEVERITY_LOW
        # OK is only reached as a recovery transition.
        return f"{prefix}: BATT RECOVERED {pct_str}", _SEVERITY_RECOVERED
