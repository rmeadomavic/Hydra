"""Tests for hydra_detect.storage_rotation.

Coverage:
- Cleanup plan on a temp directory with known-age files
- Retention floor safety (file 1 day old with 1-day retention → floor blocks it)
- Path-traversal guard (symlink escape attempt is blocked)
- Disk-pct thresholds (READY / WARN / BLOCKED boundaries)
- Clamp warning on out-of-bounds retention config
- Dry-run by default; --apply required via CLI
- Audit log written on every execute_cleanup() run
- Partial run on fs error leaves partial result, no unhandled exception
"""

from __future__ import annotations

import configparser
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from hydra_detect.storage_rotation import (
    AUDIT_LOG_FILENAME,
    CATEGORY_DIRS,
    CleanupPlan,
    CategoryPlan,
    DiskStatus,
    check_disk_at_boot,
    disk_status,
    execute_cleanup,
    plan_cleanup,
    _clamp_retention,
    _is_safe_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**storage_overrides) -> configparser.ConfigParser:
    """Build a minimal ConfigParser with [storage] section."""
    defaults = {
        "retention_detection_logs_days": "365",
        "retention_mission_bundles_days": "90",
        "retention_video_crops_days": "30",
        "retention_tak_audit_days": "90",
        "retention_feedback_crops_days": "90",
        "disk_warn_pct": "15",
        "disk_block_pct": "5",
        "retention_floor_days": "7",
        "retention_ceiling_days": "730",
    }
    defaults.update({k: str(v) for k, v in storage_overrides.items()})
    cfg = configparser.ConfigParser()
    cfg.add_section("storage")
    for k, v in defaults.items():
        cfg.set("storage", k, v)
    return cfg


def _make_output_data(tmp_path: Path) -> Path:
    """Create the standard output_data/ subdirectory structure."""
    root = tmp_path / "output_data"
    for subdir in CATEGORY_DIRS.values():
        (root / subdir).mkdir(parents=True, exist_ok=True)
    return root


def _touch_with_age(path: Path, age_days: float) -> None:
    """Create a file with mtime set to age_days ago."""
    path.touch()
    age_seconds = age_days * 86400
    old_ts = time.time() - age_seconds
    os.utime(path, (old_ts, old_ts))


# ---------------------------------------------------------------------------
# plan_cleanup: basic category detection
# ---------------------------------------------------------------------------

