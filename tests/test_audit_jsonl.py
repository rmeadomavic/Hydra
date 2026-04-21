"""Tests for the durable JSONL audit sink (FileJSONLSink).

Covers the spec laid out in impl_audit_jsonl.md:
  a) write + read-back a JSONL entry
  b) rotation triggers at configured size
  c) oldest file pruned when max_rotations exceeded
  d) disabled config → no file written
  e) disk-slow simulation → buffer kicks in, no crash
  f) lines are valid JSON
"""

from __future__ import annotations

import configparser
import io
import json
import threading
from pathlib import Path

import pytest

from hydra_detect.audit import (
    FileJSONLSink,
    attach_file_sink,
    get_default_file_sink,
)
from hydra_detect.audit.audit_log import _FILE_BUFFER_MAXLEN


def _read_all_lines(p: Path) -> list[str]:
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8").splitlines()


class TestFileJSONLSinkBasics:
    def test_write_and_read_back(self, tmp_path: Path) -> None:
        sink = FileJSONLSink(path=tmp_path / "hydra.jsonl")
        sink.push(
            kind="tak_accepted",
            message="TAK_CMD_ACCEPTED loiter",
            ref="cmd-123",
            operator="alpha",
            ts=1700000000.5,
        )
        sink.close()

        lines = _read_all_lines(tmp_path / "hydra.jsonl")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["ts"] == pytest.approx(1700000000.5)
        assert entry["kind"] == "tak_accepted"
        assert entry["message"] == "TAK_CMD_ACCEPTED loiter"
        assert entry["ref"] == "cmd-123"
        assert entry["operator"] == "alpha"

    def test_all_lines_are_valid_json(self, tmp_path: Path) -> None:
        sink = FileJSONLSink(path=tmp_path / "hydra.jsonl")
        try:
            for i in range(25):
                sink.push(
                    kind="approach_arm_events",
                    message=f"APPROACH ARM iteration {i}",
                    ref={"i": i, "dict_ref": True},
                    operator=None if i % 2 else "op",
                    ts=1700000000.0 + i,
                )
        finally:
            sink.close()

        lines = _read_all_lines(tmp_path / "hydra.jsonl")
        assert len(lines) == 25
        for raw in lines:
            parsed = json.loads(raw)  # must not raise
            assert set(parsed.keys()) == {
                "ts", "kind", "message", "ref", "operator",
            }

    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "deeply" / "nested" / "audit"
        assert not nested.exists()
        sink = FileJSONLSink(path=nested / "hydra.jsonl")
        try:
            sink.push(kind="other", message="hello")
        finally:
            sink.close()
        assert (nested / "hydra.jsonl").exists()


class TestFileJSONLSinkRotation:
    def test_rotation_triggers_at_size(self, tmp_path: Path) -> None:
        # 2 KB cap → ~20 lines of ~100 chars each trigger a rotation.
        sink = FileJSONLSink(
            path=tmp_path / "hydra.jsonl",
            max_size_mb=2.0 / 1024.0,   # 2 KB
            max_rotations=5,
        )
        try:
            padding = "x" * 180
            for i in range(40):
                sink.push(
                    kind="other",
                    message=f"{padding}-{i}",
                    ts=1700000000.0 + i,
                )
        finally:
            sink.close()

        rot1 = tmp_path / "hydra.jsonl.1"
        assert rot1.exists(), "expected .1 rotation after crossing size"
        base = tmp_path / "hydra.jsonl"
        assert base.exists()
        # Both sets should parse cleanly.
        for raw in _read_all_lines(rot1) + _read_all_lines(base):
            json.loads(raw)

    def test_oldest_pruned_when_max_rotations_exceeded(
        self, tmp_path: Path,
    ) -> None:
        sink = FileJSONLSink(
            path=tmp_path / "hydra.jsonl",
            max_size_mb=1.0 / 1024.0,   # 1 KB
            max_rotations=3,
        )
        try:
            padding = "y" * 200
            # Enough lines to trigger multiple rotations past the max.
            for i in range(200):
                sink.push(
                    kind="other",
                    message=f"{padding}-{i}",
                    ts=1700000000.0 + i,
                )
        finally:
            sink.close()

        # .1 .. .max_rotations may exist, nothing beyond.
        for n in (1, 2, 3):
            assert (tmp_path / f"hydra.jsonl.{n}").exists()
        assert not (tmp_path / "hydra.jsonl.4").exists()
        assert not (tmp_path / "hydra.jsonl.5").exists()

    def test_rotation_on_open_if_existing_file_oversize(
        self, tmp_path: Path,
    ) -> None:
        # Pre-seed an oversize base file; constructor must rotate it.
        base = tmp_path / "hydra.jsonl"
        base.write_text("x" * 4096, encoding="utf-8")
        sink = FileJSONLSink(
            path=base,
            max_size_mb=1.0 / 1024.0,   # 1 KB → the 4 KB file must rotate
            max_rotations=5,
        )
        sink.push(kind="other", message="after-open")
        sink.close()

        assert (tmp_path / "hydra.jsonl.1").exists()
        # Fresh base has only the new line.
        base_lines = _read_all_lines(base)
        assert len(base_lines) == 1
        assert json.loads(base_lines[0])["message"] == "after-open"


class _FlakyFile(io.StringIO):
    """Drop-in replacement for a text file that can fail on write."""

    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    def write(self, s: str) -> int:  # type: ignore[override]
        if self.fail:
            raise OSError("simulated disk stall")
        return super().write(s)

    def flush(self) -> None:  # type: ignore[override]
        if self.fail:
            raise OSError("simulated disk stall")
        super().flush()


