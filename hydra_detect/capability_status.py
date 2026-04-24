"""Capability status evaluators for the Hydra Detect operator readiness page.

Each subsystem reports READY / WARN / BLOCKED / ARMED with a plain-language
reason string and a fix reference. Operators see exactly what is gating each
feature. No guessing.

Registry pattern: evaluators are plain functions registered in _EVALUATORS.
evaluate_all() runs each one against a SystemState snapshot and returns the
full report list.

Issue #146 — skeleton. Real ARMED gating follows #147.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Status levels ────────────────────────────────────────────────────────────

class CapabilityStatus(str, Enum):
    """Readiness status for a single capability."""
    READY = "READY"
    WARN = "WARN"
    BLOCKED = "BLOCKED"
    ARMED = "ARMED"


# ── Report dataclass ─────────────────────────────────────────────────────────

@dataclass
class CapabilityReport:
    """Readiness report for one capability."""
    name: str
    status: CapabilityStatus
    reasons: list[str] = field(default_factory=list)
    fix_target: str | None = None


# ── System state ─────────────────────────────────────────────────────────────

@dataclass
class SystemState:
    """Lightweight snapshot of signals the evaluators need.

    Built by build_system_state() from live Hydra component references.
    Each field has a safe default so unit tests can construct partial states.
    """
    # Camera / Detection
    camera_ok: bool = False
    camera_frame_age_sec: float | None = None  # seconds since last frame; None = never

    # MAVLink
    mavlink_connected: bool = False
    mavlink_last_heartbeat_age_sec: float | None = None  # None = never seen

    # GPS (fix_type from GPS_RAW_INT: 0=none, 1=no-fix, 2=2D, 3=3D, 4=DGPS, 5=RTK)
    gps_fix: int = 0

    # TAK Output
    tak_output_enabled: bool = False
    tak_output_running: bool = False

    # TAK Commands
    tak_allowed_callsigns_set: bool = False
    tak_hmac_secret_set: bool = False

    # Disk
    disk_free_gb: float | None = None
    disk_output_dir: str = "/tmp"

    # Time source (stub — #155 will wire GPS-disciplined PPS / NTP)
    time_source: str = "RTC"

    # Vehicle profile
    vehicle_profile: str = ""
    vehicle_profile_present: bool = False

    # Schema version
    schema_version: str | None = None
    schema_version_present: bool = False

    # Raw config parser (optional — used by some evaluators)
    cfg: Any = None


# ── System state builder ─────────────────────────────────────────────────────

def build_system_state(
    stream_state: Any | None = None,
    mavlink_ref: Any | None = None,
    tak_output_ref: Any | None = None,
    tak_input_ref: Any | None = None,
    cfg: Any | None = None,
) -> SystemState:
    """Build a SystemState from live Hydra component references.

    All fields default to conservative (blocked/missing) values when the
    corresponding component is not wired. Safe to call with all-None args.
    """
    state = SystemState()
    state.cfg = cfg

    # ── Camera / Detection ────────────────────────────────────────────────
    if stream_state is not None:
        try:
            stats = stream_state.get_stats()
            state.camera_ok = bool(stats.get("camera_ok", False))
            last_frame_ts = stats.get("last_frame_ts")
            if last_frame_ts is not None:
                state.camera_frame_age_sec = time.monotonic() - float(last_frame_ts)
        except Exception:
            pass

    # ── MAVLink ───────────────────────────────────────────────────────────
    if mavlink_ref is not None:
        try:
            state.mavlink_connected = bool(mavlink_ref.connected)
            gps = mavlink_ref.get_gps()
            state.gps_fix = int(gps.get("fix", 0))
            last_update = gps.get("last_update", 0.0)
            if last_update and last_update > 0:
                state.mavlink_last_heartbeat_age_sec = time.monotonic() - float(last_update)
        except Exception:
            pass

    # ── TAK Output ────────────────────────────────────────────────────────
    if tak_output_ref is not None:
        try:
            info = tak_output_ref.get_status()
            state.tak_output_enabled = bool(info.get("enabled", False))
            state.tak_output_running = bool(info.get("running", False))
        except Exception:
            pass
    else:
        # Check config for TAK enabled flag
        if cfg is not None:
            try:
                enabled_str = cfg.get("tak", "enabled", fallback="false")
                state.tak_output_enabled = enabled_str.lower() in ("1", "true", "yes")
            except Exception:
                pass

    # ── TAK Commands ─────────────────────────────────────────────────────
    if tak_input_ref is not None:
        try:
            state.tak_allowed_callsigns_set = bool(
                getattr(tak_input_ref, "_allowed_callsigns", None)
            )
            state.tak_hmac_secret_set = bool(
                getattr(tak_input_ref, "_hmac_secret", None)
            )
        except Exception:
            pass
    elif cfg is not None:
        try:
            callsigns_raw = cfg.get("tak", "allowed_callsigns", fallback="").strip()
            state.tak_allowed_callsigns_set = bool(callsigns_raw)
            secret_raw = cfg.get("tak", "command_hmac_secret", fallback="").strip()
            state.tak_hmac_secret_set = bool(secret_raw)
        except Exception:
            pass

    # ── Disk ──────────────────────────────────────────────────────────────
    if cfg is not None:
        try:
            state.disk_output_dir = cfg.get("logging", "output_dir", fallback="/tmp")
        except Exception:
            pass
    try:
        usage = shutil.disk_usage(state.disk_output_dir)
        state.disk_free_gb = usage.free / (1024 ** 3)
    except Exception:
        state.disk_free_gb = None

    # ── Vehicle Profile ───────────────────────────────────────────────────
    if cfg is not None:
        try:
            profile = cfg.get("vehicle", "profile", fallback="").strip()
            state.vehicle_profile = profile
            if profile:
                section = f"vehicle.{profile}"
                state.vehicle_profile_present = cfg.has_section(section) or bool(profile)
            else:
                state.vehicle_profile_present = False
        except Exception:
            pass

    # ── Schema Version ────────────────────────────────────────────────────
    if cfg is not None:
        try:
            ver = cfg.get("meta", "schema_version", fallback="").strip()
            state.schema_version = ver if ver else None
            state.schema_version_present = bool(ver)
        except Exception:
            pass

    return state


# ── Evaluator helpers ─────────────────────────────────────────────────────────

_STALE_FRAME_SEC = 5.0       # frame age beyond which detection is WARN
_STALE_HEARTBEAT_SEC = 5.0   # heartbeat age beyond which MAVLink is WARN
_DISK_WARN_GB = 2.0          # free disk WARN threshold
_DISK_BLOCKED_GB = 0.5       # free disk BLOCKED threshold


# ── Evaluators ────────────────────────────────────────────────────────────────

def _eval_detection(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if not state.camera_ok:
        reasons.append(
            "Camera not initialized. Check source in [camera] config."
        )
        return CapabilityReport("Detection", CapabilityStatus.BLOCKED, reasons)

    if state.camera_frame_age_sec is None:
        reasons.append("No frames received yet. Pipeline may still be starting.")
        return CapabilityReport("Detection", CapabilityStatus.BLOCKED, reasons)

    if state.camera_frame_age_sec > _STALE_FRAME_SEC:
        reasons.append(
            f"Last frame {state.camera_frame_age_sec:.1f}s ago. "
            "Camera may be stalled."
        )
        return CapabilityReport("Detection", CapabilityStatus.WARN, reasons)

    return CapabilityReport("Detection", CapabilityStatus.READY)


def _eval_mavlink(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if not state.mavlink_connected:
        reasons.append(
            "No MAVLink connection. Check serial port and baud in [mavlink] config."
        )
        return CapabilityReport("MAVLink", CapabilityStatus.BLOCKED, reasons)

    if state.mavlink_last_heartbeat_age_sec is None:
        reasons.append("No heartbeat received. Verify Pixhawk is powered and connected.")
        return CapabilityReport("MAVLink", CapabilityStatus.WARN, reasons)

    if state.mavlink_last_heartbeat_age_sec > _STALE_HEARTBEAT_SEC:
        reasons.append(
            f"Heartbeat stale: {state.mavlink_last_heartbeat_age_sec:.1f}s ago. "
            "Link may be intermittent."
        )
        return CapabilityReport("MAVLink", CapabilityStatus.WARN, reasons)

    return CapabilityReport("MAVLink", CapabilityStatus.READY)


def _eval_gps(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if not state.mavlink_connected:
        reasons.append("MAVLink not connected. GPS fix cannot be read.")
        return CapabilityReport("GPS", CapabilityStatus.BLOCKED, reasons, fix_target="MAVLink")

    fix = state.gps_fix
    if fix == 0 or fix == 1:
        reasons.append(
            f"GPS fix missing. Current: {fix}. Required: 3D+ fix (type 3 or higher)."
        )
        return CapabilityReport("GPS", CapabilityStatus.BLOCKED, reasons)

    if fix == 2:
        reasons.append(
            "2D GPS fix only. Altitude and approach accuracy degraded. Required: 3D fix."
        )
        return CapabilityReport("GPS", CapabilityStatus.WARN, reasons)

    return CapabilityReport("GPS", CapabilityStatus.READY)


def _eval_tak_output(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if not state.tak_output_enabled:
        reasons.append(
            "TAK output disabled. Set enabled = true in [tak] config to activate."
        )
        return CapabilityReport("TAK Output", CapabilityStatus.BLOCKED, reasons)

    if not state.tak_output_running:
        reasons.append(
            "TAK output enabled but multicast socket not running. "
            "Check network and multicast group settings."
        )
        return CapabilityReport("TAK Output", CapabilityStatus.WARN, reasons)

    return CapabilityReport("TAK Output", CapabilityStatus.READY)


def _eval_tak_commands(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if not state.tak_allowed_callsigns_set:
        reasons.append(
            "No allowed callsigns configured. "
            "Set allowed_callsigns in [tak] config to enable GeoChat command intake."
        )
        return CapabilityReport("TAK Commands", CapabilityStatus.BLOCKED, reasons)

    if not state.tak_hmac_secret_set:
        reasons.append(
            "Callsigns set but command_hmac_secret is empty. "
            "Commands are spoofable over multicast. Set command_hmac_secret."
        )
        return CapabilityReport("TAK Commands", CapabilityStatus.WARN, reasons)

    return CapabilityReport("TAK Commands", CapabilityStatus.READY)


def _eval_disk(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if state.disk_free_gb is None:
        reasons.append(
            f"Cannot read disk usage for output dir: {state.disk_output_dir}."
        )
        return CapabilityReport("Disk", CapabilityStatus.WARN, reasons)

    free = state.disk_free_gb
    if free < _DISK_BLOCKED_GB:
        reasons.append(
            f"Disk critically low: {free:.1f} GB free. "
            "Detection logging will fail. Clear space immediately."
        )
        return CapabilityReport("Disk", CapabilityStatus.BLOCKED, reasons)

    if free < _DISK_WARN_GB:
        reasons.append(
            f"Disk low: {free:.1f} GB free. "
            "Detection logs may fill storage before sortie end."
        )
        return CapabilityReport("Disk", CapabilityStatus.WARN, reasons)

    return CapabilityReport("Disk", CapabilityStatus.READY)


def _eval_time_source(state: SystemState) -> CapabilityReport:
    """Stub evaluator. Real GPS-disciplined time sync wired in #155."""
    reasons = [
        "Time source: RTC only. GPS-disciplined sync not yet wired (see #155). "
        "Log timestamps may drift during long sorties."
    ]
    return CapabilityReport(
        "Time Source",
        CapabilityStatus.WARN,
        reasons,
        fix_target="#155",
    )


