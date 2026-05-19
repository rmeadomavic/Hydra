"""Per-mission statistics aggregation for the /api/summary endpoint (#72).

Scans detection JSONL files and event JSONL files in the log directory,
groups by ``mission_id`` (stamped by ``DetectionLogger`` and ``EventLogger``
respectively), and returns aggregate stats: detections by class, unique
tracks, time to first detection, vehicle-track GPS coverage area.

Results are cached for ``_SUMMARY_TTL_SEC`` to bound per-request I/O on
large log directories.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .review_export import gps_coverage

logger = logging.getLogger(__name__)

# How long to cache a per-mission summary in memory. The active mission's
# JSONL is still appending while polling, so a short TTL is the tradeoff
# between cost and freshness. 30 s is comfortably below typical "review
# tab open + tab away + tab back" cadence.
_SUMMARY_TTL_SEC = 30.0


@dataclass
class _CacheEntry:
    summary: dict
    written_at: float
    inputs_signature: tuple  # (det_mtime_sum, evt_mtime_sum, det_count, evt_count)


_cache: dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()


def _scan_log_dir(log_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return ``(detection_files, event_files)`` under ``log_dir``.

    Detection files: ``detections_*.jsonl`` and ``detections_*.csv``.
    Event files: any JSONL whose first record has a known event ``type``.

    Returns empty lists if ``log_dir`` does not exist.
    """
    if not log_dir.is_dir():
        return [], []
    det_files: list[Path] = []
    evt_files: list[Path] = []
    for f in log_dir.iterdir():
        if not f.is_file():
            continue
        if f.name.startswith("detections_") and f.suffix in (".jsonl", ".csv"):
            det_files.append(f)
            continue
        if f.suffix != ".jsonl":
            continue
        try:
            with f.open() as fh:
                first = fh.readline().strip()
            if not first:
                continue
            rec = json.loads(first)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if rec.get("type") in ("mission_start", "track", "action", "state", "detection"):
            evt_files.append(f)
    return det_files, evt_files


def _iter_jsonl(path: Path):
    """Yield JSON records from a JSONL file, skipping malformed lines."""
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("Skipping unreadable log file %s: %s", path, exc)


def _iter_detections(path: Path):
    """Yield detection rows from a JSONL or CSV detection log."""
    if path.suffix == ".csv":
        import csv
        try:
            with path.open() as f:
                reader = csv.DictReader(f)
                yield from reader
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Skipping CSV %s: %s", path, exc)
        return
    yield from _iter_jsonl(path)


def _inputs_signature(det_files: list[Path], evt_files: list[Path]) -> tuple:
    """Stable signature of (file count + mtime sum) so cache invalidates
    when a log file rotates or grows. Cheap stat-only — no reads."""
    def _sig(files: list[Path]) -> tuple:
        mt = 0.0
        sz = 0
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            mt += st.st_mtime
            sz += st.st_size
        return (len(files), mt, sz)

    return _sig(det_files) + _sig(evt_files)


def _parse_ts(ts_value: Any) -> float | None:
    """Best-effort parse of a detection timestamp into a float epoch."""
    if isinstance(ts_value, (int, float)):
        return float(ts_value)
    if isinstance(ts_value, str):
        try:
            # ISO 8601 with optional trailing Z.
            from datetime import datetime
            s = ts_value.rstrip("Z")
            return datetime.fromisoformat(s).timestamp()
        except (ValueError, TypeError):
            return None
    return None


