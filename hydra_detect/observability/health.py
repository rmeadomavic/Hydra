"""Structured subsystem health snapshot powering ``GET /api/health``.

Each subsystem probe returns ``{"status": "ok"|"warn"|"fail", "detail": str}``.
Probes never raise — an exception during probe is caught and returned as
``fail`` with the exception class name in ``detail``.

Overall status is the worst of the subsystems: ``fail`` > ``warn`` > ``ok``.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

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
    if (fix is None or fix == 0) and mavlink_ref is not None:
        # Issue #302: the fix type lives in get_gps() (key "fix") — the old
        # fallback asked get_flight_data() for a "gps_fix" key it has never
        # returned, so this probe always degraded to "no gps data" whenever
        # the stats cache was empty. StreamState also DEFAULTS gps_fix to 0
        # before the pipeline publishes, so a 0 must consult the live
        # MAVLink cache too. The cache value is trusted only while FRESH
        # (GPS_RAW_INT seen within the last 10 s): a cache that latched
        # fix=3 and then stopped hearing GPS must not override an explicit
        # no-fix with a stale OK (2026-07-18 Codex re-review).
        getter = getattr(mavlink_ref, "get_gps", None)
        if callable(getter):
            data = getter()
            if isinstance(data, dict):
                raw_ts = data.get("raw_last_update") or 0.0
                fresh = raw_ts > 0.0 and (time.monotonic() - raw_ts) <= 10.0
                live = data.get("fix")
                if fresh and live is not None:
                    fix = live
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


# Partitions surfaced as numeric percentages in ``disk_free_pct``. Each entry
# is (label, default_path). Per-partition pct is in addition to the existing
# ``subsystems.disk`` status string — phone-home consumers want a number, not
# a sentence.
#
# The ``output_data`` default path is relative — when the configured path
# does not exist, ``_partition_usage`` now omits the label entirely (with a
# warning) instead of walking up to an ancestor partition. Issue #248: the
# old ancestor fallback masked mount failures (a missing /data mount would
# silently report /). Set ``HYDRA_OUTPUT_DATA_PATH`` in the unit/env to
# anchor the probe at an absolute mount point. See systemd/hydra-detect.service
# and adversarial finding R3-3 on #227.
_DEFAULT_OUTPUT_DATA_PATH = "./output_data"


def _default_partitions() -> tuple[tuple[str, str], ...]:
    """Return the default (label, path) tuple, honouring env overrides."""
    output_path = os.environ.get(
        "HYDRA_OUTPUT_DATA_PATH", _DEFAULT_OUTPUT_DATA_PATH,
    )
    return (
        ("root", "/"),
        ("output_data", output_path),
    )


# Static module-level snapshot so existing tests / call sites importing the
# tuple keep working. Computed once at import time; the env-aware variant
# above is consulted from compute_disk_free / compute_disk_bytes.
_DISK_FREE_PARTITIONS: tuple[tuple[str, str], ...] = _default_partitions()


def _partition_usage(path: str) -> Optional[tuple[float, int, int]]:
    """Return ``(free_pct, free_bytes, total_bytes)`` for ``path``, or None.

    Returns None when the path does not exist or ``shutil.disk_usage`` fails
    — the field is omitted in that case, not zeroed, so consumers can
    distinguish "really full" from "no data" AND so a missing mount surfaces
    as an absent label rather than being silently rewritten to an ancestor
    partition (which masked mount failures — see issue #248).

    Emits a single ``logger.warning`` when the probe fails, so an operator
    looking at "why did output_data disappear from phone-home" has something
    to grep for.
    """
    p = Path(path)
    if not p.exists():
        _log.warning(
            "disk_probe: path does not exist, omitting label path=%r", path,
        )
        return None
    try:
        usage = shutil.disk_usage(os.fspath(p))
    except OSError as exc:
        _log.warning(
            "disk_probe: shutil.disk_usage failed for path=%r err=%s",
            path, exc,
        )
        return None
    if usage.total <= 0:
        _log.warning(
            "disk_probe: total bytes <= 0 for path=%r", path,
        )
        return None
    pct = round((usage.free / usage.total) * 100.0, 2)
    return (pct, int(usage.free), int(usage.total))


def _partition_free_pct(path: str) -> Optional[float]:
    """Return free-space percentage for ``path``, or None if unreadable.

    Thin wrapper over :func:`_partition_usage` preserved for callers that
    only need the percentage. New code should prefer ``_partition_usage`` so
    it can also surface absolute free/total bytes (see #232).
    """
    info = _partition_usage(path)
    return None if info is None else info[0]


def compute_disk_free(
    partitions: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    """Return ``{label: free_pct}`` for the partitions Hydra cares about.

    Used by ``/api/health`` to surface a numeric ``disk_free_pct`` field
    additively alongside the existing ``subsystems.disk`` status string.

    Args:
        partitions: Optional override mapping ``{label: path}``. When None,
            uses ``_DISK_FREE_PARTITIONS`` defaults (root + output_data).

    Returns:
        Dict mapping label → percent free (0-100, 2 decimal places). Labels
        whose disk_usage call fails are omitted, not zeroed.
    """
    if partitions is None:
        items = _default_partitions()
    else:
        items = tuple(partitions.items())
    out: Dict[str, float] = {}
    for label, path in items:
        info = _partition_usage(path)
        if info is not None:
            out[label] = info[0]
    return out


def compute_disk_bytes(
    partitions: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, int]]:
    """Return ``{label: {"free": bytes, "total": bytes}}`` per partition.

    Sibling of :func:`compute_disk_free`. Percent-only telemetry can't tell
    a phone-home consumer if "5% free" means 200 GB on a 4 TB NVMe (plenty)
    or 1.6 GB on a 32 GB SD card (about to fill). Absolute byte counts let
    downstream Capability Status gates (#226) set platform-aware BLOCKED
    thresholds. (See adversarial finding R3-1 on #227.)

    The bytes and percent surfaces are computed from the same underlying
    ``shutil.disk_usage`` call and are guaranteed internally consistent
    within rounding (``disk_free_pct`` is rounded to 2 decimal places;
    ``disk_bytes`` carries integer bytes). At the rounding boundary the
    two may report slightly different sides of a threshold — consumers
    should pick one canonical surface for gating decisions and stick with
    it. (See adversarial finding R3-1 on PR #236.)

    Args:
        partitions: Optional override mapping ``{label: path}``. When None,
            uses ``_DISK_FREE_PARTITIONS`` defaults (root + output_data).

    Returns:
        Dict mapping label → ``{"free": int, "total": int}``. Labels whose
        ``disk_usage`` call fails are omitted, not zeroed.
    """
    if partitions is None:
        items = _default_partitions()
    else:
        items = tuple(partitions.items())
    out: Dict[str, Dict[str, int]] = {}
    for label, path in items:
        info = _partition_usage(path)
        if info is not None:
            _pct, free, total = info
            out[label] = {"free": free, "total": total}
    return out


def _alert_absent_partitions(
    expected: tuple[tuple[str, str], ...],
    present: Dict[str, Any],
) -> None:
    """Emit a structured warning for each expected partition label missing
    from the computed disk metrics.

    Issue #248 / PR #253 made ``_partition_usage`` omit a label (with a
    ``disk_probe`` warning) when its path does not exist, instead of walking
    up to an ancestor partition. That removed the silent-rewrite bug but left
    a downstream gap: when a configured mount (e.g. ``output_data``) drops
    mid-mission, its ``disk_free_pct`` series simply disappears and nothing
    flags it. ``_partition_usage`` warns from the probe's point of view ("a
    path was bad"); this function warns from the *config's* point of view
    ("a partition we were told to watch is gone"), which is the signal an
    operator actually alerts on.

    Every label in the configured/expected list is, by construction, a mount
    Hydra is told to watch — there is no "optional partition" marker in the
    data model, so a configured-but-absent label is always alert-worthy.
    Uses the same ``_log.warning`` structured-line mechanism the rest of
    this module uses; the observability package has no separate alert object.
    """
    for label, path in expected:
        if label not in present:
            _log.warning(
                "partition_absent: expected partition label missing from "
                "disk metrics — configured mount may have dropped "
                "label=%r path=%r",
                label, path,
            )


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
    disk_partitions: Optional[Dict[str, str]] = None,
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
          "disk_free_pct": {"root": 87.2, "output_data": 64.5},
          "disk_bytes":    {"root": {"free": 234..., "total": 480...},
                            "output_data": {"free": ..., "total": ...}},
        }

    The ``disk_free_pct`` field is additive — existing consumers reading
    ``subsystems.disk.status`` keep working unchanged. The sibling
    ``disk_bytes`` field (added in #232) carries absolute byte counts so
    consumers can compute platform-aware headroom rather than treating
    percentages from a 32 GB SD card and a 4 TB NVMe identically.
    Partition labels whose disk_usage call fails are omitted (not zeroed)
    in both maps so phone-home can distinguish "really full" from "no data".
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

    disk_free_pct = compute_disk_free(disk_partitions)
    disk_bytes = compute_disk_bytes(disk_partitions)

    # Consumer-side absent-partition detection (issue #248 follow-up). The
    # producers above omit a label whose path is unreadable; here — where the
    # configured/expected partition list is known — compare expected against
    # present and warn for any expected label that dropped out, so a mount
    # failure surfaces as an alert rather than a silently missing series.
    if disk_partitions is None:
        expected = _default_partitions()
    else:
        expected = tuple(disk_partitions.items())
    _alert_absent_partitions(expected, disk_free_pct)

    return {
        "status": _worst(subsystems),
        "ts": time.time(),
        "subsystems": subsystems,
        "disk_free_pct": disk_free_pct,
        "disk_bytes": disk_bytes,
    }
