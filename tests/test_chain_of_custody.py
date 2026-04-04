"""Tests for detection log chain-of-custody (issue #39)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
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

    def test_multi_detection_frame_chains_sequentially(self):
        """Each record in a multi-detection frame must chain from the previous."""
        with tempfile.TemporaryDirectory() as tmpdir:
            multi_result = TrackingResult(
                tracks=[
                    TrackedObject(track_id=1, x1=10, y1=20, x2=100, y2=200,
                                  confidence=0.9, class_id=0, label="person"),
                    TrackedObject(track_id=2, x1=110, y1=20, x2=200, y2=200,
                                  confidence=0.8, class_id=1, label="car"),
                    TrackedObject(track_id=3, x1=210, y1=20, x2=300, y2=200,
                                  confidence=0.7, class_id=2, label="truck"),
                ],
                active_ids=3,
            )
            dl = DetectionLogger(
                log_dir=tmpdir, log_format="jsonl",
                save_images=False, image_dir=tmpdir,
                model_hash="multi_test",
            )
            dl.start()
            dl.log(multi_result, frame=np.zeros((100, 100, 3), dtype=np.uint8))
            dl.stop()

            log_file = list(Path(tmpdir).glob("*.jsonl"))[0]
            lines = log_file.read_text().strip().split("\n")
            assert len(lines) == 3

            # Verify sequential chaining: each record's hash builds on previous
            prev_hash = "0" * 64
            for i, line in enumerate(lines):
                record = json.loads(line)
                stored_hash = record.pop("chain_hash")
                record_json = json.dumps(record, sort_keys=True)
                import hashlib
                expected = hashlib.sha256(
                    (record_json + prev_hash).encode()
                ).hexdigest()
                assert stored_hash == expected, (
                    f"Record {i}: chain broken — expected {expected[:16]}..., "
                    f"got {stored_hash[:16]}..."
                )
                prev_hash = stored_hash

            # Also verify via verify_log
            ok, count, msg = verify(log_file)
            assert ok is True
            assert count == 3

    def test_verify_missing_file(self):
        """verify() should fail gracefully for a missing file."""
        ok, count, msg = verify("/nonexistent/file.jsonl")
        assert ok is False
