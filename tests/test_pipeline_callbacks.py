"""Integration tests for pipeline runtime callbacks (threshold, lock, strike)."""

from __future__ import annotations

import configparser
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from hydra_detect.detectors.base import DetectionResult
from hydra_detect.detectors.yolo_detector import YOLODetector
from hydra_detect.pipeline import Pipeline
from hydra_detect.tracker import TrackedObject, TrackingResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline(**overrides) -> Pipeline:
    """Build a Pipeline with mocked subsystems (no real camera/detector/MAVLink)."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read_dict({
        "camera": {"source": "0", "width": "640", "height": "480", "fps": "30"},
        "detector": {"yolo_model": "yolov8n.pt", "yolo_confidence": "0.45"},
        "tracker": {"track_thresh": "0.5", "track_buffer": "30", "match_thresh": "0.8"},
        "mavlink": {"enabled": "false"},
        "web": {"enabled": "false"},
        "logging": {"log_dir": "/tmp/hydra_test", "save_images": "false"},
    })
    cfg.read_dict(overrides)

    with patch.object(Pipeline, "__init__", lambda self, *a, **kw: None):
        p = Pipeline.__new__(Pipeline)

    # Wire minimal internal state
    p._cfg = cfg
    p._detector = MagicMock(spec=YOLODetector)
    p._camera = MagicMock()
    p._camera.has_frame = True
    p._camera.width = 640
    p._mavlink = None
    p._init_target_state()
    p._running = False
    return p


def _sample_track(track_id: int = 1, label: str = "person") -> TrackingResult:
    return TrackingResult(
        tracks=[TrackedObject(track_id=track_id, x1=100, y1=100, x2=200, y2=200,
                              confidence=0.9, class_id=0, label=label)],
        active_ids=1,
    )


# ---------------------------------------------------------------------------
# Threshold change
# ---------------------------------------------------------------------------

class TestThresholdChange:
    def test_yolo_threshold_update(self):
        p = _make_pipeline()
        p._detector = YOLODetector(confidence=0.45)
        p._handle_threshold_change(0.7)
        assert p._detector.get_threshold() == 0.7


# ---------------------------------------------------------------------------
# Target lock / unlock
# ---------------------------------------------------------------------------

class TestTargetLock:
    def test_lock_valid_track(self):
        p = _make_pipeline()
        p._last_track_result = _sample_track(track_id=5)
        assert p._handle_target_lock(5) is True
        assert p._locked_track_id == 5
        assert p._lock_mode == "track"

    def test_lock_invalid_track(self):
        p = _make_pipeline()
        p._last_track_result = _sample_track(track_id=5)
        assert p._handle_target_lock(999) is False
        assert p._locked_track_id is None

    def test_lock_no_tracks(self):
        p = _make_pipeline()
        p._last_track_result = None
        assert p._handle_target_lock(1) is False

    def test_unlock(self):
        p = _make_pipeline()
        p._locked_track_id = 5
        p._lock_mode = "track"
        p._handle_target_unlock()
        assert p._locked_track_id is None
        assert p._lock_mode is None


# ---------------------------------------------------------------------------
# Strike command
# ---------------------------------------------------------------------------

class TestStrikeCommand:
    def test_strike_no_mavlink(self):
        """Without MAVLink, strike sets visual lock and returns True (visual-only mode)."""
        p = _make_pipeline()
        p._last_track_result = _sample_track(track_id=3)
        assert p._handle_strike_command(3) is True
        assert p._locked_track_id == 3
        assert p._lock_mode == "strike"

    def test_strike_no_tracks(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._last_track_result = None
        assert p._handle_strike_command(1) is False

    def test_strike_track_not_found(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._last_track_result = _sample_track(track_id=3)
        assert p._handle_strike_command(999) is False

    def test_strike_no_gps(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._mavlink.estimate_target_position.return_value = None
        p._last_track_result = _sample_track(track_id=3)
        assert p._handle_strike_command(3) is False

    def test_strike_success(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._mavlink.estimate_target_position.return_value = (34.0, -118.0)
        p._mavlink.command_guided_to.return_value = True
        p._last_track_result = _sample_track(track_id=3)

        assert p._handle_strike_command(3) is True
        assert p._locked_track_id == 3
        assert p._lock_mode == "strike"
        p._mavlink.command_guided_to.assert_called_once_with(34.0, -118.0)


# ---------------------------------------------------------------------------
# Active tracks API helper
# ---------------------------------------------------------------------------

class TestActiveTracks:
    def test_no_tracks(self):
        p = _make_pipeline()
        assert p._get_active_tracks() == []

    def test_with_tracks(self):
        p = _make_pipeline()
        p._last_track_result = _sample_track(track_id=7, label="vehicle")
        tracks = p._get_active_tracks()
        assert len(tracks) == 1
        assert tracks[0]["track_id"] == 7
        assert tracks[0]["label"] == "vehicle"


# ---------------------------------------------------------------------------
# RTSP toggle / status
# ---------------------------------------------------------------------------

class TestRTSPCallbacks:
    def test_rtsp_status_when_disabled(self):
        p = _make_pipeline()
        p._rtsp = None
        p._rtsp_enabled = False
        p._rtsp_port = 8554
        p._rtsp_mount = "/hydra"
        status = p._get_rtsp_status()
        assert status["enabled"] is False
        assert status["running"] is False

    def test_rtsp_status_when_running(self):
        p = _make_pipeline()
        p._rtsp = MagicMock()
        p._rtsp.running = True
        p._rtsp.url = "rtsp://0.0.0.0:8554/hydra"
        p._rtsp.client_count = 2
        p._rtsp_enabled = True
        p._rtsp_port = 8554
        p._rtsp_mount = "/hydra"
        status = p._get_rtsp_status()
        assert status["running"] is True
        assert status["clients"] == 2

    def test_rtsp_toggle_off(self):
        p = _make_pipeline()
        p._rtsp = MagicMock()
        p._rtsp.running = True
        p._rtsp_enabled = True
        p._rtsp_port = 8554
        p._rtsp_mount = "/hydra"
        p._rtsp_bitrate = 2_000_000
        result = p._handle_rtsp_toggle(False)
        assert result["status"] == "ok"
        assert result["running"] is False
        assert p._rtsp is None