class TestDiskSlowBehavior:
    def test_buffer_kicks_in_no_crash(self, tmp_path: Path) -> None:
        sink = FileJSONLSink(path=tmp_path / "hydra.jsonl")
        # Swap the file object for a flaky one so writes raise OSError.
        flaky = _FlakyFile()
        flaky.fail = True
        sink._file = flaky  # noqa: SLF001 — test hook

        # Must not raise even though the "disk" is failing.
        for i in range(10):
            sink.push(kind="other", message=f"line-{i}")
        assert sink.buffered() == 10

        # Recovery: flip the flag; next push drains the backlog.
        flaky.fail = False
        sink.push(kind="other", message="after-recovery")
        assert sink.buffered() == 0

        # 11 lines (10 queued + 1 that recovered them) landed in the
        # flaky buffer before close; verify all parse as JSON.
        lines = flaky.getvalue().splitlines()
        assert len(lines) == 11
        for raw in lines:
            json.loads(raw)

        # Keep flaky good so close() does not crash.
        sink.close()

    def test_buffer_drop_oldest_at_capacity(self, tmp_path: Path) -> None:
        sink = FileJSONLSink(
            path=tmp_path / "hydra.jsonl",
            buffer_maxlen=5,
        )
        flaky = _FlakyFile()
        flaky.fail = True
        sink._file = flaky  # noqa: SLF001

        for i in range(20):
            sink.push(kind="other", message=f"msg-{i}")
        # Buffer is bounded — oldest dropped.
        assert sink.buffered() == 5
        sink.close()

    def test_default_buffer_cap_is_500(self) -> None:
        assert _FILE_BUFFER_MAXLEN == 500


class TestGetDefaultFileSink:
    def test_disabled_returns_none(self, tmp_path: Path) -> None:
        cfg = configparser.ConfigParser()
        cfg["audit"] = {
            "enabled": "false",
            "jsonl_path": str(tmp_path / "hydra.jsonl"),
        }
        sink = get_default_file_sink(cfg)
        assert sink is None
        # Nothing written — disabled means no file, ever.
        assert not (tmp_path / "hydra.jsonl").exists()

    def test_enabled_reads_config(self, tmp_path: Path) -> None:
        target = tmp_path / "subdir" / "audit.jsonl"
        cfg = configparser.ConfigParser()
        cfg["audit"] = {
            "enabled": "true",
            "jsonl_path": str(target),
            "max_size_mb": "7",
            "max_rotations": "3",
        }
        sink = get_default_file_sink(cfg)
        assert sink is not None
        try:
            assert sink.path == target
            assert sink._max_bytes == 7 * 1024 * 1024  # noqa: SLF001
            assert sink._max_rotations == 3            # noqa: SLF001
            sink.push(kind="other", message="hi")
        finally:
            sink.close()
        assert target.exists()

    def test_missing_section_uses_defaults(self) -> None:
        cfg = configparser.ConfigParser()
        # No [audit] section at all.
        sink = get_default_file_sink(cfg)
        assert sink is not None
        try:
            # Defaults: 10 MB × 5 rotations = 50 MB ceiling, path /data/audit
            assert sink._max_bytes == 10 * 1024 * 1024  # noqa: SLF001
            assert sink._max_rotations == 5             # noqa: SLF001
            assert str(sink.path).endswith("hydra.jsonl")
        finally:
            sink.close()

    def test_none_config_returns_defaults(self) -> None:
        sink = get_default_file_sink(None)
        assert sink is not None
        sink.close()


class TestAttachFileSink:
    def test_attach_forwards_records(self, tmp_path: Path) -> None:
        import logging

        sink = FileJSONLSink(path=tmp_path / "hydra.jsonl")
        logger_name = f"hydra.audit.test.{id(sink)}"
        attach_file_sink(sink, logger_name=logger_name)

        logging.getLogger(logger_name).warning(
            "TAK_CMD_REJECTED stale HMAC",
        )
        sink.close()

        lines = _read_all_lines(tmp_path / "hydra.jsonl")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        # _classify() should slot this into tak_rejected.
        assert entry["kind"] == "tak_rejected"
        assert "TAK_CMD_REJECTED" in entry["message"]


class TestThreadSafety:
    def test_concurrent_pushes_all_land(self, tmp_path: Path) -> None:
        sink = FileJSONLSink(path=tmp_path / "hydra.jsonl")
        n_threads = 8
        n_per = 50

        def worker(tid: int) -> None:
            for i in range(n_per):
                sink.push(
                    kind="other",
                    message=f"t{tid}-i{i}",
                    ts=1700000000.0 + tid * 0.001 + i * 0.00001,
                )

        threads = [
            threading.Thread(target=worker, args=(tid,))
            for tid in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sink.close()

        lines = _read_all_lines(tmp_path / "hydra.jsonl")
        assert len(lines) == n_threads * n_per
        # Every line must still be parseable — no interleaving corruption.
        for raw in lines:
            json.loads(raw)


class TestSchemaWiring:
    def test_audit_section_present_in_schema(self) -> None:
        from hydra_detect.config_schema import SCHEMA

        assert "audit" in SCHEMA
        fields = SCHEMA["audit"]
        for key in ("enabled", "jsonl_path", "max_size_mb", "max_rotations"):
            assert key in fields

    def test_hud_layout_still_in_schema(self) -> None:
        # Preservation rule: do not drop hud_layout while editing schema.
        from hydra_detect.config_schema import SCHEMA

        assert "hud_layout" in SCHEMA["web"]
