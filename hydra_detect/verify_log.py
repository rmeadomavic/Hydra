"""Verify the integrity of a Hydra detection log's SHA-256 hash chain.

Usage::

    python -m hydra_detect.verify_log <logfile.jsonl>
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def verify(path: str | Path) -> tuple[bool, int, str]:
    """Verify a JSONL log file's rolling hash chain.

    Returns (ok, line_count, message).
    Tolerates a truncated (incomplete JSON) final record — logs a warning
    and reports the chain as valid up to the second-to-last record.
    """
    path = Path(path)
    if not path.exists():
        return False, 0, f"File not found: {path}"

    prev_hash = "0" * 64  # genesis hash
    line_num = 0
    lines: list[str] = []

    with open(path) as f:
        for raw_line in f:
            stripped = raw_line.strip()
            if stripped:
                lines.append(stripped)

    for i, line in enumerate(lines):
        line_num = i + 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            # Tolerate truncated final record
            if i == len(lines) - 1:
                logger.warning(
                    "Line %d: truncated final record (incomplete JSON) — "
                    "chain valid up to line %d",
                    line_num, line_num - 1,
                )
                return (
                    True,
                    line_num - 1,
                    f"OK — {line_num - 1} records verified, chain intact "
                    f"(line {line_num} truncated, skipped).",
                )
            return False, line_num, f"Line {line_num}: invalid JSON — {exc}"

        stored_hash = record.pop("chain_hash", None)
        if stored_hash is None:
            return False, line_num, f"Line {line_num}: missing chain_hash field"

        # Any optional fields (e.g. time_source) present in the record were
        # included in the hash at write time, so they are covered naturally
        # here.  No special handling needed — the chain remains intact whether
        # or not optional fields are present, as long as write and verify use
        # the same sort_keys=True serialization.
        record_json = json.dumps(record, sort_keys=True)
        expected = hashlib.sha256(
            (record_json + prev_hash).encode()
        ).hexdigest()

        if stored_hash != expected:
            return (
                False,
                line_num,
                f"Line {line_num}: chain broken — "
                f"expected {expected[:16]}..., got {stored_hash[:16]}...",
            )

        prev_hash = stored_hash

    return True, line_num, f"OK — {line_num} records verified, chain intact."


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m hydra_detect.verify_log <logfile.jsonl>")
        sys.exit(1)

    ok, count, msg = verify(sys.argv[1])
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
