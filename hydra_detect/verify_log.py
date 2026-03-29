"""Verify the integrity of a Hydra detection log's SHA-256 hash chain.

Usage::

    python -m hydra_detect.verify_log <logfile.jsonl>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def verify(path: str | Path) -> tuple[bool, int, str]:
    """Verify a JSONL log file's rolling hash chain.

    Returns (ok, line_count, message).
    """
    path = Path(path)
    if not path.exists():
        return False, 0, f"File not found: {path}"

    prev_hash = "0" * 64  # genesis hash
    line_num = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            line_num += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                return False, line_num, f"Line {line_num}: invalid JSON — {exc}"

            stored_hash = record.pop("chain_hash", None)
            if stored_hash is None:
                return False, line_num, f"Line {line_num}: missing chain_hash field"

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
