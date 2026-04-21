"""Structured subsystem health snapshot powering ``GET /api/health``.

Each subsystem probe returns ``{"status": "ok"|"warn"|"fail", "detail": str}``.
Probes never raise — an exception during probe is caught and returned as
``fail`` with the exception class name in ``detail``.

Overall status is the worst of the subsystems: ``fail`` > ``warn`` > ``ok``.
"""

from __future__ import annotations

import shutil
import time
from typing import Any, Dict, Optional

# Subsystems listed in the response, in display order. Dashboards iterate
# this to render a per-subsystem light.
SUBSYSTEMS = (
    "camera",
    "mavlink",
    "gps",
    "detector",
    "rtsp",
    "tak",
    "audit",
    "disk",
)

_STATUS_RANK = {"ok": 0, "warn": 1, "fail": 2}

# Disk-free thresholds (bytes). Below warn → warn; below fail → fail.
_DISK_WARN_BYTES = 1 * 1024 * 1024 * 1024   # 1 GB
_DISK_FAIL_BYTES = 100 * 1024 * 1024        # 100 MB


def _ok(detail: str = "") -> Dict[str, str]:
    return {"status": "ok", "detail": detail}


def _warn(detail: str) -> Dict[str, str]:
    return {"status": "warn", "detail": detail}


def _fail(detail: str) -> Dict[str, str]:
    return {"status": "fail", "detail": detail}


def _safe_probe(fn, ref: Any) -> Dict[str, str]:
    """Run a probe callable; convert any raised exception into ``fail``."""
    try:
        return fn(ref)
    except Exception as exc:  # pragma: no cover — defensive
        return _fail(f"{type(exc).__name__}: {exc}")


def _probe_camera(stats: Dict[str, Any]) -> Dict[str, str]:
    if not stats:
        return _warn("stats unavailable")
    cam_ok = stats.get("camera_ok")
    fps = stats.get("fps") or 0.0
    if cam_ok is False:
        return _fail("camera_ok=false")
    if fps <= 0:
        return _warn("fps=0 (pipeline not producing frames)")
    return _ok(f"fps={fps:.1f}")


def _probe_detector(stats: Dict[str, Any]) -> Dict[str, str]:
    if not stats:
        return _warn("stats unavailable")
    name = stats.get("detector", "n/a")
    fps = stats.get("fps") or 0.0
    if not name or name == "n/a":
        return _warn("detector=n/a")
    if fps <= 0:
        return _warn(f"detector={name} but fps=0")
    return _ok(f"detector={name} fps={fps:.1f}")


def _probe_mavlink(mavlink_ref: Any) -> Dict[str, str]:
    if mavlink_ref is None:
        return _warn("mavlink not registered")
    connected = getattr(mavlink_ref, "connected", None)
    if connected is False:
        return _warn("mavlink disconnected")
    if connected is True:
        return _ok("connected")
    # Some facades don't expose .connected — treat as warn, don't fail.
    return _warn("mavlink connection state unknown")


def _probe_gps(mavlink_ref: Any, stats: Dict[str, Any]) -> Dict[str, str]:
    fix = None
    if stats:
        fix = stats.get("gps_fix")
    if fix is None and mavlink_ref is not None:
        getter = getattr(mavlink_ref, "get_flight_data", None)
        if callable(getter):
            data = getter()
            if isinstance(data, dict):
                fix = data.get("gps_fix")
    if fix is None:
        return _warn("no gps data")
    try:
        fix_i = int(fix)
    except (TypeError, ValueError):
        return _warn(f"gps_fix={fix!r}")
    if fix_i >= 3:
        return _ok(f"gps_fix={fix_i}")
    if fix_i == 2:
        return _warn("gps_fix=2 (2D only)")
    return _warn(f"gps_fix={fix_i} (no fix)")


def _probe_rtsp(stats: Dict[str, Any]) -> Dict[str, str]:
    if not stats:
        return _warn("stats unavailable")
    running = stats.get("rtsp_running")
    if running is None:
        return _warn("rtsp not reported")
    if running:
        return _ok("rtsp running")
    return _warn("rtsp not running")


def _probe_tak(tak_output_ref: Any) -> Dict[str, str]:
    if tak_output_ref is None:
        return _warn("tak not registered")
    # Touch a cheap attribute to confirm the ref is live.
    _ = getattr(tak_output_ref, "enabled", None)
    return _ok("tak output registered")


def _probe_audit(audit_sink: Any) -> Dict[str, str]:
    if audit_sink is None:
        return _warn("audit sink missing")
    try:
        n = len(audit_sink)
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
    return _ok(f"audit events={n}")


def _probe_disk(path: str = "/") -> Dict[str, str]:
    try:
        usage = shutil.disk_usage(path)
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
    free = usage.free
    if free < _DISK_FAIL_BYTES:
        return _fail(f"disk free {free // (1024 * 1024)} MB < 100 MB")
    if free < _DISK_WARN_BYTES:
        return _warn(f"disk free {free // (1024 * 1024)} MB < 1 GB")
    return _ok(f"disk free {free // (1024 * 1024)} MB")


def _worst(items: Dict[str, Dict[str, str]]) -> str:
    worst_rank = 0
    for v in items.values():
        r = _STATUS_RANK.get(v.get("status"), 1)
        if r > worst_rank:
            worst_rank = r
    for name, rank in _STATUS_RANK.items():
        if rank == worst_rank:
            return name
    return "ok"


def health_snapshot(
    *,
    stats: Optional[Dict[str, Any]] = None,
    mavlink_ref: Any = None,
    tak_output_ref: Any = None,
    audit_sink: Any = None,
    disk_path: str = "/",
) -> Dict[str, Any]:
    """Return the ``/api/health`` response body.

    Shape::

        {
          "status": "ok"|"warn"|"fail",
          "ts": <unix timestamp>,
          "subsystems": {
             "camera":   {"status": ..., "detail": ...},
             "mavlink":  {...},
             "gps":      {...},
             "detector": {...},
             "rtsp":     {...},
             "tak":      {...},
             "audit":    {...},
             "disk":     {...},
          },
        }
    """
    stats = stats or {}
    subsystems: Dict[str, Dict[str, str]] = {}
    subsystems["camera"] = _safe_probe(_probe_camera, stats)
    subsystems["mavlink"] = _safe_probe(_probe_mavlink, mavlink_ref)
    subsystems["gps"] = _safe_probe(lambda _s: _probe_gps(mavlink_ref, stats), None)
    subsystems["detector"] = _safe_probe(_probe_detector, stats)
    subsystems["rtsp"] = _safe_probe(_probe_rtsp, stats)
    subsystems["tak"] = _safe_probe(_probe_tak, tak_output_ref)
    subsystems["audit"] = _safe_probe(_probe_audit, audit_sink)
    subsystems["disk"] = _safe_probe(lambda _r: _probe_disk(disk_path), None)

    return {
        "status": _worst(subsystems),
        "ts": time.time(),
        "subsystems": subsystems,
    }
