"""Storage rotation — per-category retention and disk-free Capability Status gates.

Run via scripts/storage_rotation.py (dry-run by default).
Import disk_status() for Capability Status wiring (#146).
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maps logical category name → subdirectory under output_data/
CATEGORY_DIRS: dict[str, str] = {
    "detection_logs": "logs",
    "mission_bundles": "missions",
    "video_crops": "crops",
    "tak_audit": "tak",
    "feedback_crops": "feedback",
}

# Key in [storage] section that holds retention days per category
RETENTION_KEYS: dict[str, str] = {
    "detection_logs": "retention_detection_logs_days",
    "mission_bundles": "retention_mission_bundles_days",
    "video_crops": "retention_video_crops_days",
    "tak_audit": "retention_tak_audit_days",
    "feedback_crops": "retention_feedback_crops_days",
}

# Name written to the audit log
AUDIT_LOG_FILENAME = "storage_rotation.log"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class CategoryPlan:
    """Files scheduled for deletion in one category."""
    category: str
    subdir: str
    files: list[Path] = field(default_factory=list)
    total_bytes: int = 0

    def add(self, path: Path) -> None:
        self.files.append(path)
        try:
            self.total_bytes += path.stat().st_size
        except OSError:
            pass


@dataclass
class CleanupPlan:
    """What would be deleted — returned by plan_cleanup()."""
    categories: dict[str, CategoryPlan] = field(default_factory=dict)
    skipped_floor: int = 0       # files skipped because younger than floor
    skipped_traversal: int = 0   # files skipped because path escaped output_data/

    @property
    def total_files(self) -> int:
        return sum(len(cp.files) for cp in self.categories.values())

    @property
    def total_bytes(self) -> int:
        return sum(cp.total_bytes for cp in self.categories.values())


@dataclass
class CategoryResult:
    """Outcome of deleting one category's files."""
    category: str
    removed: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class CleanupResult:
    """Outcome returned by execute_cleanup()."""
    categories: dict[str, CategoryResult] = field(default_factory=dict)
    audit_path: Path | None = None

    @property
    def total_removed(self) -> int:
        return sum(r.removed for r in self.categories.values())

    @property
    def total_bytes_freed(self) -> int:
        return sum(r.bytes_freed for r in self.categories.values())

    @property
    def total_errors(self) -> int:
        return sum(len(r.errors) for r in self.categories.values())


class DiskStatus(NamedTuple):
    """Result of disk_status()."""
    status: str   # "READY" | "WARN" | "BLOCKED"
    reason: str


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_storage_int(cfg: configparser.ConfigParser, key: str, default: int) -> int:
    """Read an int from [storage], falling back to default."""
    try:
        return cfg.getint("storage", key, fallback=default)
    except (ValueError, configparser.Error):
        return default


def _clamp_retention(value: int, floor: int, ceiling: int, key: str) -> int:
    """Clamp a retention value to [floor, ceiling], logging a warning if clamped."""
    if value < floor:
        logger.warning(
            "Config: %s = %d is below floor (%d). Clamped to %d.",
            key, value, floor, floor,
        )
        return floor
    if value > ceiling:
        logger.warning(
            "Config: %s = %d exceeds ceiling (%d). Clamped to %d.",
            key, value, ceiling, ceiling,
        )
        return ceiling
    return value


def _effective_floor(cfg: configparser.ConfigParser) -> int:
    """Read retention_floor_days from config, enforce minimum of 1.

    Operators can set floor to 0 in config to bypass the safety belt entirely;
    clamp to 1 so the belt always holds.
    """
    floor = _get_storage_int(cfg, "retention_floor_days", 7)
    if floor < 1:
        logger.warning(
            "Config: retention_floor_days = %d below minimum of 1. Clamped to 1.",
            floor,
        )
        floor = 1
    return floor


