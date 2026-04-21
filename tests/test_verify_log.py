"""Tests for verify_log — SHA-256 chain integrity validator.

Security-relevant: this is the tamper-detection mechanism for detection logs.
Zero test coverage before this file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hydra_detect.verify_log import verify


GENESIS = "0" * 64


def _append_record(path: Path, prev_hash: str, payload: dict) -> str:
    """Append a record + chain_hash to the file, return the new chain hash."""
    record_json = json.dumps(payload, sort_keys=True)
    chain_hash = hashlib.sha256((record_json + prev_hash).encode()).hexdigest()
    record = dict(payload)
    record["chain_hash"] = chain_hash
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return chain_hash


class TestVerify:
    def test_missing_file_returns_false(self, tmp_path):
        ok, count, msg = verify(tmp_path / "nope.jsonl")
        assert ok is False
        assert count == 0
        assert "not found" in msg.lower()

    def test_empty_file_is_valid(self, tmp_path):
        p = tmp_path / "log.jsonl"
        p.write_text("")
        ok, count, msg = verify(p)
        assert ok is True
        assert count == 0

    def test_valid_chain_verifies(self, tmp_path):
        p = tmp_path / "log.jsonl"
        h = GENESIS
        h = _append_record(p, h, {"t": 1, "label": "person"})
        h = _append_record(p, h, {"t": 2, "label": "car"})
        h = _append_record(p, h, {"t": 3, "label": "truck"})

        ok, count, msg = verify(p)
        assert ok is True
        assert count == 3
        assert "3 records verified" in msg

    def test_broken_chain_detected(self, tmp_path):
        p = tmp_path / "log.jsonl"
        h = GENESIS
        h = _append_record(p, h, {"t": 1, "label": "person"})
        # Now corrupt the chain by appending with the WRONG prev hash
        bad_record = {"t": 2, "label": "car"}
        bad_hash = hashlib.sha256(
            (json.dumps(bad_record, sort_keys=True) + "deadbeef" * 8).encode()
        ).hexdigest()
        bad_record["chain_hash"] = bad_hash
        with open(p, "a") as f:
            f.write(json.dumps(bad_record) + "\n")

        ok, count, msg = verify(p)
        assert ok is False
        assert count == 2
        assert "chain broken" in msg

    def test_missing_chain_hash_field(self, tmp_path):
        p = tmp_path / "log.jsonl"
        p.write_text(json.dumps({"t": 1, "label": "person"}) + "\n")
        ok, count, msg = verify(p)
        assert ok is False
        assert "missing chain_hash" in msg

    def test_truncated_final_record_tolerated(self, tmp_path):
        """A partially-written final line (incomplete JSON) is skipped, not failed."""
        p = tmp_path / "log.jsonl"
        h = GENESIS
        _append_record(p, h, {"t": 1, "label": "person"})
        # Append garbage (simulating power loss mid-write)
        with open(p, "a") as f:
            f.write('{"t": 2, "label": "car", "chain_hash": "af')

        ok, count, msg = verify(p)
        assert ok is True
        assert count == 1
        assert "truncated" in msg

    def test_invalid_json_midfile_fails(self, tmp_path):
        """JSON errors mid-file (not just final line) must fail hard."""
        p = tmp_path / "log.jsonl"
        h = GENESIS
        _append_record(p, h, {"t": 1, "label": "person"})
        with open(p, "a") as f:
            f.write("garbage not json\n")
        # Still add a valid-looking final record so the garbage isn't "truncated"
        _append_record(p, GENESIS, {"t": 3, "label": "truck"})

        ok, count, msg = verify(p)
        assert ok is False
        assert "invalid JSON" in msg

    def test_accepts_path_as_string(self, tmp_path):
        p = tmp_path / "log.jsonl"
        _append_record(p, GENESIS, {"t": 1, "label": "person"})
        ok, _, _ = verify(str(p))
        assert ok is True
