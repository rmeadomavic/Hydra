"""Tests for DetectionLogger rotation and retention features."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from hydra_detect.detection_logger import DetectionLogger
from hydra_detect.tracker import TrackedObject, TrackingResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracking_result(n: int = 1) -> TrackingResult:
    """Return a TrackingResult with *n* dummy tracks."""
    tracks = []
    for i in range(n):
        t = TrackedObject(
            track_id=i + 1,
            x1=10.0, y1=10.0, x2=50.0, y2=80.0,
            confidence=0.9,
            class_id=0,
            label="person",
        )
        tracks.append(t)
    return TrackingResult(tracks)


def _make_logger(tmp_path: Path, **kwargs) -> DetectionLogger:
    """Create a DetectionLogger writing to tmp_path with test defaults."""
    defaults = dict(
        log_dir=str(tmp_path / "logs"),
        log_format="jsonl",
        save_images=False,
        image_dir=str(tmp_path / "images"),
        max_log_size_mb=0.0001,   # ~100 bytes — triggers rotation quickly
        max_log_files=3,
    )
    defaults.update(kwargs)
    return DetectionLogger(**defaults)


# ---------------------------------------------------------------------------
# Unit tests: _open_log_file / _close_log_file
# ---------------------------------------------------------------------------

class TestOpenCloseLogFile:
    def test_first_open_creates_file(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl._log_dir.mkdir(parents=True, exist_ok=True)
        assert dl._open_log_file()
        assert dl._current_log_path is not None
        assert dl._current_log_path.exists()
        dl._close_log_file()

    def test_index_increments_on_each_open(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl._log_dir.mkdir(parents=True, exist_ok=True)
        dl._open_log_file()
        assert dl._log_index == 1
        dl._close_log_file()
        dl._open_log_file()
        assert dl._log_index == 2
        dl._close_log_file()

    def test_file_named_with_zero_padded_index(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl._log_dir.mkdir(parents=True, exist_ok=True)
        dl._open_log_file()
        assert dl._current_log_path.name == "detections_001.jsonl"
        dl._close_log_file()

    def test_csv_extension_when_format_is_csv(self, tmp_path):
        dl = _make_logger(tmp_path, log_format="csv")
        dl._log_dir.mkdir(parents=True, exist_ok=True)
        dl._open_log_file()
        assert dl._current_log_path.name == "detections_001.csv"
        dl._close_log_file()

    def test_close_sets_handles_to_none(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl._log_dir.mkdir(parents=True, exist_ok=True)
        dl._open_log_file()
        dl._close_log_file()
        assert dl._json_file is None
        assert dl._csv_file is None
        assert dl._csv_writer is None

    def test_seed_index_from_existing_logs(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "detections_001.jsonl").write_text("{}\n")
        (log_dir / "detections_007.jsonl").write_text("{}\n")

        dl = _make_logger(tmp_path, log_dir=str(log_dir))
        dl._seed_log_index()

        assert dl._log_index == 7

    def test_open_after_seed_uses_next_index(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "detections_004.jsonl").write_text("{}\n")

        dl = _make_logger(tmp_path, log_dir=str(log_dir))
        dl._seed_log_index()
        assert dl._open_log_file()

        assert dl._current_log_path.name == "detections_005.jsonl"
        dl._close_log_file()


# ---------------------------------------------------------------------------
# Unit tests: _prune_old_logs
# ---------------------------------------------------------------------------

class TestPruneOldLogs:
    def test_excess_files_deleted(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # Create 5 dummy log files
        for i in range(1, 6):
            p = log_dir / f"detections_{i:03d}.jsonl"
            p.write_text('{"x":1}\n')
            # Stagger mtimes so sort order is deterministic
            t = 1_700_000_000 + i
            import os
            os.utime(p, (t, t))

        dl = _make_logger(tmp_path, log_dir=str(log_dir), max_log_files=3)
        dl._prune_old_logs()

        remaining = sorted(log_dir.glob("detections_*.jsonl"))
        assert len(remaining) == 3
        # Oldest two (001, 002) should be gone; 003-005 should survive
        names = [p.name for p in remaining]
        assert "detections_001.jsonl" not in names
        assert "detections_002.jsonl" not in names
        assert "detections_005.jsonl" in names

    def test_no_deletion_when_under_limit(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        for i in range(1, 3):
            (log_dir / f"detections_{i:03d}.jsonl").write_text("{}\n")

        dl = _make_logger(tmp_path, log_dir=str(log_dir), max_log_files=5)
        dl._prune_old_logs()

        assert len(list(log_dir.glob("detections_*.jsonl"))) == 2


# ---------------------------------------------------------------------------
# Unit tests: _prune_old_images
# ---------------------------------------------------------------------------

class TestPruneOldImages:
    def test_excess_images_deleted(self, tmp_path):
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        import os

        # max_log_files=1 -> budget = 1*200 = 200; create 201 images
        for i in range(201):
            p = image_dir / f"frame_{i:06d}.jpg"
            p.write_bytes(b"\xff\xd8\xff")   # minimal JPEG header
            os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))

        dl = _make_logger(
            tmp_path,
            image_dir=str(image_dir),
            save_images=True,
            max_log_files=1,
        )
        dl._prune_old_images()

        remaining = list(image_dir.glob("*.jpg"))
        assert len(remaining) == 200

    def test_no_deletion_when_under_limit(self, tmp_path):
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        for i in range(5):
            (image_dir / f"frame_{i:06d}.jpg").write_bytes(b"\xff\xd8\xff")

        dl = _make_logger(tmp_path, image_dir=str(image_dir), max_log_files=5)
        dl._prune_old_images()

        assert len(list(image_dir.glob("*.jpg"))) == 5


# ---------------------------------------------------------------------------
# Integration test: rotation via start / log / stop
# ---------------------------------------------------------------------------

class TestRotationIntegration:
    def test_rotation_creates_new_file(self, tmp_path):
        """Writing enough data through the queue should trigger a new log file."""
        dl = _make_logger(
            tmp_path,
            # Very small threshold so every flush cycle triggers rotation
            max_log_size_mb=0.000001,
            max_log_files=10,
        )
        dl.start()

        tracking = _make_tracking_result(1)
        # Emit 60 frames: flush happens every 30, giving ≥2 rotation checks
        for i in range(60):
            dl.log(tracking, frame=None, gps=None)

        # Give the background thread time to drain
        dl.stop()

        log_files = sorted((tmp_path / "logs").glob("detections_*.jsonl"))
        assert len(log_files) >= 2, (
            f"Expected at least 2 rotated log files, got {len(log_files)}: {log_files}"
        )

    def test_retention_enforced(self, tmp_path):
        """Old log files beyond max_log_files are pruned during rotation."""
        dl = _make_logger(
            tmp_path,
            max_log_size_mb=0.000001,
            max_log_files=2,
        )
        dl.start()

        tracking = _make_tracking_result(1)
        # 120 frames → at least 4 flush cycles → at least 4 rotations
        for _ in range(120):
            dl.log(tracking, frame=None, gps=None)

        dl.stop()

        log_files = list((tmp_path / "logs").glob("detections_*.jsonl"))
        assert len(log_files) <= 2, (
            f"Expected ≤2 log files after pruning, got {len(log_files)}: {log_files}"
        )

    def test_log_file_contains_valid_jsonl(self, tmp_path):
        """Records written to the log file must be valid JSON lines."""
        dl = _make_logger(
            tmp_path,
            # Large threshold so no rotation happens
            max_log_size_mb=100.0,
            max_log_files=5,
        )
        dl.start()

        tracking = _make_tracking_result(2)
        for _ in range(5):
            dl.log(tracking, frame=None, gps=None)

        dl.stop()

        log_files = sorted((tmp_path / "logs").glob("detections_*.jsonl"))
        assert log_files, "At least one log file must exist"
        records = []
        for lf in log_files:
            for line in lf.read_text().splitlines():
                if line.strip():
                    records.append(json.loads(line))
        assert len(records) > 0
        assert all("label" in r for r in records)

    @patch("hydra_detect.detection_logger.open", new_callable=mock_open)
    def test_rotation_failure_disables_logger_without_closing_current_file(self, mock_file, tmp_path):
        dl = _make_logger(tmp_path)
        dl._log_dir.mkdir(parents=True, exist_ok=True)
        assert dl._open_log_file()
        dl._max_log_size_bytes = 0

        original_handle = dl._json_file
        assert original_handle is not None

        mock_file.side_effect = OSError("disk full")
        dl._rotate_if_needed()

        assert dl._disabled is True
        assert dl._json_file is original_handle


# ---------------------------------------------------------------------------
# Constructor parameter tests
# ---------------------------------------------------------------------------

class TestConstructorParams:
    def test_max_log_size_stored_as_bytes(self, tmp_path):
        dl = _make_logger(tmp_path, max_log_size_mb=5.0)
        assert dl._max_log_size_bytes == 5 * 1024 * 1024

    def test_max_log_files_minimum_one(self, tmp_path):
        dl = _make_logger(tmp_path, max_log_files=0)
        assert dl._max_log_files == 1

    def test_defaults(self, tmp_path):
        dl = DetectionLogger(
            log_dir=str(tmp_path / "logs"),
            image_dir=str(tmp_path / "images"),
        )
        assert dl._max_log_size_bytes == 10 * 1024 * 1024
        assert dl._max_log_files == 20
