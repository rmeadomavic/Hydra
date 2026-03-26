"""Tests for mission profile loading and validation."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from hydra_detect.profiles import get_profile, load_profiles


class TestLoadProfiles:
    def test_load_valid_profiles(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text(json.dumps({
            "default_profile": "general",
            "profiles": [
                {
                    "id": "general",
                    "name": "General",
                    "description": "Standard detection",
                    "model": "yolov8n.pt",
                    "confidence": 0.45,
                    "yolo_classes": [0, 1, 2],
                    "alert_classes": ["person", "car"],
                    "auto_loiter_on_detect": False,
                    "strike_distance_m": 20.0,
                },
            ],
        }))
        data = load_profiles(str(p))
        assert data["default_profile"] == "general"
        assert len(data["profiles"]) == 1
        assert data["profiles"][0]["id"] == "general"

    def test_load_missing_file_returns_empty(self):
        data = load_profiles("/nonexistent/profiles.json")
        assert data["profiles"] == []
        assert data["default_profile"] is None

    def test_load_malformed_json_returns_empty(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text("not valid json{{{")
        data = load_profiles(str(p))
        assert data["profiles"] == []

    def test_load_missing_required_field_skips_profile(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text(json.dumps({
            "default_profile": "good",
            "profiles": [
                {"id": "good", "name": "Good", "description": "ok",
                 "model": "m.pt", "confidence": 0.5, "yolo_classes": None,
                 "alert_classes": ["a"], "auto_loiter_on_detect": False,
                 "strike_distance_m": 10.0},
                {"id": "bad", "name": "Bad"},
            ],
        }))
        data = load_profiles(str(p))
        assert len(data["profiles"]) == 1
        assert data["profiles"][0]["id"] == "good"

    def test_null_yolo_classes_accepted(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text(json.dumps({
            "default_profile": "a",
            "profiles": [
                {"id": "a", "name": "A", "description": "d",
                 "model": "m.pt", "confidence": 0.5, "yolo_classes": None,
                 "alert_classes": [], "auto_loiter_on_detect": False,
                 "strike_distance_m": 10.0},
            ],
        }))
        data = load_profiles(str(p))
        assert data["profiles"][0]["yolo_classes"] is None


class TestGetProfile:
    def test_get_existing_profile(self):
        profiles = {
            "default_profile": "a",
            "profiles": [{"id": "a", "name": "A"}],
        }
        assert get_profile(profiles, "a")["id"] == "a"

    def test_get_nonexistent_returns_none(self):
        profiles = {"default_profile": "a", "profiles": [{"id": "a", "name": "A"}]}
        assert get_profile(profiles, "nope") is None
