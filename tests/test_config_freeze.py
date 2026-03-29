"""Tests for config freeze during active engagement."""

from __future__ import annotations

import configparser
from unittest.mock import patch

import pytest

from hydra_detect.web.config_api import (
    SAFETY_LOCKED_FIELDS,
    set_engagement_check,
    write_config,
)
from hydra_detect.autonomous import AutonomousController


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_engagement_cb():
    """Clear the engagement callback before and after each test."""
    set_engagement_check(None)
    yield
    set_engagement_check(None)


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config.ini with safety-critical and normal sections."""
    config = configparser.ConfigParser()
    config["camera"] = {"source": "auto", "width": "640", "height": "480"}
    config["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.45"}
    config["autonomous"] = {
        "enabled": "true",
        "min_confidence": "0.85",
        "geofence_lat": "34.05",
    }
    config["servo_tracking"] = {
        "enabled": "true",
        "strike_channel": "2",
        "strike_pwm_fire": "1900",
        "strike_pwm_safe": "1100",
        "pan_channel": "1",
        "pan_pwm_center": "1500",
    }
    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        config.write(f)
    return path


# ---------------------------------------------------------------------------
# Config freeze tests
# ---------------------------------------------------------------------------

class TestConfigFreezeDuringEngagement:
    def test_safety_fields_rejected_during_engagement(self, tmp_config):
        """Safety-locked fields are rejected when engagement is active."""
        set_engagement_check(lambda: True)

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            result = write_config({
                "autonomous": {"min_confidence": "0.50"},
            })

        assert len(result["locked"]) == 1
        assert "autonomous.min_confidence" in result["locked"][0]
        assert "active engagement" in result["locked"][0]
        # Also appears in skipped
        assert len(result["skipped"]) == 1

        # Verify the file was NOT changed
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["autonomous"]["min_confidence"] == "0.85"

    def test_servo_tracking_locked_fields_rejected(self, tmp_config):
        """Only the specific servo_tracking keys are locked."""
        set_engagement_check(lambda: True)

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            result = write_config({
                "servo_tracking": {
                    "strike_channel": "5",
                    "pan_pwm_center": "1600",
                },
            })

        # strike_channel is locked, pan_pwm_center is NOT locked
        assert len(result["locked"]) == 1
        assert "strike_channel" in result["locked"][0]

        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["servo_tracking"]["strike_channel"] == "2"  # unchanged
        assert config["servo_tracking"]["pan_pwm_center"] == "1600"  # changed

    def test_non_safety_fields_allowed_during_engagement(self, tmp_config):
        """Non-safety sections (camera, detector) are writable during engagement."""
        set_engagement_check(lambda: True)

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            result = write_config({
                "camera": {"width": "1280"},
                "detector": {"yolo_confidence": "0.60"},
            })

        assert result["locked"] == []

        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["width"] == "1280"
        assert config["detector"]["yolo_confidence"] == "0.60"

    def test_fields_allowed_when_no_engagement(self, tmp_config):
        """Safety fields are writable when no engagement is active."""
        set_engagement_check(lambda: False)

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            result = write_config({
                "autonomous": {"min_confidence": "0.50"},
                "servo_tracking": {"strike_channel": "5"},
            })

        assert result["locked"] == []

        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["autonomous"]["min_confidence"] == "0.50"
        assert config["servo_tracking"]["strike_channel"] == "5"

    def test_fields_allowed_when_no_callback_registered(self, tmp_config):
        """Without an engagement callback, all fields are writable."""
        # _reset_engagement_cb already clears it; just verify
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            result = write_config({
                "autonomous": {"min_confidence": "0.50"},
            })

        assert result["locked"] == []

        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["autonomous"]["min_confidence"] == "0.50"

    def test_locked_key_in_return_dict(self, tmp_config):
        """The return dict always contains a 'locked' key."""
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            result = write_config({"camera": {"width": "1280"}})
        assert "locked" in result

    def test_multiple_safety_fields_all_rejected(self, tmp_config):
        """Multiple safety fields in one request are all rejected."""
        set_engagement_check(lambda: True)

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            result = write_config({
                "autonomous": {
                    "min_confidence": "0.50",
                    "geofence_lat": "35.00",
                },
                "servo_tracking": {
                    "strike_pwm_fire": "1800",
                    "strike_pwm_safe": "1200",
                    "pan_channel": "3",
                },
            })

        # 2 from autonomous + 3 from servo_tracking = 5
        assert len(result["locked"]) == 5


# ---------------------------------------------------------------------------
# has_active_evaluation tests
# ---------------------------------------------------------------------------

import time


class TestHasActiveEvaluation:
    def test_no_tracks_returns_false(self):
        ctrl = AutonomousController(enabled=True)
        assert ctrl.has_active_evaluation() is False

    def test_active_track_returns_true(self):
        ctrl = AutonomousController(enabled=True)
        ctrl._persistence.counts[42] = 3
        ctrl._last_evaluate_time = time.monotonic()
        assert ctrl.has_active_evaluation() is True

    def test_zero_count_returns_false(self):
        ctrl = AutonomousController(enabled=True)
        ctrl._persistence.counts[42] = 0
        ctrl._last_evaluate_time = time.monotonic()
        assert ctrl.has_active_evaluation() is False

    def test_cleared_after_end_frame(self):
        ctrl = AutonomousController(enabled=True)
        ctrl._last_evaluate_time = time.monotonic()
        ctrl._persistence.begin_frame()
        ctrl._persistence.mark(1)
        ctrl._persistence.end_frame()
        assert ctrl.has_active_evaluation() is True
        ctrl._persistence.begin_frame()
        ctrl._persistence.end_frame()
        assert ctrl.has_active_evaluation() is False

    def test_stale_evaluate_returns_false(self):
        ctrl = AutonomousController(enabled=True)
        ctrl._persistence.counts[42] = 3
        ctrl._last_evaluate_time = time.monotonic() - 5.0
        assert ctrl.has_active_evaluation() is False

    def test_never_evaluated_returns_false(self):
        ctrl = AutonomousController(enabled=True)
        ctrl._persistence.counts[42] = 3
        assert ctrl.has_active_evaluation() is False