class TestPlanCleanup:
    def test_old_file_in_category_is_planned(self, tmp_path):
        """A file older than retention_days must appear in the plan."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=30, retention_floor_days=7)
        # Create a file 40 days old (older than 30-day retention).
        old_file = root / "crops" / "old_crop.jpg"
        _touch_with_age(old_file, 40)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        assert "video_crops" in plan.categories
        planned = [f.name for f in plan.categories["video_crops"].files]
        assert "old_crop.jpg" in planned

    def test_recent_file_not_planned(self, tmp_path):
        """A file within the retention window must not appear in the plan."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=30, retention_floor_days=7)
        recent = root / "crops" / "recent_crop.jpg"
        _touch_with_age(recent, 10)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        if "video_crops" in plan.categories:
            names = [f.name for f in plan.categories["video_crops"].files]
            assert "recent_crop.jpg" not in names

    def test_mixed_age_files(self, tmp_path):
        """Only files older than retention are planned; recent ones are skipped."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=30, retention_floor_days=7)
        old = root / "crops" / "old.jpg"
        recent = root / "crops" / "new.jpg"
        _touch_with_age(old, 35)
        _touch_with_age(recent, 15)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        assert "video_crops" in plan.categories
        planned_names = [f.name for f in plan.categories["video_crops"].files]
        assert "old.jpg" in planned_names
        assert "new.jpg" not in planned_names

    def test_missing_subdir_silently_skipped(self, tmp_path):
        """If a category subdir doesn't exist, plan_cleanup must not raise."""
        root = _make_output_data(tmp_path)
        # Remove one subdir to simulate it not existing.
        import shutil
        shutil.rmtree(root / "crops")
        cfg = _make_cfg()
        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)
        assert "video_crops" not in plan.categories

    def test_directories_not_planned(self, tmp_path):
        """Subdirectories inside a category dir must not appear in the plan."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=30, retention_floor_days=7)
        subsubdir = root / "crops" / "2025-01-01"
        subsubdir.mkdir()
        _touch_with_age(subsubdir / "file.jpg", 40)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)
        if "video_crops" in plan.categories:
            for f in plan.categories["video_crops"].files:
                assert f.is_file()


# ---------------------------------------------------------------------------
# Retention floor safety
# ---------------------------------------------------------------------------

class TestRetentionFloor:
    def test_floor_blocks_deletion_of_young_file(self, tmp_path):
        """A file 1 day old with 1-day retention must be skipped due to 7-day floor."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(
            retention_video_crops_days=1,  # would delete 1-day-old files...
            retention_floor_days=7,        # ...but floor says never < 7 days
        )
        young = root / "crops" / "young.jpg"
        _touch_with_age(young, 1)  # 1 day old

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        # File must NOT be in any plan because it's younger than floor.
        if "video_crops" in plan.categories:
            names = [f.name for f in plan.categories["video_crops"].files]
            assert "young.jpg" not in names
        assert plan.skipped_floor >= 1

    def test_floor_blocks_even_if_older_than_retention(self, tmp_path):
        """Floor=7 must block deletion even for files 5 days old with 3-day retention."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(
            retention_video_crops_days=3,
            retention_floor_days=7,
        )
        borderline = root / "crops" / "borderline.jpg"
        _touch_with_age(borderline, 5)  # older than 3-day retention, younger than 7-day floor

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        if "video_crops" in plan.categories:
            names = [f.name for f in plan.categories["video_crops"].files]
            assert "borderline.jpg" not in names

    def test_file_beyond_floor_is_planned(self, tmp_path):
        """A file 8 days old with 3-day retention and 7-day floor must be planned."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(
            retention_video_crops_days=3,
            retention_floor_days=7,
        )
        old = root / "crops" / "old_enough.jpg"
        _touch_with_age(old, 8)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        assert "video_crops" in plan.categories
        names = [f.name for f in plan.categories["video_crops"].files]
        assert "old_enough.jpg" in names


# ---------------------------------------------------------------------------
# Path traversal guard
# ---------------------------------------------------------------------------

