"""Capability status API router — GET /api/capabilities.

Registered from web/server.py with a single include_router() call.
No modifications to existing endpoints.

Caches the result for _CACHE_TTL_SEC to avoid evaluating all subsystems
on every poll cycle. Safe to call at dashboard poll rates (1 Hz or faster).
"""

from __future__ import annotations

import datetime
import threading
import time
from typing import Any

from fastapi import APIRouter

from hydra_detect.capability_status import (
    CapabilityReport,
    CapabilityStatus,
    SystemState,
    build_system_state,
    evaluate_all,
)

router = APIRouter()

# ── TTL cache ─────────────────────────────────────────────────────────────────

_CACHE_TTL_SEC = 2.0  # refresh no faster than every 2s

_cache_lock = threading.Lock()
_cached_result: dict[str, Any] | None = None
_cache_expires_at: float = 0.0

# References to live Hydra components — set by wire_components().
# All default to None (safe: evaluators return conservative status).
_stream_state_ref: Any = None
_mavlink_ref: Any = None
_tak_output_ref: Any = None
_tak_input_ref: Any = None
_cfg_ref: Any = None
_servo_state_ref: Any = None
_autonomy_ref: Any = None
_operating_mode_getter: Any = None


def wire_components(
    stream_state: Any | None = None,
    mavlink_ref: Any | None = None,
    tak_output_ref: Any | None = None,
    tak_input_ref: Any | None = None,
    cfg: Any | None = None,
    servo_state_ref: Any | None = None,
    autonomy_ref: Any | None = None,
    operating_mode_getter: Any | None = None,
) -> None:
    """Connect live Hydra component references so evaluators read real signals.

    Called once from server.py after the pipeline is wired. Safe to call
    multiple times — later calls overwrite earlier ones.

    operating_mode_getter is a no-arg callable returning the current mode
    string (e.g. ``lambda: _read_current_mode().value``). Decoupled so the
    capability_api module does not import operating_mode at startup.
    """
    global _stream_state_ref, _mavlink_ref, _tak_output_ref, _tak_input_ref, _cfg_ref
    global _servo_state_ref, _autonomy_ref, _operating_mode_getter
    _stream_state_ref = stream_state
    _mavlink_ref = mavlink_ref
    _tak_output_ref = tak_output_ref
    _tak_input_ref = tak_input_ref
    _cfg_ref = cfg
    _servo_state_ref = servo_state_ref
    _autonomy_ref = autonomy_ref
    _operating_mode_getter = operating_mode_getter


def reset_cache() -> None:
    """Expire the TTL cache. Used by tests to force a fresh evaluation."""
    global _cached_result, _cache_expires_at
    with _cache_lock:
        _cached_result = None
        _cache_expires_at = 0.0


def _serialize_report(r: CapabilityReport) -> dict[str, Any]:
    return {
        "name": r.name,
        "status": r.status.value,
        "reasons": r.reasons,
        "fix_target": r.fix_target,
    }


def _build_response() -> dict[str, Any]:
    op_mode: str | None = None
    if _operating_mode_getter is not None:
        try:
            op_mode = _operating_mode_getter()
        except Exception:
            op_mode = None
    state: SystemState = build_system_state(
        stream_state=_stream_state_ref,
        mavlink_ref=_mavlink_ref,
        tak_output_ref=_tak_output_ref,
        tak_input_ref=_tak_input_ref,
        cfg=_cfg_ref,
        servo_state_ref=_servo_state_ref,
        autonomy_ref=_autonomy_ref,
        operating_mode=op_mode,
    )
    reports = evaluate_all(state)
    # Update the Disk-BLOCKED gate state for synchronous callers
    # (mission-start endpoint, detection-logger crop suppression).
    # Issue #226: the registry is a one-stop source for the gate state;
    # piggy-backing on the cached response means we do not re-walk the
    # evaluators on every mission-start hit.
    disk_report = next((r for r in reports if r.name == "Disk"), None)
    blocked = disk_report is not None and disk_report.status == CapabilityStatus.BLOCKED
    _set_disk_blocked(
        blocked,
        disk_report.reasons[0] if blocked and disk_report.reasons else "",
    )
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "capabilities": [_serialize_report(r) for r in reports],
    }


# ── Disk-BLOCKED gate state (issue #226) ──────────────────────────────────────
#
# Mirrored from the most recent capability evaluation so the mission-start
# endpoint and crop-emission gate can read it without re-running the
# registry on every request. Updated by _build_response (so the 2 s cache
# TTL governs the freshness, same as the dashboard).
_disk_gate_lock = threading.Lock()
_disk_blocked: bool = False
_disk_blocked_reason: str = ""
# Listeners notified when the disk-BLOCKED gate flips. Used by the pipeline
# to toggle crop emission off / back on.
_disk_gate_listeners: list[Any] = []


def _set_disk_blocked(blocked: bool, reason: str) -> None:
    """Update the cached disk-BLOCKED state and notify listeners on flip."""
    global _disk_blocked, _disk_blocked_reason
    with _disk_gate_lock:
        flipped = blocked != _disk_blocked
        _disk_blocked = blocked
        _disk_blocked_reason = reason
        listeners = list(_disk_gate_listeners) if flipped else []
    for listener in listeners:
        try:
            listener(blocked, reason)
        except Exception:
            # A broken listener must not stall the readiness path.
            pass


def is_disk_blocked() -> tuple[bool, str]:
    """Return ``(blocked, reason)`` from the most recent capability evaluation.

    Mission-start and crop-emission gates call this. The state is updated by
    ``/api/capabilities`` poll cycles (2 s TTL). A unit that has never been
    polled returns ``(False, "")`` — fail-open so a missing dashboard does
    not refuse missions, matching #245 Phase A's conservative gate posture.
    """
    with _disk_gate_lock:
        return _disk_blocked, _disk_blocked_reason


def register_disk_gate_listener(listener: Any) -> None:
    """Register a callable invoked when the disk-BLOCKED gate flips.

    Listener signature: ``listener(blocked: bool, reason: str) -> None``.
    Called on every flip (READY->BLOCKED and BLOCKED->READY).
    """
    with _disk_gate_lock:
        if listener not in _disk_gate_listeners:
            _disk_gate_listeners.append(listener)


def reset_disk_gate_for_test() -> None:
    """Test-only: clear listeners and reset cached state."""
    global _disk_blocked, _disk_blocked_reason
    with _disk_gate_lock:
        _disk_blocked = False
        _disk_blocked_reason = ""
        _disk_gate_listeners.clear()


@router.get("/api/capabilities")
async def api_capabilities() -> dict[str, Any]:
    """Return capability readiness for all registered subsystems.

    Cached: refreshes at most once every 2 seconds. Returns::

        {
          "generated_at": "2026-04-23T12:00:00.000000Z",
          "capabilities": [
            {
              "name": "Detection",
              "status": "READY",
              "reasons": [],
              "fix_target": null
            },
            ...
          ]
        }
    """
    global _cached_result, _cache_expires_at

    now = time.monotonic()
    with _cache_lock:
        if _cached_result is not None and now < _cache_expires_at:
            return _cached_result

    result = _build_response()

    with _cache_lock:
        _cached_result = result
        _cache_expires_at = time.monotonic() + _CACHE_TTL_SEC

    return result