def _resolve_retention(cfg: configparser.ConfigParser) -> dict[str, int]:
    """Return clamped retention days per category."""
    floor = _effective_floor(cfg)
    ceiling = _get_storage_int(cfg, "retention_ceiling_days", 730)

    if floor > ceiling:
        logger.warning(
            "Config: retention_floor_days (%d) > retention_ceiling_days (%d). "
            "Raising ceiling to floor to keep retention bounded.",
            floor, ceiling,
        )
        ceiling = floor

    defaults = {
        "detection_logs": 365,
        "mission_bundles": 90,
        "video_crops": 30,
        "tak_audit": 90,
        "feedback_crops": 90,
    }

    result: dict[str, int] = {}
    for category, ini_key in RETENTION_KEYS.items():
        raw = _get_storage_int(cfg, ini_key, defaults[category])
        result[category] = _clamp_retention(raw, floor, ceiling, ini_key)
    return result


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _is_safe_path(path: Path, root: Path) -> bool:
    """Return True if path is strictly inside root (no traversal escape).

    Resolves symlinks on both sides. A symlink inside output_data/ that
    points outside is blocked.
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
        return True
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Core: plan_cleanup (pure, no side effects)
# ---------------------------------------------------------------------------

def plan_cleanup(
    cfg: configparser.ConfigParser,
    root: Path,
    now: datetime,
) -> CleanupPlan:
    """Return what WOULD be deleted — no files touched.

    Args:
        cfg:  Parsed config.ini (must have [storage] section or fall back to defaults).
        root: Path to output_data/ directory.
        now:  Reference time (pass datetime.now(timezone.utc) in production).

    Returns:
        CleanupPlan with files grouped by category and safety skip counts.
    """
    plan = CleanupPlan()

    # Clock-forward guard. If `now` is significantly ahead of wall time
    # (NTP overcorrect after power loss, RTC battery dead, etc.) then
    # floor_cutoff is also future-large and the floor check passes for
    # every real file. Refuse to plan in that case. One bad clock jump
    # would otherwise delete the entire mission archive in one run.
    wall_now = datetime.now(timezone.utc)
    now_compare = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    if now_compare > wall_now + timedelta(minutes=5):
        logger.error(
            "Storage rotation: clock-forward guard tripped. "
            "now=%s exceeds wall=%s by >5 minutes. Refusing to plan cleanup.",
            now_compare.isoformat(), wall_now.isoformat(),
        )
        return plan

    floor_days = _effective_floor(cfg)
    retention = _resolve_retention(cfg)

    for category, subdir_name in CATEGORY_DIRS.items():
        days = retention[category]
        cutoff = now.timestamp() - days * 86400
        floor_cutoff = now.timestamp() - floor_days * 86400

        subdir = root / subdir_name
        if not subdir.exists():
            continue

        cat_plan = CategoryPlan(category=category, subdir=subdir_name)

        try:
            candidates = list(subdir.rglob("*"))
        except OSError as exc:
            logger.warning("Cannot list %s: %s", subdir, exc)
            continue

        for path in candidates:
            if not path.is_file():
                continue

            # Path traversal guard: block anything that resolves outside root.
            if not _is_safe_path(path, root):
                logger.warning(
                    "Path traversal blocked: %s resolves outside output_data/. Skipped.",
                    path,
                )
                plan.skipped_traversal += 1
                continue

            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue

            # Safety belt: never delete younger than floor, regardless of config.
            if mtime >= floor_cutoff:
                plan.skipped_floor += 1
                continue

            # Delete if older than the configured retention window.
            if mtime < cutoff:
                cat_plan.add(path)

        if cat_plan.files:
            plan.categories[category] = cat_plan

    return plan


# ---------------------------------------------------------------------------
# Core: execute_cleanup
# ---------------------------------------------------------------------------

def execute_cleanup(plan: CleanupPlan, audit_root: Path) -> CleanupResult:
    """Delete the files in plan. Logs every removal. Writes JSONL audit entry.

    Never raises — a deletion error is recorded in the result, not re-raised.

    Args:
        plan:       CleanupPlan from plan_cleanup().
        audit_root: Directory that holds storage_rotation.log (output_data/).

    Returns:
        CleanupResult with per-category counts and the audit log path.
    """
    started_at = datetime.now(timezone.utc)
    result = CleanupResult()

    for category, cat_plan in plan.categories.items():
        cat_result = CategoryResult(category=category)

        for path in cat_plan.files:
            try:
                size = path.stat().st_size
                path.unlink()
                cat_result.removed += 1
                cat_result.bytes_freed += size
                logger.debug("Removed %s (%d bytes).", path.name, size)
            except OSError as exc:
                msg = f"{path}: {exc}"
                cat_result.errors.append(msg)
                logger.warning("Could not remove %s: %s", path, exc)

        result.categories[category] = cat_result

    # Summary log line.
    total = result.total_removed
    freed_mb = result.total_bytes_freed / (1024 * 1024)
    if result.total_errors:
        logger.warning(
            "Cleanup partial. %d files removed (%.1f MB). %d errors. See audit log.",
            total, freed_mb, result.total_errors,
        )
    else:
        if total:
            logger.info(
                "Removed %d files (%.1f MB). Oldest retained: see audit log.",
                total, freed_mb,
            )
        else:
            logger.info("Cleanup complete. 0 files removed.")

    # Write JSONL audit entry.
    result.audit_path = _write_audit_entry(
        audit_root=audit_root,
        started_at=started_at,
        plan=plan,
        result=result,
    )

    return result


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def _write_audit_entry(
    audit_root: Path,
    started_at: datetime,
    plan: CleanupPlan,
    result: CleanupResult,
) -> Path | None:
    """Append one JSONL line to output_data/storage_rotation.log."""
    audit_path = audit_root / AUDIT_LOG_FILENAME

    entry: dict = {
        "ts": started_at.isoformat(),
        "total_files_planned": plan.total_files,
        "total_files_removed": result.total_removed,
        "total_bytes_freed": result.total_bytes_freed,
        "skipped_floor": plan.skipped_floor,
        "skipped_traversal": plan.skipped_traversal,
        "errors": result.total_errors,
        "categories": {},
    }

    for category, cat_result in result.categories.items():
        entry["categories"][category] = {
            "removed": cat_result.removed,
            "bytes_freed": cat_result.bytes_freed,
            "errors": cat_result.errors,
        }

    try:
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return audit_path
    except OSError as exc:
        logger.warning("Could not write audit log at %s: %s", audit_path, exc)
        return None


# ---------------------------------------------------------------------------
# Disk status gate
# ---------------------------------------------------------------------------

def disk_status(cfg: configparser.ConfigParser, root: Path) -> DiskStatus:
    """Return disk free status for Capability Status gates.

    Args:
        cfg:  Parsed config (reads [storage] disk_warn_pct / disk_block_pct).
        root: Path whose filesystem usage is measured (output_data/).

    Returns:
        DiskStatus(status, reason) where status is "READY", "WARN", or "BLOCKED".
    """
    warn_pct = _get_storage_int(cfg, "disk_warn_pct", 15)
    block_pct = _get_storage_int(cfg, "disk_block_pct", 5)

    try:
        usage = shutil.disk_usage(root)
        free_pct = (usage.free / usage.total) * 100.0
    except OSError as exc:
        return DiskStatus(
            status="WARN",
            reason=f"Cannot read disk usage at {root}: {exc}",
        )

    free_gb = usage.free / (1024 ** 3)

    if free_pct <= block_pct:
        return DiskStatus(
            status="BLOCKED",
            reason=(
                f"Disk {free_pct:.1f}% free ({free_gb:.2f} GB). "
                f"Below block threshold ({block_pct}%). "
                "Run storage rotation to free space."
            ),
        )
    if free_pct <= warn_pct:
        return DiskStatus(
            status="WARN",
            reason=(
                f"Disk {free_pct:.1f}% free ({free_gb:.2f} GB). "
                f"Below warn threshold ({warn_pct}%). "
                "Consider running storage rotation."
            ),
        )
    return DiskStatus(
        status="READY",
        reason=f"Disk {free_pct:.1f}% free ({free_gb:.2f} GB).",
    )


# ---------------------------------------------------------------------------
# Boot-time BLOCKED check
# ---------------------------------------------------------------------------

def check_disk_at_boot(
    cfg: configparser.ConfigParser,
    root: Path,
) -> bool:
    """Check disk status at boot. Returns True if BLOCKED, False otherwise.

    Logs a loud WARNING if BLOCKED. Callers can use the return value as a
    signal to pause recording or surface a Capability Status alert.
    Does NOT modify pipeline state — that's the caller's job.
    """
    status, reason = disk_status(cfg, root)
    if status == "BLOCKED":
        logger.warning(
            "STORAGE BLOCKED. %s Hydra will not record until space is freed. "
            "Run: python scripts/storage_rotation.py --apply",
            reason,
        )
        return True
    if status == "WARN":
        logger.warning("STORAGE WARN. %s", reason)
    return False
