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


def wire_components(
    stream_state: Any | None = None,
    mavlink_ref: Any | None = None,
    tak_output_ref: Any | None = None,
    tak_input_ref: Any | None = None,
    cfg: Any | None = None,
) -> None:
    """Connect live Hydra component references so evaluators read real signals.

    Called once from server.py after the pipeline is wired. Safe to call
    multiple times — later calls overwrite earlier ones.
    """
    global _stream_state_ref, _mavlink_ref, _tak_output_ref, _tak_input_ref, _cfg_ref
    _stream_state_ref = stream_state
    _mavlink_ref = mavlink_ref
    _tak_output_ref = tak_output_ref
    _tak_input_ref = tak_input_ref
    _cfg_ref = cfg


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
    state: SystemState = build_system_state(
        stream_state=_stream_state_ref,
        mavlink_ref=_mavlink_ref,
        tak_output_ref=_tak_output_ref,
        tak_input_ref=_tak_input_ref,
        cfg=_cfg_ref,
    )
    reports = evaluate_all(state)
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "capabilities": [_serialize_report(r) for r in reports],
    }


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
