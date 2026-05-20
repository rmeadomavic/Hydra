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

import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from hydra_detect.config_schema import SECTION_AUTONOMOUS

logger = logging.getLogger(__name__)


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
    disk_total_gb: float | None = None
    disk_free_pct: float | None = None
    disk_output_dir: str = "/tmp"
    # Operator-tunable thresholds. Issue #226 ([capability.disk] section).
    # Defaults match the storage-rotation gate; both pct AND absolute floor
    # must trip for BLOCKED so 5% of a 4 TB NVMe (200 GB headroom) does not
    # falsely flag a unit whose missions consume tens of GB.
    disk_warn_pct: float = 15.0
    disk_blocked_pct: float = 5.0
    disk_blocked_min_free_gb: float = 5.0

    # Time source (stub — #155 will wire GPS-disciplined PPS / NTP)
    time_source: str = "RTC"

    # Vehicle profile
    vehicle_profile: str = ""
    vehicle_profile_present: bool = False

    # Schema version
    schema_version: str | None = None
    schema_version_present: bool = False

    # SoC temperatures in degrees Celsius (None on non-Jetson hosts).
    # Populated from system.read_jetson_stats() via the merged
    # stream_state stats dict; see build_system_state.
    cpu_temp_c: float | None = None
    gpu_temp_c: float | None = None

    # Sustained-below-target FPS window in seconds. Populated from the
    # module-level _fps_tracker singleton, which the pipeline feeds via
    # record_fps() each time it pushes new pipeline stats.
    fps_below_target_sustained_sec: float = 0.0

    # Servo / pan-tilt tracker. Powered by ServoState (servo/servo_state.py).
    # ``servo_enabled`` reflects whether a controller has claimed the servo
    # channel; ``servo_locked_track_id`` is the track the gimbal is currently
    # centered on (None when scanning / no lock).
    servo_enabled: bool = False
    servo_locked_track_id: int | None = None

    # Autonomy module state. ``autonomy_mode`` is one of dryrun / shadow /
    # live (display-level state machine — see autonomous.set_mode).
    # ``autonomy_enabled`` reflects the boot-time enable flag.
    # ``autonomy_geofence_present`` is True when a valid polygon (>=3 pts)
    # or non-zero centre+radius is configured. Autonomy Live also needs
    # operator confirmation that we are in OperatingMode.ARMED.
    autonomy_mode: str = "dryrun"
    autonomy_enabled: bool = False
    autonomy_geofence_present: bool = False
    operating_mode: str = "OBSERVE"

    # Fleet view. ``identity_callsign`` is read from config or identity.ini;
    # blank when first-boot identity has not been generated yet (see
    # identity_boot.py).
    identity_callsign: str = ""

    # Raw config parser (optional — used by some evaluators)
    cfg: Any = None


# ── System state builder ─────────────────────────────────────────────────────