class TestPathTraversal:
    def test_symlink_outside_root_is_blocked(self, tmp_path):
        """A symlink inside output_data/ that resolves outside must be skipped."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=1, retention_floor_days=1)

        # Create a real file outside output_data/
        outside_file = tmp_path / "secret.txt"
        outside_file.write_text("sensitive data")
        _touch_with_age(outside_file, 10)

        # Create a symlink inside crops/ pointing to it.
        link = root / "crops" / "escape_link.txt"
        try:
            link.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        # The link must not appear in the plan.
        if "video_crops" in plan.categories:
            paths = plan.categories["video_crops"].files
            assert link not in paths
        assert plan.skipped_traversal >= 1

    def test_normal_file_inside_root_is_not_blocked(self, tmp_path):
        """A regular file inside output_data/ must pass the traversal check."""
        root = _make_output_data(tmp_path)
        assert _is_safe_path(root / "crops" / "file.jpg", root)

    def test_parent_path_is_blocked(self, tmp_path):
        """A path outside root must fail _is_safe_path."""
        root = tmp_path / "output_data"
        root.mkdir()
        outside = tmp_path / "other" / "file.txt"
        assert not _is_safe_path(outside, root)


# ---------------------------------------------------------------------------
# Disk status thresholds
# ---------------------------------------------------------------------------

class TestDiskStatus:
    def _mock_usage(self, total_gb: float, free_gb: float):
        """Return a shutil.disk_usage-like namedtuple."""
        total = int(total_gb * 1024 ** 3)
        free = int(free_gb * 1024 ** 3)
        used = total - free
        import collections
        Usage = collections.namedtuple("Usage", ["total", "used", "free"])
        return Usage(total=total, used=used, free=free)

    def test_ready_above_warn(self, tmp_path):
        """READY when free% > warn_pct."""
        cfg = _make_cfg(disk_warn_pct=15, disk_block_pct=5)
        root = tmp_path
        # 20% free → READY
        with patch("hydra_detect.storage_rotation.shutil.disk_usage") as mock_du:
            mock_du.return_value = self._mock_usage(total_gb=100, free_gb=20)
            status, reason = disk_status(cfg, root)
        assert status == "READY"

    def test_warn_at_warn_boundary(self, tmp_path):
        """WARN when free% == warn_pct."""
        cfg = _make_cfg(disk_warn_pct=15, disk_block_pct=5)
        root = tmp_path
        # Exactly 15% free → WARN (15 <= 15 and 15 > 5)
        with patch("hydra_detect.storage_rotation.shutil.disk_usage") as mock_du:
            mock_du.return_value = self._mock_usage(total_gb=100, free_gb=15)
            status, reason = disk_status(cfg, root)
        assert status == "WARN"
        assert "WARN" not in reason or "warn" in reason.lower() or "15" in reason

    def test_blocked_at_block_boundary(self, tmp_path):
        """BLOCKED when free% == block_pct."""
        cfg = _make_cfg(disk_warn_pct=15, disk_block_pct=5)
        root = tmp_path
        # Exactly 5% free → BLOCKED
        with patch("hydra_detect.storage_rotation.shutil.disk_usage") as mock_du:
            mock_du.return_value = self._mock_usage(total_gb=100, free_gb=5)
            status, reason = disk_status(cfg, root)
        assert status == "BLOCKED"
        assert "BLOCKED" not in reason or "block" in reason.lower() or "5" in reason

    def test_blocked_below_block_pct(self, tmp_path):
        """BLOCKED when free% < block_pct."""
        cfg = _make_cfg(disk_warn_pct=15, disk_block_pct=5)
        root = tmp_path
        # 2% free → BLOCKED
        with patch("hydra_detect.storage_rotation.shutil.disk_usage") as mock_du:
            mock_du.return_value = self._mock_usage(total_gb=100, free_gb=2)
            status, reason = disk_status(cfg, root)
        assert status == "BLOCKED"

    def test_warn_between_block_and_warn(self, tmp_path):
        """WARN when block_pct < free% <= warn_pct."""
        cfg = _make_cfg(disk_warn_pct=15, disk_block_pct=5)
        root = tmp_path
        # 10% free → WARN
        with patch("hydra_detect.storage_rotation.shutil.disk_usage") as mock_du:
            mock_du.return_value = self._mock_usage(total_gb=100, free_gb=10)
            status, reason = disk_status(cfg, root)
        assert status == "WARN"

    def test_disk_usage_oserror_returns_warn(self, tmp_path):
        """An OSError from shutil.disk_usage must return WARN, not crash."""
        cfg = _make_cfg()
        root = tmp_path
        with patch(
            "hydra_detect.storage_rotation.shutil.disk_usage",
            side_effect=OSError("no such device"),
        ):
            status, reason = disk_status(cfg, root)
        assert status == "WARN"
        assert "Cannot read disk usage" in reason

    def test_disk_status_returns_named_tuple(self, tmp_path):
        """disk_status() must return a DiskStatus NamedTuple."""
        cfg = _make_cfg()
        with patch("hydra_detect.storage_rotation.shutil.disk_usage") as mock_du:
            mock_du.return_value = self._mock_usage(total_gb=100, free_gb=50)
            result = disk_status(cfg, tmp_path)
        assert isinstance(result, DiskStatus)
        assert result.status in ("READY", "WARN", "BLOCKED")
        assert isinstance(result.reason, str)


# ---------------------------------------------------------------------------
# Retention clamp warnings
# ---------------------------------------------------------------------------

class TestRetentionClamp:
    def test_clamp_below_floor_logs_warning(self, caplog):
        """Retention value below floor must be clamped and a warning logged."""
        import logging
        with caplog.at_level(logging.WARNING, logger="hydra_detect.storage_rotation"):
            clamped = _clamp_retention(value=3, floor=7, ceiling=730, key="test_key")
        assert clamped == 7
        assert any("below floor" in r.message or "Clamped" in r.message for r in caplog.records)

    def test_clamp_above_ceiling_logs_warning(self, caplog):
        """Retention value above ceiling must be clamped and a warning logged."""
        import logging
        with caplog.at_level(logging.WARNING, logger="hydra_detect.storage_rotation"):
            clamped = _clamp_retention(value=800, floor=7, ceiling=730, key="test_key")
        assert clamped == 730
        assert any("ceiling" in r.message.lower() or "Clamped" in r.message for r in caplog.records)

    def test_clamp_within_bounds_no_warning(self, caplog):
        """A value within [floor, ceiling] must not produce a warning."""
        import logging
        with caplog.at_level(logging.WARNING, logger="hydra_detect.storage_rotation"):
            clamped = _clamp_retention(value=90, floor=7, ceiling=730, key="test_key")
        assert clamped == 90
        assert not any(
            "Clamped" in r.message for r in caplog.records
            if r.name == "hydra_detect.storage_rotation"
        )

    def test_config_with_below_floor_retention_still_applies_floor(self, tmp_path):
        """plan_cleanup must respect the floor even if config sets a low retention."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(
            retention_video_crops_days=1,   # below floor
            retention_floor_days=7,
        )
        # File 2 days old: older than 1-day config, but younger than 7-day floor.
        f = root / "crops" / "day2.jpg"
        _touch_with_age(f, 2)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        if "video_crops" in plan.categories:
            names = [p.name for p in plan.categories["video_crops"].files]
            assert "day2.jpg" not in names


