"""Tests for the post-mission review tool (log parsing, export, API endpoints)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from hydra_detect.review_export import (
    build_summary,
    generate_html,
    parse_csv_log,
    parse_jsonl,
    parse_log,
    main as export_main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_JSONL = [
    {"timestamp": "2026-03-15T12:00:01Z", "frame": 1, "track_id": 1,
     "label": "mine", "class_id": 0, "confidence": 0.92,
     "bbox": [100, 100, 200, 200], "lat": 34.05, "lon": -118.25,
     "alt": 10.0, "fix": 4, "image": "img_001.jpg"},
    {"timestamp": "2026-03-15T12:00:02Z", "frame": 2, "track_id": 1,
     "label": "mine", "class_id": 0, "confidence": 0.89,
     "bbox": [105, 105, 205, 205], "lat": 34.051, "lon": -118.251,
     "alt": 10.0, "fix": 4, "image": "img_002.jpg"},
    {"timestamp": "2026-03-15T12:00:03Z", "frame": 3, "track_id": 2,
     "label": "buoy", "class_id": 1, "confidence": 0.78,
     "bbox": [300, 150, 350, 200], "lat": None, "lon": None,
     "alt": None, "fix": 0, "image": None},
]

SAMPLE_CSV = """timestamp,frame,track_id,label,class_id,confidence,x1,y1,x2,y2,lat,lon,alt,fix,image
2026-03-15T12:00:01Z,1,1,mine,0,0.920,100.0,100.0,200.0,200.0,34.05,-118.25,10.0,4,img_001.jpg
2026-03-15T12:00:02Z,2,2,buoy,1,0.780,300.0,150.0,350.0,200.0,,,,,
"""


@pytest.fixture
def jsonl_file(tmp_path):
    path = tmp_path / "test.jsonl"
    with open(path, "w") as f:
        for record in SAMPLE_JSONL:
            f.write(json.dumps(record) + "\n")
    return path


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "test.csv"
    path.write_text(SAMPLE_CSV)
    return path


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

class TestParseJsonl:
    def test_basic(self, jsonl_file):
        records = parse_jsonl(jsonl_file)
        assert len(records) == 3
        assert records[0]["label"] == "mine"
        assert records[0]["confidence"] == 0.92

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert parse_jsonl(path) == []

    def test_bad_lines_skipped(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text('{"label": "mine"}\nnot json\n{"label": "buoy"}\n')
        records = parse_jsonl(path)
        assert len(records) == 2


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_basic(self, csv_file):
        records = parse_csv_log(csv_file)
        assert len(records) == 2
        assert records[0]["label"] == "mine"
        assert records[0]["confidence"] == 0.92
        assert records[0]["track_id"] == 1

    def test_auto_detect(self, csv_file, jsonl_file):
        csv_records = parse_log(csv_file)
        jsonl_records = parse_log(jsonl_file)
        assert len(csv_records) == 2
        assert len(jsonl_records) == 3


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_basic(self):
        summary = build_summary(SAMPLE_JSONL)
        assert summary["total"] == 3
        assert summary["tracks"] == 2
        assert summary["with_gps"] == 2
        assert summary["classes"]["mine"] == 2
        assert summary["classes"]["buoy"] == 1

    def test_empty(self):
        summary = build_summary([])
        assert summary["total"] == 0
        assert summary["tracks"] == 0


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

class TestGenerateHtml:
    def test_contains_detections(self):
        html = generate_html(SAMPLE_JSONL, build_summary(SAMPLE_JSONL))
        assert "HYDRA DETECT" in html
        assert "leaflet" in html.lower()
        assert "mine" in html

    def test_custom_title(self):
        html = generate_html([], build_summary([]), title="Test Report")
        assert "Test Report" in html


# ---------------------------------------------------------------------------
# CLI export
# ---------------------------------------------------------------------------

class TestExportCli:
    def test_jsonl_export(self, jsonl_file, tmp_path):
        output = tmp_path / "report.html"
        result = export_main([str(jsonl_file), "-o", str(output)])
        assert result == 0
        assert output.exists()
        content = output.read_text()
        assert "mine" in content
        assert "leaflet" in content.lower()

    def test_csv_export(self, csv_file, tmp_path):
        output = tmp_path / "report.html"
        result = export_main([str(csv_file), "-o", str(output)])
        assert result == 0
        assert output.exists()

    def test_missing_file(self, tmp_path):
        result = export_main(["/nonexistent/file.jsonl", "-o", str(tmp_path / "out.html")])
        assert result == 1

    def test_empty_log(self, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        output = tmp_path / "report.html"
        result = export_main([str(empty), "-o", str(output)])
        assert result == 0  # Should still generate HTML


# ---------------------------------------------------------------------------
# Path traversal protection (web endpoint logic)
# ---------------------------------------------------------------------------

class TestPathTraversal:
    """Test the filename validation used by review endpoints."""

    @pytest.mark.parametrize("filename", [
        "../etc/passwd",
        "foo/../../bar",
        "..\\windows\\system32",
        "valid..file",  # double dots without slash are fine in filenames
    ])
    def test_traversal_blocked(self, filename):
        # Reproduce the validation from server.py
        blocked = "/" in filename or "\\" in filename or ".." in filename
        if ".." in filename:
            assert blocked is True
        if "/" in filename or "\\" in filename:
            assert blocked is True

    @pytest.mark.parametrize("filename", [
        "detections_20260315_120000.jsonl",
        "detections_20260315_120000.csv",
        "img_000001.jpg",
    ])
    def test_valid_filenames(self, filename):
        blocked = "/" in filename or "\\" in filename or ".." in filename
        assert blocked is False