def compute_summary(mission_id: str, log_dir: Path) -> dict:
    """Compute the summary for one mission. Bypasses the cache."""
    det_files, evt_files = _scan_log_dir(log_dir)

    by_class: dict[str, int] = {}
    track_ids: set[Any] = set()
    det_count = 0
    earliest_det_ts: float | None = None
    unparseable_ts_count = 0
    mission_start_ts: float | None = None
    mission_end_ts: float | None = None
    action_count = 0
    state_count = 0
    track_points: list[tuple[float, float]] = []

    # Detection rows
    for path in det_files:
        for row in _iter_detections(path):
            if str(row.get("mission_id") or "") != mission_id:
                continue
            det_count += 1
            label = row.get("label") or "unknown"
            by_class[label] = by_class.get(label, 0) + 1
            tid = row.get("track_id")
            if tid is not None and tid != "":
                track_ids.add(tid)
            ts_parsed = _parse_ts(row.get("timestamp"))
            if ts_parsed is None:
                # Track rows we could not parse so the operator can tell
                # "unknown — N rows had bad timestamps" apart from
                # "no detections at all" (R2-2 in docs/adversarial/230.md).
                unparseable_ts_count += 1
                continue
            if earliest_det_ts is None or ts_parsed < earliest_det_ts:
                earliest_det_ts = ts_parsed

    # Event rows (mission lifecycle + vehicle telemetry + operator actions)
    for path in evt_files:
        for rec in _iter_jsonl(path):
            if str(rec.get("mission_id") or "") != mission_id:
                continue
            rtype = rec.get("type")
            ts = rec.get("ts")
            if rtype == "mission_start":
                mission_start_ts = ts if isinstance(ts, (int, float)) else mission_start_ts
            elif rtype == "mission_end":
                mission_end_ts = ts if isinstance(ts, (int, float)) else mission_end_ts
            elif rtype == "track":
                lat = rec.get("lat")
                lon = rec.get("lon")
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                    track_points.append((float(lat), float(lon)))
            elif rtype == "action":
                action_count += 1
            elif rtype == "state":
                state_count += 1

    # R2-2: surface why time_to_first_detection_sec might be null so the
    # operator can distinguish "no detections" from "had detections but
    # all timestamps unparseable" from "everything fine, here's the value."
    time_to_first_detection_sec: float | None = None
    if det_count == 0:
        ttfd_status = "no_detections"
    elif earliest_det_ts is None:
        # We had detections but every timestamp failed to parse.
        ttfd_status = "unknown"
    elif mission_start_ts is None:
        # Detections present, mission_start_ts missing — can't compute delta.
        ttfd_status = "unknown"
    else:
        delta = earliest_det_ts - mission_start_ts
        if delta >= 0:
            time_to_first_detection_sec = round(delta, 3)
            ttfd_status = "known"
        else:
            # Earliest detection precedes mission_start — clock skew or
            # leaked rows from before start_mission(). Treat as unknown.
            ttfd_status = "unknown"

    duration_sec: float | None = None
    if mission_start_ts is not None:
        end = mission_end_ts if mission_end_ts is not None else time.time()
        duration_sec = round(end - mission_start_ts, 3)

    coverage = gps_coverage(track_points)

    return {
        "mission_id": mission_id,
        "detections": {
            "total": det_count,
            "by_class": by_class,
            "unique_tracks": len(track_ids),
            "unparseable_timestamp_count": unparseable_ts_count,
        },
        "time_to_first_detection_sec": time_to_first_detection_sec,
        "time_to_first_detection_status": ttfd_status,
        "mission_start_ts": mission_start_ts,
        "mission_end_ts": mission_end_ts,
        "duration_sec": duration_sec,
        "operator_actions": action_count,
        "state_changes": state_count,
        "gps_coverage": coverage,
    }


def get_summary(mission_id: str, log_dir: Path | str) -> dict:
    """Return cached or freshly-computed summary for ``mission_id``."""
    if not isinstance(mission_id, str) or not mission_id:
        raise ValueError("mission_id must be a non-empty string")

    log_path = Path(log_dir)
    det_files, evt_files = _scan_log_dir(log_path)
    signature = _inputs_signature(det_files, evt_files)
    now = time.monotonic()

    with _cache_lock:
        entry = _cache.get(mission_id)
        if (
            entry is not None
            and entry.inputs_signature == signature
            and (now - entry.written_at) < _SUMMARY_TTL_SEC
        ):
            return entry.summary

    summary = compute_summary(mission_id, log_path)

    with _cache_lock:
        _cache[mission_id] = _CacheEntry(
            summary=summary,
            written_at=now,
            inputs_signature=signature,
        )
        # Bound the cache — 64 distinct missions is more than enough for
        # a single Jetson session; oldest entries are evicted.
        if len(_cache) > 64:
            oldest = min(_cache.items(), key=lambda kv: kv[1].written_at)[0]
            _cache.pop(oldest, None)
    return summary


def list_missions(log_dir: Path | str) -> list[dict]:
    """Return a list of mission summaries (id, name, start, end, log file).

    Scans event timeline JSONLs in ``log_dir`` and pulls the
    ``mission_start`` record from each. Useful for the review tab's
    mission picker.
    """
    log_path = Path(log_dir)
    _, evt_files = _scan_log_dir(log_path)
    out: list[dict] = []
    for path in evt_files:
        start: dict | None = None
        end: dict | None = None
        for rec in _iter_jsonl(path):
            if rec.get("type") == "mission_start" and start is None:
                start = rec
            elif rec.get("type") == "mission_end":
                end = rec
        if start is None or not start.get("mission_id"):
            continue
        out.append({
            "mission_id": start.get("mission_id"),
            "name": start.get("name"),
            "callsign": start.get("callsign"),
            "started_ts": start.get("ts"),
            "ended_ts": end.get("ts") if end else None,
            "log_file": path.name,
        })
    out.sort(key=lambda m: m.get("started_ts") or 0.0, reverse=True)
    return out


def clear_cache() -> None:
    """Drop all cached summaries (tests + after a manual log purge)."""
    with _cache_lock:
        _cache.clear()


def invalidate_for_log_dir(log_dir: Path | str) -> int:
    """Drop every cached summary so the next ``/api/summary`` re-reads disk.

    Called from ``DetectionLogger._prune_old_logs`` after it deletes one or
    more rotated JSONL files (R1-1 in docs/adversarial/230.md). The signature
    cache is keyed on file count + mtime sum + size sum, all of which can
    coincidentally line up across a delete (deleted file no longer contributes
    to any sum, so the new signature can match a stale cache entry for the
    truncated dataset). Explicit invalidation is the only safe answer.

    We invalidate the entire cache rather than per-mission because the prune
    operation does not know which mission_ids the deleted file contributed
    to without re-scanning — and re-scanning is exactly what we want to
    force on the next call.

    Args:
        log_dir: The log directory whose contents just shifted. Currently
            unused (we drop everything), but kept in the signature so a
            future per-directory cache can scope invalidation properly.

    Returns:
        Number of cache entries dropped (for callers that want to log it).
    """
    del log_dir  # Reserved for future per-directory cache scoping.
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n
