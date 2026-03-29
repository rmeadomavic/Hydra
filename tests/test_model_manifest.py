"""Tests for model manifest generation and validation (issue #40)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hydra_detect.model_manifest import (
    compute_file_hash,
    generate_manifest,
    load_manifest,
    validate_model,
)


def _make_model_dir(tmpdir: str) -> Path:
    """Create a temp dir with fake model files."""
    d = Path(tmpdir) / "models"
    d.mkdir()
    (d / "test.pt").write_bytes(b"fake model data")
    (d / "test2.pt").write_bytes(b"another model")
    return d


class TestComputeFileHash:
    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "test.bin"
            p.write_bytes(b"hello")
            h1 = compute_file_hash(p)
            h2 = compute_file_hash(p)
            assert h1 == h2
            assert len(h1) == 64


class TestGenerateManifest:
    def test_finds_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = _make_model_dir(tmpdir)
            entries = generate_manifest(str(d))
            assert len(entries) == 2
            names = {e["filename"] for e in entries}
            assert names == {"test.pt", "test2.pt"}

    def test_includes_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = _make_model_dir(tmpdir)
            entries = generate_manifest(str(d))
            for e in entries:
                assert len(e["sha256"]) == 64


class TestLoadManifest:
    def test_loads_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps([{"filename": "test.pt"}]))
            result = load_manifest(p)
            assert result is not None
            assert len(result) == 1

    def test_returns_none_for_missing(self):
        assert load_manifest(Path("/nonexistent/manifest.json")) is None

    def test_returns_none_for_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text("not json")
            assert load_manifest(p) is None


class TestValidateModel:
    def test_valid_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = _make_model_dir(tmpdir)
            h = compute_file_hash(d / "test.pt")
            entry = {"filename": "test.pt", "sha256": h, "classes": ["person"]}
            ok, reason = validate_model(entry, [d])
            assert ok is True

    def test_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = _make_model_dir(tmpdir)
            entry = {"filename": "test.pt", "sha256": "0" * 64, "classes": ["person"]}
            ok, reason = validate_model(entry, [d])
            assert ok is False
            assert "checksum mismatch" in reason

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            entry = {"filename": "missing.pt", "sha256": "abc", "classes": ["person"]}
            ok, reason = validate_model(entry, [d])
            assert ok is False
            assert "not found" in reason

    def test_empty_classes_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = _make_model_dir(tmpdir)
            h = compute_file_hash(d / "test.pt")
            entry = {"filename": "test.pt", "sha256": h, "classes": []}
            ok, reason = validate_model(entry, [d])
            assert ok is False
            assert "empty class" in reason
