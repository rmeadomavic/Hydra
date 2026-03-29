"""Tests for detection log chain-of-custody (issue #39)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from hydra_detect.detection_logger import DetectionLogger
from hydra_detect.tracker import TrackedObject, TrackingResult
from hydra_detect.verify_log import verify


def _make_tracking_result() -> TrackingResult:
    return TrackingResult(
        tracks=[TrackedObject(
            track_id=1, x1=10, y1=20, x2=100, y2=200,
            confidence=0.95, class_id=0, label="person",
        )],
        active_ids=1,
    )


class TestChainOfCustody:
    def test_records_include_model_hash(self):
        """Each logged record should contain the model hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dl = DetectionLogger(
                log_dir=tmpdir, log_format="jsonl",
                save_images=False, image_dir=tmpdir,
                model_hash="abc123def456",
            )
            dl.start()
            dl.log(_make_tracking_result(), frame=np.zeros((100, 100, 3), dtype=np.uint8))
            dl.stop()

            log_file = list(Path(tmpdir).glob("*.jsonl"))[0]
            record = json.loads(log_file.read_text().strip())
            assert record["model_hash"] == "abc123def456"

    def test_records_include_chain_hash(self):
        """Each logged record should contain a chain_hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dl = DetectionLogger(
                log_dir=tmpdir, log_format="jsonl",
                save_images=False, image_dir=tmpdir,
            )
            dl.start()
            dl.log(_make_tracking_result(), frame=np.zeros((100, 100, 3), dtype=np.uint8))
            dl.stop()

            log_file = list(Path(tmpdir).glob("*.jsonl"))[0]
            record = json.loads(log_file.read_text().strip())
            assert "chain_hash" in record
            assert len(record["chain_hash"]) == 64  # SHA-256 hex

    def test_chain_verifies_intact(self):
        """verify() should pass for an unmodified log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dl = DetectionLogger(
                log_dir=tmpdir, log_format="jsonl",
                save_images=False, image_dir=tmpdir,
                model_hash="test_model_hash",
            )
            dl.start()
            for _ in range(5):
                dl.log(_make_tracking_result(), frame=np.zeros((100, 100, 3), dtype=np.uint8))
            dl.stop()

            log_file = list(Path(tmpdir).glob("*.jsonl"))[0]
            ok, count, msg = verify(log_file)
            assert ok is True
            assert count == 5

    def test_chain_detects_tampering(self):
        """verify() should fail if a record is modified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dl = DetectionLogger(
                log_dir=tmpdir, log_format="jsonl",
                save_images=False, image_dir=tmpdir,
            )
            dl.start()
            for _ in range(3):
                dl.log(_make_tracking_result(), frame=np.zeros((100, 100, 3), dtype=np.uint8))
            dl.stop()

            log_file = list(Path(tmpdir).glob("*.jsonl"))[0]
            lines = log_file.read_text().strip().split("\n")
            # Tamper with the second record
            record = json.loads(lines[1])
            record["confidence"] = 0.999
            lines[1] = json.dumps(record)
            log_file.write_text("\n".join(lines) + "\n")

            ok, count, msg = verify(log_file)
            assert ok is False
            assert count == 2  # fails on the tampered line

    def test_verify_missing_file(self):
        """verify() should fail gracefully for a missing file."""
        ok, count, msg = verify("/nonexistent/file.jsonl")
        assert ok is False