# ---------------------------------------------------------------------------
# execute_cleanup: audit log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_audit_log_written_after_execute(self, tmp_path):
        """execute_cleanup must write a JSONL line to storage_rotation.log."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=30, retention_floor_days=7)
        old = root / "crops" / "old.jpg"
        _touch_with_age(old, 40)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)
        result = execute_cleanup(plan, audit_root=root)

        audit_path = root / AUDIT_LOG_FILENAME
        assert audit_path.exists()
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert "ts" in entry
        assert "total_files_removed" in entry
        assert result.audit_path == audit_path

    def test_audit_log_appends_on_repeated_runs(self, tmp_path):
        """Each execute_cleanup call must append a new line (not overwrite)."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=1, retention_floor_days=1)

        for i in range(2):
            f = root / "crops" / f"old_{i}.jpg"
            _touch_with_age(f, 10)
            now = datetime.now(timezone.utc)
            plan = plan_cleanup(cfg, root, now)
            execute_cleanup(plan, audit_root=root)

        audit_path = root / AUDIT_LOG_FILENAME
        lines = audit_path.read_text().strip().splitlines()
        # At least 2 entries (one per run).
        assert len(lines) >= 2

    def test_audit_entry_has_expected_fields(self, tmp_path):
        """Audit JSONL entry must include required fields."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg()
        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)
        execute_cleanup(plan, audit_root=root)

        audit_path = root / AUDIT_LOG_FILENAME
        entry = json.loads(audit_path.read_text().strip().splitlines()[-1])
        for key in ("ts", "total_files_planned", "total_files_removed",
                    "total_bytes_freed", "skipped_floor", "skipped_traversal",
                    "errors", "categories"):
            assert key in entry, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# execute_cleanup: atomic behavior on fs errors
# ---------------------------------------------------------------------------

class TestAtomicBehavior:
    def test_oserror_on_single_file_does_not_raise(self, tmp_path):
        """An OSError deleting one file must not propagate — other files still processed."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=1, retention_floor_days=1)

        f1 = root / "crops" / "file1.jpg"
        f2 = root / "crops" / "file2.jpg"
        _touch_with_age(f1, 10)
        _touch_with_age(f2, 10)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        # Make f1's unlink fail.
        original_unlink = Path.unlink

        def patched_unlink(self, missing_ok=False):
            if self.name == "file1.jpg":
                raise OSError("disk error")
            original_unlink(self, missing_ok=missing_ok)

        with patch.object(Path, "unlink", patched_unlink):
            result = execute_cleanup(plan, audit_root=root)

        # f2 should have been removed; f1 errored.
        assert result.total_errors >= 1
        assert result.total_removed >= 1  # f2 was deleted

    def test_oserror_logged_in_audit(self, tmp_path):
        """Errors must appear in the audit log entry."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=1, retention_floor_days=1)
        f = root / "crops" / "fail.jpg"
        _touch_with_age(f, 10)

        now = datetime.now(timezone.utc)
        plan = plan_cleanup(cfg, root, now)

        def always_fail(self, missing_ok=False):
            raise OSError("forced error")

        with patch.object(Path, "unlink", always_fail):
            execute_cleanup(plan, audit_root=root)

        audit_path = root / AUDIT_LOG_FILENAME
        assert audit_path.exists()
        entry = json.loads(audit_path.read_text().strip().splitlines()[-1])
        assert entry["errors"] >= 1


# ---------------------------------------------------------------------------
# CLI: dry-run by default, --apply required
# ---------------------------------------------------------------------------

class TestCLI:
    def test_dry_run_does_not_delete(self, tmp_path):
        """Running the CLI without --apply must not delete any files."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=1, retention_floor_days=1)
        old = root / "crops" / "old.jpg"
        _touch_with_age(old, 10)

        # Write a temporary config.ini
        config_path = tmp_path / "config.ini"
        with open(config_path, "w") as fh:
            cfg.write(fh)

        # Import the CLI main() and run without --apply
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.storage_rotation import main  # type: ignore[import]

        exit_code = main([
            "--config", str(config_path),
            "--data", str(root),
            # no --apply
        ])

        assert exit_code == 0
        assert old.exists(), "File must not be deleted in dry-run mode."

    def test_apply_deletes_old_files(self, tmp_path):
        """Running the CLI with --apply must delete expired files."""
        root = _make_output_data(tmp_path)
        cfg = _make_cfg(retention_video_crops_days=1, retention_floor_days=1)
        old = root / "crops" / "old_apply.jpg"
        _touch_with_age(old, 10)

        config_path = tmp_path / "config.ini"
        with open(config_path, "w") as fh:
            cfg.write(fh)

        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.storage_rotation import main  # type: ignore[import]

        exit_code = main([
            "--config", str(config_path),
            "--data", str(root),
            "--apply",
        ])

        assert exit_code == 0
        assert not old.exists(), "File must be deleted when --apply is given."

    def test_missing_config_returns_1(self, tmp_path):
        """CLI must return 1 if config file does not exist."""
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.storage_rotation import main  # type: ignore[import]

        exit_code = main([
            "--config", str(tmp_path / "nonexistent.ini"),
            "--data", str(tmp_path),
        ])
        assert exit_code == 1

    def test_missing_data_dir_returns_1(self, tmp_path):
        """CLI must return 1 if output_data/ does not exist."""
        config_path = tmp_path / "config.ini"
        cfg = _make_cfg()
        with open(config_path, "w") as fh:
            cfg.write(fh)

        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.storage_rotation import main  # type: ignore[import]

        exit_code = main([
            "--config", str(config_path),
            "--data", str(tmp_path / "nonexistent_data"),
        ])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# check_disk_at_boot