def build_system_state(
    stream_state: Any | None = None,
    mavlink_ref: Any | None = None,
    tak_output_ref: Any | None = None,
    tak_input_ref: Any | None = None,
    cfg: Any | None = None,
    servo_state_ref: Any | None = None,
    autonomy_ref: Any | None = None,
    operating_mode: str | None = None,
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
            # Jetson SoC temps are merged into stats by pipeline.facade
            # via system.read_jetson_stats(); read straight through.
            cpu_t = stats.get("cpu_temp_c")
            gpu_t = stats.get("gpu_temp_c")
            state.cpu_temp_c = float(cpu_t) if cpu_t is not None else None
            state.gpu_temp_c = float(gpu_t) if gpu_t is not None else None
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
        # Operator-tunable thresholds. Prefer [capability.disk]; fall back to
        # legacy [storage] keys so existing operator configs keep working
        # without edit (backward compat per #226 acceptance criteria).
        try:
            state.disk_warn_pct = float(cfg.get(
                "capability.disk", "warn_pct",
                fallback=cfg.get("storage", "disk_warn_pct", fallback="15"),
            ))
            state.disk_blocked_pct = float(cfg.get(
                "capability.disk", "blocked_pct",
                fallback=cfg.get("storage", "disk_block_pct", fallback="5"),
            ))
            state.disk_blocked_min_free_gb = float(cfg.get(
                "capability.disk", "blocked_min_free_gb", fallback="5",
            ))
        except Exception:
            pass
    try:
        usage = shutil.disk_usage(state.disk_output_dir)
        state.disk_free_gb = usage.free / (1024 ** 3)
        state.disk_total_gb = usage.total / (1024 ** 3)
        if usage.total > 0:
            state.disk_free_pct = (usage.free / usage.total) * 100.0
    except Exception:
        state.disk_free_gb = None
        state.disk_total_gb = None
        state.disk_free_pct = None

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

    # ── Servo / Follow ────────────────────────────────────────────────────
    if servo_state_ref is not None:
        try:
            servo_info = servo_state_ref.get_api_status()
            state.servo_enabled = bool(servo_info.get("enabled", False))
            lock = servo_info.get("locked_track_id")
            state.servo_locked_track_id = (
                int(lock) if lock is not None else None
            )
        except Exception:
            pass

    # ── Autonomy ──────────────────────────────────────────────────────────
    if autonomy_ref is not None:
        try:
            getter = getattr(autonomy_ref, "get_mode", None)
            if callable(getter):
                state.autonomy_mode = str(getter())
            state.autonomy_enabled = bool(getattr(autonomy_ref, "enabled", False))
            has_fence = getattr(autonomy_ref, "_has_valid_geofence", None)
            if callable(has_fence):
                state.autonomy_geofence_present = bool(has_fence())
        except Exception:
            pass
    elif cfg is not None:
        try:
            # Legacy-config guard (#247 / PR #252). The canonical schema
            # section is [autonomous]; an early-development config.ini may
            # carry a stale [autonomy] section. When the legacy section is
            # present and the canonical one is absent, the reads below all
            # fall to defaults — warn the operator instead of silently
            # disabling autonomy on a unit they tuned.
            try:
                has_legacy = cfg.has_section("autonomy")
                has_canonical = cfg.has_section(SECTION_AUTONOMOUS)
            except Exception:
                has_legacy = has_canonical = False
            if has_legacy and not has_canonical:
                logger.warning(
                    "Config has a legacy [autonomy] section but no "
                    "[%s] section — autonomy settings will fall to "
                    "defaults (disabled, no geofence). Rename the section "
                    "to [%s] to restore your tuned values.",
                    SECTION_AUTONOMOUS, SECTION_AUTONOMOUS,
                )

            mode_raw = cfg.get(
                SECTION_AUTONOMOUS, "mode", fallback="dryrun",
            ).strip().lower()
            if mode_raw in ("dryrun", "shadow", "live"):
                state.autonomy_mode = mode_raw
            enabled_raw = cfg.get(
                SECTION_AUTONOMOUS, "enabled", fallback="false",
            ).strip()
            state.autonomy_enabled = enabled_raw.lower() in ("1", "true", "yes")
            # Geofence presence: any non-zero centre, or a polygon with 3+ pts.
            poly_raw = cfg.get(
                SECTION_AUTONOMOUS, "geofence_polygon", fallback="",
            ).strip()
            if poly_raw:
                pts = [p for p in poly_raw.split(";") if "," in p]
                state.autonomy_geofence_present = len(pts) >= 3
            if not state.autonomy_geofence_present:
                try:
                    lat = float(cfg.get(
                        SECTION_AUTONOMOUS, "geofence_lat", fallback="0",
                    ).strip())
                    lon = float(cfg.get(
                        SECTION_AUTONOMOUS, "geofence_lon", fallback="0",
                    ).strip())
                    state.autonomy_geofence_present = (lat != 0.0 or lon != 0.0)
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

    # ── Operating mode ────────────────────────────────────────────────────
    if operating_mode is not None:
        state.operating_mode = str(operating_mode).upper()
    elif cfg is not None:
        try:
            state.operating_mode = cfg.get(
                "system", "mode", fallback="OBSERVE",
            ).strip().upper()
        except Exception:
            pass

    # ── Identity / Fleet View ─────────────────────────────────────────────
    if cfg is not None:
        try:
            state.identity_callsign = cfg.get(
                "identity", "callsign", fallback="",
            ).strip()
        except Exception:
            pass

    # ── Performance — sustained FPS-below-target window ──────────────────
    # READ-ONLY here. The tracker is fed exclusively by the pipeline hot
    # loop via record_fps() — see hydra_detect/pipeline/facade.py. Reading
    # stream_state.get_stats()["fps"] from the readiness poll and pushing
    # it back into the tracker would let stale samples masquerade as fresh
    # measurements: when the pipeline stalls, the cached "fps" value never
    # changes, so every poll would re-stamp the last good value as a new
    # above-threshold sample and reset _below_since=None. That defeats
    # the sustained-below-target signal in exactly the failure mode it is
    # meant to catch (per PR #183 Codex review).
    state.fps_below_target_sustained_sec = _fps_tracker.sustained_below_sec(
        time.monotonic(),
    )

    return state


# ── Evaluator helpers ─────────────────────────────────────────────────────────

_STALE_FRAME_SEC = 5.0       # frame age beyond which detection is WARN
_STALE_HEARTBEAT_SEC = 5.0   # heartbeat age beyond which MAVLink is WARN
_DISK_WARN_GB = 2.0          # free disk WARN threshold
_DISK_BLOCKED_GB = 0.5       # free disk BLOCKED threshold
# SoC temp thresholds. Orin Nano begins thermal throttling around 85-92 °C
# depending on rail; WARN at 75 leaves the operator margin to land/shade.
_SOC_TEMP_WARN_C = 75.0
_SOC_TEMP_BLOCK_C = 90.0
# FPS thresholds. 5 FPS is the documented Hydra minimum on Jetson (CLAUDE.md).
# WARN fires only after FPS has been continuously below for the full window
# so a single transient dip does not flap the operator status.
_FPS_WARN_THRESHOLD = 5.0
_FPS_WARN_WINDOW_SEC = 30.0
# Performance evaluator thermal-hint threshold. When the hottest reachable
# SoC temp is within this many degrees of the thermal WARN threshold, the
# Performance WARN reason text points at thermal causes; otherwise it points
# at config under-provisioning. 5 °C below WARN gives the FPS-drop signal
# room to lead the thermal signal under typical Jetson Orin thermal mass.
_PERF_THERMAL_HINT_C = 5.0


# ── Sustained-FPS tracker singleton ──────────────────────────────────────────

class _FpsTracker:
    """Thread-safe tracker of how long FPS has been below the warn threshold.

    Fed exclusively by the pipeline hot loop via record_fps() with fresh
    per-frame measurements. Read by the readiness page via
    sustained_below_sec() during evaluator runs. None samples (FPS not yet
    known) leave the existing state untouched so a brief pipeline transient
    does not mask an ongoing thermal event.

    Important: do NOT add a re-feed path from build_system_state or any
    other readiness-poll site. The cached fps in stream_state.get_stats()
    has no freshness timestamp; pushing it on every poll would re-stamp
    the last good value as a fresh above-threshold sample and reset
    _below_since=None even when the pipeline has stalled. See PR #183
    Codex review for the failure mode.
    """

    def __init__(self) -> None:
        self._below_since: float | None = None
        self._lock = threading.Lock()

    def record(self, fps: float | None, now: float) -> None:
        if fps is None:
            return
        with self._lock:
            if fps < _FPS_WARN_THRESHOLD:
                if self._below_since is None:
                    self._below_since = now
            else:
                self._below_since = None

    def sustained_below_sec(self, now: float) -> float:
        with self._lock:
            if self._below_since is None:
                return 0.0
            return max(0.0, now - self._below_since)

    def reset(self) -> None:
        with self._lock:
            self._below_since = None


_fps_tracker = _FpsTracker()


def record_fps(fps: float | None, now_s: float | None = None) -> None:
    """Public API: pipeline pushes the current pipeline FPS into the tracker."""
    _fps_tracker.record(fps, now_s if now_s is not None else time.monotonic())


def sustained_fps_below_sec(now_s: float | None = None) -> float:
    """Public API: how long current FPS has been below the warn threshold."""
    return _fps_tracker.sustained_below_sec(
        now_s if now_s is not None else time.monotonic(),
    )


def reset_fps_tracker() -> None:
    """Public API: zero the sustained-below window.

    Production callers: model-swap and pipeline-restart paths in
    ``hydra_detect/pipeline/facade.py``. A model swap drops the pipeline
    FPS for the duration of the new model load (typically 5-15 s on
    Jetson Orin Nano); without this reset the dip accumulates into the
    sustained-below window and produces a 30 s false WARN labelled as
    thermal throttling on the readiness page. Same reasoning for in-
    process pipeline restart via ``/api/restart`` — operators who restart
    *because* of a WARN must see the WARN clear rather than persist.

    Closes adversarial findings R3-2 + R3-8 from PR #183.
    """
    _fps_tracker.reset()


def reset_fps_tracker_for_test() -> None:
    """Test-only alias — kept for backwards compat with existing tests."""
    reset_fps_tracker()


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
    """Platform-aware disk gate (#226).

    BLOCKED requires BOTH ``disk_free_pct < blocked_pct`` AND
    ``disk_free_gb < blocked_min_free_gb``. 5% of a 4 TB NVMe is 200 GB
    of headroom and should not block missions; 5% of a 32 GB SD card is
    1.6 GB and should. WARN fires on pct alone — operators get the
    dashboard banner well before the absolute floor matters.

    Falls back to absolute-GB legacy thresholds when ``disk_free_pct`` is
    not available (test fixtures and older states that pre-date #226).
    """
    reasons: list[str] = []

    if state.disk_free_gb is None:
        reasons.append(
            f"Cannot read disk usage for output dir: {state.disk_output_dir}."
        )
        return CapabilityReport("Disk", CapabilityStatus.WARN, reasons)

    free = state.disk_free_gb

    # Platform-aware path — preferred when pct + total are available.
    if state.disk_free_pct is not None:
        pct = state.disk_free_pct
        warn_pct = state.disk_warn_pct
        block_pct = state.disk_blocked_pct
        block_min_gb = state.disk_blocked_min_free_gb

        if pct < block_pct and free < block_min_gb:
            reasons.append(
                f"Disk critically low: {pct:.1f}% free ({free:.2f} GB). "
                f"Below {block_pct:.0f}% and under {block_min_gb:.1f} GB floor. "
                "Refusing new mission bundles and pausing crop emission. "
                "Detection metadata logging continues. Run storage rotation."
            )
            return CapabilityReport(
                "Disk", CapabilityStatus.BLOCKED, reasons,
            )

        if pct < warn_pct:
            reasons.append(
                f"Disk low: {pct:.1f}% free ({free:.2f} GB). "
                f"Below WARN threshold ({warn_pct:.0f}%). "
                "Consider running storage rotation before next sortie."
            )
            return CapabilityReport("Disk", CapabilityStatus.WARN, reasons)

        return CapabilityReport("Disk", CapabilityStatus.READY)

    # Legacy absolute-GB fallback (test fixtures without pct/total).
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


def _eval_thermal(state: SystemState) -> CapabilityReport:
    """Operator-visible SoC temperature gate.

    Treats unknown temps (None on both zones) as READY rather than WARN —
    dev boxes and SITL hosts have no sysfs thermal data, and surfacing a
    nag-warn there would train operators to ignore the signal.
    """
    cpu = state.cpu_temp_c
    gpu = state.gpu_temp_c

    available = [t for t in (cpu, gpu) if t is not None]
    if not available:
        return CapabilityReport("Thermal", CapabilityStatus.READY)

    hottest = max(available)
    detail = (
        f"cpu={cpu:.1f}C, gpu={gpu:.1f}C"
        if (cpu is not None and gpu is not None)
        else (f"cpu={cpu:.1f}C" if cpu is not None else f"gpu={gpu:.1f}C")
    )

    if hottest > _SOC_TEMP_BLOCK_C:
        reasons = [
            f"SoC at {hottest:.1f}C ({detail}). "
            f"Above thermal-throttle limit of {_SOC_TEMP_BLOCK_C:.0f}C — "
            "performance is degraded. Land or shade the unit immediately."
        ]
        return CapabilityReport("Thermal", CapabilityStatus.BLOCKED, reasons)

    if hottest > _SOC_TEMP_WARN_C:
        reasons = [
            f"SoC at {hottest:.1f}C ({detail}). "
            f"Above WARN threshold of {_SOC_TEMP_WARN_C:.0f}C — approaching "
            "thermal throttling. Increase airflow or reduce load."
        ]
        return CapabilityReport("Thermal", CapabilityStatus.WARN, reasons)

    return CapabilityReport("Thermal", CapabilityStatus.READY)


def _eval_performance(state: SystemState) -> CapabilityReport:
    """WARN when pipeline FPS has been below the Jetson minimum for >= 30 s.

    A single transient dip is ignored — the tracker only counts continuous
    below-threshold time. Reason text branches on observed SoC temperature:
    when CPU or GPU is within ``_PERF_THERMAL_HINT_C`` of the thermal WARN
    threshold (data already on SystemState from the Thermal evaluator), the
    operator is pointed at thermal causes; otherwise the operator is pointed
    at config under-provisioning (heavy model, telephoto USV, wide-class
    surveillance — all legitimate sub-target FPS configurations).

    Closes adversarial finding R3-4 from PR #183: the prior unconditional
    "Likely thermal throttling or detector overload" reason text actively
    misdirected operators on three legitimate config combinations.
    """
    sustained = state.fps_below_target_sustained_sec
    if sustained < _FPS_WARN_WINDOW_SEC:
        return CapabilityReport("Performance", CapabilityStatus.READY)

    base_msg = (
        f"Pipeline FPS below {_FPS_WARN_THRESHOLD:.0f} for "
        f"{sustained:.0f}s (window {_FPS_WARN_WINDOW_SEC:.0f}s)."
    )

    cpu = state.cpu_temp_c
    gpu = state.gpu_temp_c
    available_temps = [t for t in (cpu, gpu) if t is not None]
    hottest = max(available_temps) if available_temps else None
    thermal_hint_threshold = _SOC_TEMP_WARN_C - _PERF_THERMAL_HINT_C

    if hottest is not None and hottest >= thermal_hint_threshold:
        cause = (
            f" SoC at {hottest:.1f}C is within {_PERF_THERMAL_HINT_C:.0f}C "
            f"of the thermal WARN threshold ({_SOC_TEMP_WARN_C:.0f}C) — "
            "likely thermal throttling. Increase airflow, shade the unit, "
            "or land."
        )
    elif hottest is not None:
        cause = (
            f" SoC at {hottest:.1f}C is below the thermal hint threshold — "
            "thermal cause unlikely. Active profile may be heavier than "
            "the platform supports: check model size, input resolution, "
            "and active capability set."
        )
    else:
        cause = (
            " SoC temperature unavailable — cannot rule out thermal "
            "cause. Check active profile (model size, input resolution, "
            "active capability set) and SoC temps if reachable."
        )

    return CapabilityReport(
        "Performance",
        CapabilityStatus.WARN,
        [base_msg + cause],
    )


def _eval_follow(state: SystemState) -> CapabilityReport:
    """Follow needs GPS + MAVLink + an operator-confirmed track lock.

    The minimum operational set is documented in the issue body:
    "Follow READY = GPS + MAVLink + target lock available".
    """
    reasons: list[str] = []

    if not state.mavlink_connected:
        reasons.append("MAVLink not connected — cannot command vehicle.")
        return CapabilityReport(
            "Follow", CapabilityStatus.BLOCKED, reasons, fix_target="MAVLink",
        )

    if state.gps_fix < 3:
        reasons.append(
            f"GPS fix={state.gps_fix}. Follow needs 3D fix (type 3 or higher) "
            "for position-based commands."
        )
        return CapabilityReport(
            "Follow", CapabilityStatus.BLOCKED, reasons, fix_target="GPS",
        )

    if state.servo_locked_track_id is None:
        reasons.append(
            "No target lock. Operator must select a track in the dashboard "
            "before Follow can engage."
        )
        return CapabilityReport("Follow", CapabilityStatus.WARN, reasons)

    return CapabilityReport("Follow", CapabilityStatus.READY)


def _eval_servo_tracking(state: SystemState) -> CapabilityReport:
    """Servo Tracking READY when a controller has claimed the servo channel.

    WARN when channel is enabled but no track is locked (idle scan). BLOCKED
    when no controller is wired at all — the operator gets a precise reason
    pointing at servo config.
    """
    reasons: list[str] = []

    if not state.servo_enabled:
        reasons.append(
            "Servo channel not claimed. Enable servo_tracker in [servo] config "
            "or check that the pan-tilt controller is wired."
        )
        return CapabilityReport(
            "Servo Tracking", CapabilityStatus.BLOCKED, reasons,
        )

    if state.servo_locked_track_id is None:
        reasons.append(
            "Servo enabled but scanning — no track lock. Select a track in "
            "the dashboard or wait for autonomous lock."
        )
        return CapabilityReport(
            "Servo Tracking", CapabilityStatus.WARN, reasons,
        )

    return CapabilityReport("Servo Tracking", CapabilityStatus.READY)


def _eval_autonomy_dryrun(state: SystemState) -> CapabilityReport:
    """Dryrun autonomy: evaluator runs, no MAVLink commands fire.

    Always READY when MAVLink is up (we still read MAVLink to read GPS), and
    a geofence is present. The dryrun mode is exactly the safe default — it
    needs the LEAST gating. BLOCKED only when prerequisites that even the
    evaluator can't run without are missing.
    """
    reasons: list[str] = []

    if not state.mavlink_connected:
        reasons.append(
            "MAVLink not connected. Dryrun evaluator needs GPS + vehicle "
            "mode data to score detections."
        )
        return CapabilityReport(
            "Autonomy Dryrun", CapabilityStatus.BLOCKED, reasons,
            fix_target="MAVLink",
        )

    if not state.autonomy_geofence_present:
        reasons.append(
            "No geofence configured. Set geofence_polygon or geofence_lat/lon "
            "+ radius in [autonomy] config — dryrun rejects every detection "
            "without one."
        )
        return CapabilityReport(
            "Autonomy Dryrun", CapabilityStatus.BLOCKED, reasons,
        )

    return CapabilityReport("Autonomy Dryrun", CapabilityStatus.READY)


def _eval_autonomy_shadow(state: SystemState) -> CapabilityReport:
    """Shadow autonomy: full evaluation runs, servo can lock but no command fires.

    Same preconditions as Dryrun plus a servo channel — Shadow is the bridge
    between Dryrun (read-only) and Live (full engagement). Without servo,
    Shadow is indistinguishable from Dryrun, so we BLOCK it to keep the
    distinction meaningful to the operator.
    """
    reasons: list[str] = []

    if not state.mavlink_connected:
        reasons.append(
            "MAVLink not connected. Shadow needs GPS + vehicle mode data."
        )
        return CapabilityReport(
            "Autonomy Shadow", CapabilityStatus.BLOCKED, reasons,
            fix_target="MAVLink",
        )

    if not state.autonomy_geofence_present:
        reasons.append(
            "No geofence configured. Shadow requires the same geofence as "
            "Dryrun — set geofence in [autonomy] config."
        )
        return CapabilityReport(
            "Autonomy Shadow", CapabilityStatus.BLOCKED, reasons,
        )

    if not state.servo_enabled:
        reasons.append(
            "Shadow without servo is identical to Dryrun. Enable the servo "
            "channel so Shadow can demonstrate the lock behaviour Live will use."
        )
        return CapabilityReport(
            "Autonomy Shadow", CapabilityStatus.BLOCKED, reasons,
            fix_target="Servo Tracking",
        )

    return CapabilityReport("Autonomy Shadow", CapabilityStatus.READY)


def _eval_log_export(state: SystemState) -> CapabilityReport:
    """Log Export readiness — disk space + writable output dir.

    The CLI exporter (review_export.py) writes HTML reports to the same
    output_dir the detection logger feeds. If the disk is full or the
    directory is unreadable, the export will fail silently mid-flight; this
    evaluator surfaces the precondition before the operator clicks Export.
    """
    reasons: list[str] = []

    if state.disk_free_gb is None:
        reasons.append(
            f"Cannot read output dir: {state.disk_output_dir}. "
            "Export will fail. Verify path exists and is readable."
        )
        return CapabilityReport(
            "Log Export", CapabilityStatus.BLOCKED, reasons,
        )

    if state.disk_free_gb < _DISK_BLOCKED_GB:
        reasons.append(
            f"Disk critically low: {state.disk_free_gb:.1f} GB. "
            "Export will fail mid-write. Clear space before exporting."
        )
        return CapabilityReport(
            "Log Export", CapabilityStatus.BLOCKED, reasons,
            fix_target="Disk",
        )

    if state.disk_free_gb < _DISK_WARN_GB:
        reasons.append(
            f"Disk low: {state.disk_free_gb:.1f} GB. "
            "Large exports may run out of space."
        )
        return CapabilityReport("Log Export", CapabilityStatus.WARN, reasons)

    return CapabilityReport("Log Export", CapabilityStatus.READY)


def _eval_fleet_view(state: SystemState) -> CapabilityReport:
    """Fleet View needs a unit identity (callsign).

    The fleet page polls peer Hydra instances and labels them by callsign.
    Without an identity, this unit cannot identify itself to peers and the
    fleet page will not render this row in any neighbour's table. Identity
    is generated by first-boot Platform Setup (identity_boot.py).
    """
    reasons: list[str] = []

    if not state.identity_callsign:
        reasons.append(
            "Unit callsign not set. Run Platform Setup or set "
            "[identity].callsign to register this unit on the fleet view."
        )
        return CapabilityReport(
            "Fleet View", CapabilityStatus.BLOCKED, reasons,
        )

    return CapabilityReport("Fleet View", CapabilityStatus.READY)


def _eval_autonomy_live(state: SystemState) -> CapabilityReport:
    """Live autonomy: real MAVLink commands fire.

    Live is the most-gated capability. Preconditions: MAVLink + 3D GPS +
    geofence + servo + autonomy.enabled=true + OperatingMode=ARMED.
    The ARMED mode requirement is the operator's explicit two-step confirm
    — see operating_mode.set_mode(confirmed_twice=True).
    """
    reasons: list[str] = []

    if not state.mavlink_connected:
        reasons.append("MAVLink not connected — cannot command vehicle.")
        return CapabilityReport(
            "Autonomy Live", CapabilityStatus.BLOCKED, reasons,
            fix_target="MAVLink",
        )

    if state.gps_fix < 3:
        reasons.append(
            f"GPS fix={state.gps_fix}. Live needs 3D fix for position math."
        )
        return CapabilityReport(
            "Autonomy Live", CapabilityStatus.BLOCKED, reasons,
            fix_target="GPS",
        )

    if not state.autonomy_geofence_present:
        reasons.append(
            "No geofence configured. Live without a geofence is rejected by "
            "the evaluator — set geofence in [autonomy] config."
        )
        return CapabilityReport(
            "Autonomy Live", CapabilityStatus.BLOCKED, reasons,
        )

    if not state.autonomy_enabled:
        reasons.append(
            "Autonomy disabled. Set [autonomy].enabled=true in config."
        )
        return CapabilityReport(
            "Autonomy Live", CapabilityStatus.BLOCKED, reasons,
        )

    if state.operating_mode != "ARMED":
        reasons.append(
            f"Operating mode is {state.operating_mode}. Live engagement is "
            "gated on OperatingMode.ARMED — operator must confirm twice from "
            "the mode panel."
        )
        return CapabilityReport(
            "Autonomy Live", CapabilityStatus.BLOCKED, reasons,
            fix_target="#147",
        )

    return CapabilityReport("Autonomy Live", CapabilityStatus.READY)


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
    ("Thermal", _eval_thermal),
    ("Performance", _eval_performance),
    ("Servo Tracking", _eval_servo_tracking),
    ("Follow", _eval_follow),
    ("Autonomy Dryrun", _eval_autonomy_dryrun),
    ("Autonomy Shadow", _eval_autonomy_shadow),
    ("Autonomy Live", _eval_autonomy_live),
    ("Drop", _eval_drop),
    ("RF Hunt", _eval_rf_hunt),
    ("Log Export", _eval_log_export),
    ("Fleet View", _eval_fleet_view),
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
