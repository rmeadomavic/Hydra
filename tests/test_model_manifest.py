"""Tests for model manifest generation and validation (issue #40)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from hydra_detect.model_manifest import (
    compute_file_hash,
    extract_classes,
    generate_manifest,
    load_manifest,
    main,
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


class TestExtractClasses:
    def test_non_pt_returns_empty(self):
        """extract_classes() returns empty list for non-.pt files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine_path = Path(tmpdir) / "model.engine"
            engine_path.write_bytes(b"fake engine data")
            assert extract_classes(engine_path) == []

            onnx_path = Path(tmpdir) / "model.onnx"
            onnx_path.write_bytes(b"fake onnx data")
            assert extract_classes(onnx_path) == []

    def test_import_failure_returns_empty(self):
        """extract_classes() returns empty list when ultralytics cannot be imported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pt_path = Path(tmpdir) / "model.pt"
            pt_path.write_bytes(b"fake pt data")

            with patch.dict(sys.modules, {"ultralytics": None}):
                result = extract_classes(pt_path)
                assert result == []


class TestGenerateManifestClasses:
    def test_pt_files_get_classes_populated(self):
        """generate_manifest() populates classes for .pt files via YOLO introspection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir) / "models"
            d.mkdir()
            (d / "yolo.pt").write_bytes(b"fake model")

            mock_model = MagicMock()
            mock_model.names = {0: "person", 1: "car", 2: "dog"}

            with patch("hydra_detect.model_manifest.extract_classes",
                       return_value=["person", "car", "dog"]):
                entries = generate_manifest(str(d))

            assert len(entries) == 1
            assert entries[0]["classes"] == ["person", "car", "dog"]
            assert entries[0]["filename"] == "yolo.pt"


class TestMainCLIClassCopying:
    def test_engine_gets_classes_from_matching_pt(self):
        """CLI main() copies classes from .pt to .engine/.onnx with same stem."""
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir) / "models"
            d.mkdir()
            (d / "yolo.pt").write_bytes(b"fake pt model")
            (d / "yolo.engine").write_bytes(b"fake engine model")
            (d / "yolo.onnx").write_bytes(b"fake onnx model")

            test_classes = ["person", "car", "truck"]

            def mock_extract(path):
                if path.suffix == ".pt":
                    return test_classes
                return []

            with patch("hydra_detect.model_manifest.extract_classes",
                       side_effect=mock_extract):
                with patch("sys.argv", ["model_manifest", str(d)]):
                    main()

            # Read the generated manifest
            manifest_path = d / "manifest.json"
            assert manifest_path.exists()
            manifest = json.loads(manifest_path.read_text())

            # All three files should be in the manifest
            by_name = {e["filename"]: e for e in manifest}
            assert "yolo.pt" in by_name
            assert "yolo.engine" in by_name
            assert "yolo.onnx" in by_name

            # .pt should have classes from extract_classes
            assert by_name["yolo.pt"]["classes"] == test_classes
            # .engine and .onnx should have inherited classes from .pt
            assert by_name["yolo.engine"]["classes"] == test_classes
            assert by_name["yolo.onnx"]["classes"] == test_classes