def _eval_vehicle_profile(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if not state.vehicle_profile or not state.vehicle_profile_present:
        reasons.append(
            "No vehicle profile selected. "
            "Set profile in [vehicle] config (drone / usv / ugv / fw)."
        )
        return CapabilityReport(
            "Vehicle Profile",
            CapabilityStatus.BLOCKED,
            reasons,
            fix_target="#148",
        )

    return CapabilityReport("Vehicle Profile", CapabilityStatus.READY)


def _eval_schema_version(state: SystemState) -> CapabilityReport:
    reasons: list[str] = []

    if not state.schema_version_present or not state.schema_version:
        reasons.append(
            "Schema version missing from [meta] section. "
            "Config may pre-date versioning (see #156 / PR #162)."
        )
        return CapabilityReport(
            "Schema Version",
            CapabilityStatus.BLOCKED,
            reasons,
            fix_target="#156",
        )

    return CapabilityReport("Schema Version", CapabilityStatus.READY)


def _eval_autonomy_live(state: SystemState) -> CapabilityReport:
    return CapabilityReport(
        "Autonomy Live",
        CapabilityStatus.BLOCKED,
        reasons=["ARMED mode not implemented. Autonomous engagement gated on #147."],
        fix_target="#147",
    )


def _eval_drop(state: SystemState) -> CapabilityReport:
    return CapabilityReport(
        "Drop",
        CapabilityStatus.BLOCKED,
        reasons=["ARMED mode not implemented. Payload drop gated on #147."],
        fix_target="#147",
    )


def _eval_rf_hunt(state: SystemState) -> CapabilityReport:
    return CapabilityReport(
        "RF Hunt",
        CapabilityStatus.BLOCKED,
        reasons=["ARMED mode not implemented. RF hunt autonomous nav gated on #147."],
        fix_target="#147",
    )


# ── Registry ──────────────────────────────────────────────────────────────────

_EVALUATORS: list[tuple[str, Any]] = [
    ("Detection", _eval_detection),
    ("MAVLink", _eval_mavlink),
    ("GPS", _eval_gps),
    ("TAK Output", _eval_tak_output),
    ("TAK Commands", _eval_tak_commands),
    ("Disk", _eval_disk),
    ("Time Source", _eval_time_source),
    ("Vehicle Profile", _eval_vehicle_profile),
    ("Schema Version", _eval_schema_version),
    ("Autonomy Live", _eval_autonomy_live),
    ("Drop", _eval_drop),
    ("RF Hunt", _eval_rf_hunt),
]

#: Ordered list of all registered capability names.
CAPABILITY_NAMES: list[str] = [name for name, _ in _EVALUATORS]


def evaluate_all(state: SystemState) -> list[CapabilityReport]:
    """Run every registered evaluator and return the full report list."""
    reports: list[CapabilityReport] = []
    for _name, evaluator in _EVALUATORS:
        try:
            report = evaluator(state)
        except Exception as exc:
            # Never let a broken evaluator crash the status page.
            report = CapabilityReport(
                name=_name,
                status=CapabilityStatus.WARN,
                reasons=[f"Evaluator error: {exc}"],
            )
        reports.append(report)
    return reports