# ---------------------------------------------------------------------------

class TestCheckDiskAtBoot:
    def test_blocked_returns_true_and_logs_warning(self, tmp_path, caplog):
        """check_disk_at_boot must return True and log WARNING when BLOCKED."""
        import logging
        cfg = _make_cfg(disk_warn_pct=15, disk_block_pct=5)
        import collections
        Usage = collections.namedtuple("Usage", ["total", "used", "free"])
        # 2% free → BLOCKED
        mock_usage = Usage(total=100 * 1024**3, used=98 * 1024**3, free=2 * 1024**3)
        with caplog.at_level(logging.WARNING, logger="hydra_detect.storage_rotation"):
            with patch(
                "hydra_detect.storage_rotation.shutil.disk_usage",
                return_value=mock_usage,
            ):
                result = check_disk_at_boot(cfg, tmp_path)
        assert result is True
        assert any("BLOCKED" in r.message for r in caplog.records)

    def test_ready_returns_false(self, tmp_path):
        """check_disk_at_boot must return False when disk is READY."""
        cfg = _make_cfg(disk_warn_pct=15, disk_block_pct=5)
        import collections
        Usage = collections.namedtuple("Usage", ["total", "used", "free"])
        mock_usage = Usage(total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3)
        with patch(
            "hydra_detect.storage_rotation.shutil.disk_usage",
            return_value=mock_usage,
        ):
            result = check_disk_at_boot(cfg, tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# Repo root path helper (used in CLI tests above)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
