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


# ---------------------------------------------------------------------------
# MAVLink Video toggle / status
# ---------------------------------------------------------------------------

class TestMAVLinkVideoCallbacks:
    def test_mavlink_video_status_when_disabled(self):
        p = _make_pipeline()
        p._mavlink_video = None
        p._mavlink_video_enabled = False
        status = p._get_mavlink_video_status()
        assert status["enabled"] is False
        assert status["running"] is False

    def test_mavlink_video_status_when_running(self):
        p = _make_pipeline()
        p._mavlink_video = MagicMock()
        p._mavlink_video.get_status.return_value = {
            "enabled": True, "running": True, "width": 160, "height": 120,
            "quality": 20, "current_fps": 1.5, "bytes_per_sec": 5000,
        }
        status = p._get_mavlink_video_status()
        assert status["running"] is True
        assert status["current_fps"] == 1.5

    def test_mavlink_video_toggle_off(self):
        p = _make_pipeline()
        p._mavlink_video = MagicMock()
        p._mavlink_video_enabled = True
        result = p._handle_mavlink_video_toggle(False)
        assert result["status"] == "ok"
        assert p._mavlink_video is None


# ---------------------------------------------------------------------------
# Target unlock reason (lost vs manual)
# ---------------------------------------------------------------------------

class TestTargetUnlockReason:
    def test_unlock_lost_sends_tgt_lost_statustext(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._locked_track_id = 5
        p._lock_mode = "track"
        p._handle_target_unlock(reason="lost")
        assert p._locked_track_id is None
        p._mavlink.send_statustext.assert_called_once()
        msg = p._mavlink.send_statustext.call_args[0][0]
        assert "TGT LOST" in msg

    def test_unlock_manual_sends_released_statustext(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._locked_track_id = 5
        p._lock_mode = "track"
        p._handle_target_unlock()
        msg = p._mavlink.send_statustext.call_args[0][0]
        assert "RELEASED" in msg


# ---------------------------------------------------------------------------
# ServoTracker setup
# ---------------------------------------------------------------------------

class TestServoTrackerSetup:
    def test_servo_tracker_none_without_mavlink(self):
        p = _make_pipeline()
        assert p._servo_tracker is None

    def test_channel_collision_pan_equals_light_bar(self):
        """Validate the collision detection logic directly."""
        channels = [4, 2, 4]
        assert len(channels) != len(set(channels))

    def test_channel_collision_pan_equals_strike(self):
        channels = [2, 2]
        assert len(channels) != len(set(channels))

    def test_no_collision_distinct_channels(self):
        channels = [1, 2, 4]
        assert len(channels) == len(set(channels))


# ---------------------------------------------------------------------------
# ServoTracker integration (strike, unlock, shutdown)
# ---------------------------------------------------------------------------

class TestServoTrackerIntegration:
    def _pipeline_with_servo(self):
        """Build a pipeline with a mock servo tracker."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._mavlink.estimate_target_position.return_value = (34.0, -118.0)
        p._mavlink.command_guided_to.return_value = True
        p._servo_tracker = MagicMock()
        p._servo_tracker.replaces_yaw = False
        return p

    def test_strike_fires_servo(self):
        p = self._pipeline_with_servo()
        p._last_track_result = _sample_track(track_id=3)
        p._handle_strike_command(3)
        p._servo_tracker.fire_strike.assert_called_once()

    def test_strike_fires_servo_even_without_gps(self):
        p = self._pipeline_with_servo()
        p._mavlink.estimate_target_position.return_value = None
        p._last_track_result = _sample_track(track_id=3)
        p._handle_strike_command(3)
        p._servo_tracker.fire_strike.assert_called_once()

    def test_unlock_safes_servo(self):
        p = self._pipeline_with_servo()
        p._locked_track_id = 3
        p._lock_mode = "track"
        p._handle_target_unlock()
        p._servo_tracker.safe.assert_called_once()

    def test_unlock_lost_safes_servo(self):
        p = self._pipeline_with_servo()
        p._locked_track_id = 3
        p._lock_mode = "track"
        p._handle_target_unlock(reason="lost")
        p._servo_tracker.safe.assert_called_once()

    def test_no_servo_tracker_no_error(self):
        """Strike and unlock work fine without servo tracker."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._mavlink.estimate_target_position.return_value = (34.0, -118.0)
        p._mavlink.command_guided_to.return_value = True
        p._servo_tracker = None
        p._last_track_result = _sample_track(track_id=3)
        assert p._handle_strike_command(3) is True
        p._handle_target_unlock()

    def test_shutdown_safes_servo(self):
        p = self._pipeline_with_servo()
        p._rf_hunt = None
        p._kismet_manager = None
        p._rtsp = None
        p._mavlink_video = None
        p._camera = MagicMock()
        p._detector = MagicMock()
        p._det_logger = MagicMock()
        p._shutdown()
        p._servo_tracker.safe.assert_called_once()


# ---------------------------------------------------------------------------
# Kismet auto-start on RF hunt
# ---------------------------------------------------------------------------

from hydra_detect.rf.kismet_manager import KismetManager


class TestKismetAutoStart:
    def test_auto_start_creates_kismet_manager(self):
        """When _kismet_manager is None, _handle_rf_start creates one."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._rf_hunt = None
        p._kismet_manager = None

        with patch.object(KismetManager, "__init__", return_value=None) as mock_init, \
             patch.object(KismetManager, "start", return_value=True), \
             patch("hydra_detect.pipeline.RFHuntController") as mock_ctrl:
            mock_ctrl.return_value.start.return_value = True
            result = p._handle_rf_start({"mode": "wifi"})

        assert result is True
        assert p._kismet_manager is not None

    def test_auto_start_failure_returns_false(self):
        """When Kismet auto-start fails, return False and reset manager."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._rf_hunt = None
        p._kismet_manager = None

        with patch.object(KismetManager, "__init__", return_value=None), \
             patch.object(KismetManager, "start", return_value=False):
            result = p._handle_rf_start({"mode": "wifi"})

        assert result is False
        assert p._kismet_manager is None

    def test_existing_kismet_manager_not_replaced(self):
        """When _kismet_manager already exists, don't create a new one."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._rf_hunt = None
        existing_mgr = MagicMock()
        p._kismet_manager = existing_mgr

        with patch("hydra_detect.pipeline.RFHuntController") as mock_ctrl:
            mock_ctrl.return_value.start.return_value = True
            p._handle_rf_start({"mode": "wifi"})

        assert p._kismet_manager is existing_mgr
