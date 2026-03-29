"""Tests for camera loss detection and degraded mode (issue #36)."""

from __future__ import annotations

import configparser
from unittest.mock import MagicMock, patch

from hydra_detect.autonomous import AutonomousController
from hydra_detect.pipeline import Pipeline


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

    p._cfg = cfg
    p._callsign = "HYDRA-1"
    p._camera = MagicMock()
    p._mavlink = MagicMock()
    p._mavlink.send_statustext = MagicMock()
    p._autonomous = AutonomousController(enabled=True)
    p._init_target_state()
    p._running = False
    p._cam_fail_count = 0
    p._cam_lost = False
    p._CAM_FAIL_THRESHOLD = 2
    p._event_logger = MagicMock()
    return p


import numpy as np

_FAKE_FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# AutonomousController suppression
# ---------------------------------------------------------------------------

class TestAutonomousSuppression:
    def test_suppressed_blocks_evaluate(self):
        ctrl = AutonomousController(enabled=True)
        ctrl.suppressed = True
        mavlink = MagicMock()
        # evaluate should return immediately without checking geofence etc.
        ctrl.evaluate([], mavlink, MagicMock(), MagicMock())
        # If it got past the guard it would call get_vehicle_mode — verify it didn't
        mavlink.get_vehicle_mode.assert_not_called()

    def test_unsuppressed_allows_evaluate(self):
        import time
        ctrl = AutonomousController(enabled=True, geofence_lat=1.0, geofence_lon=1.0)
        ctrl.suppressed = False
        mavlink = MagicMock()
        mavlink.get_vehicle_mode.return_value = "AUTO"
        mavlink.get_lat_lon.return_value = (1.0, 1.0, 0.0)
        mavlink.get_gps.return_value = {"last_update": time.monotonic(), "fix": 3}
        ctrl.evaluate([], mavlink, MagicMock(), MagicMock())
        # Should proceed past the guard and check vehicle mode
        mavlink.get_vehicle_mode.assert_called()

    def test_suppressed_independent_of_enabled(self):
        ctrl = AutonomousController(enabled=True)
        assert ctrl.suppressed is False
        ctrl.suppressed = True
        assert ctrl.enabled is True
        assert ctrl.suppressed is True


# ---------------------------------------------------------------------------
# Pipeline camera loss detection
# ---------------------------------------------------------------------------

class TestCameraLossDetection:
    def test_single_none_no_alert(self):
        """One None frame (below threshold) should not trigger cam lost."""
        p = _make_pipeline()
        p._camera.read.return_value = None
        result = p._check_camera_frame()
        assert result is None
        assert p._cam_lost is False
        assert p._cam_fail_count == 1
        p._mavlink.send_statustext.assert_not_called()

    def test_two_nones_triggers_cam_lost(self):
        """Two consecutive Nones should trigger camera lost alert."""
        p = _make_pipeline()
        p._camera.read.return_value = None
        p._check_camera_frame()
        p._check_camera_frame()
        assert p._cam_lost is True
        assert p._cam_fail_count == 2
        p._mavlink.send_statustext.assert_called_once_with(
            "HYDRA-1: CAM LOST", severity=4
        )

    def test_cam_lost_not_spammed(self):
        """After triggering, additional Nones should NOT send more alerts."""
        p = _make_pipeline()
        p._camera.read.return_value = None
        for _ in range(10):
            p._check_camera_frame()
        assert p._cam_lost is True
        assert p._cam_fail_count == 10
        # Still only one alert
        p._mavlink.send_statustext.assert_called_once()

    def test_frame_restores_cam_lost(self):
        """A valid frame after cam lost should clear the lost state."""
        p = _make_pipeline()
        # Trigger loss
        p._camera.read.return_value = None
        p._check_camera_frame()
        p._check_camera_frame()
        assert p._cam_lost is True

        # Restore
        p._camera.read.return_value = _FAKE_FRAME
        frame = p._check_camera_frame()
        assert frame is not None
        assert p._cam_lost is False
        assert p._cam_fail_count == 0

    def test_restore_sends_statustext(self):
        """Camera restore should send CAM RESTORED message."""
        p = _make_pipeline()
        p._camera.read.return_value = None
        p._check_camera_frame()
        p._check_camera_frame()
        p._mavlink.send_statustext.reset_mock()

        p._camera.read.return_value = _FAKE_FRAME
        p._check_camera_frame()
        p._mavlink.send_statustext.assert_called_once_with(
            "HYDRA-1: CAM RESTORED", severity=5
        )

    def test_autonomous_suppressed_on_loss(self):
        """Autonomous controller should be suppressed during camera loss."""
        p = _make_pipeline()
        assert p._autonomous.suppressed is False
        p._camera.read.return_value = None
        p._check_camera_frame()
        p._check_camera_frame()
        assert p._autonomous.suppressed is True

    def test_autonomous_restored_on_recovery(self):
        """Autonomous controller should be un-suppressed on camera recovery."""
        p = _make_pipeline()
        p._camera.read.return_value = None
        p._check_camera_frame()
        p._check_camera_frame()
        assert p._autonomous.suppressed is True

        p._camera.read.return_value = _FAKE_FRAME
        p._check_camera_frame()
        assert p._autonomous.suppressed is False

    def test_no_mavlink_no_crash(self):
        """Camera loss with no MAVLink should not raise."""
        p = _make_pipeline()
        p._mavlink = None
        p._camera.read.return_value = None
        p._check_camera_frame()
        p._check_camera_frame()
        assert p._cam_lost is True

    def test_no_autonomous_no_crash(self):
        """Camera loss with no autonomous controller should not raise."""
        p = _make_pipeline()
        p._autonomous = None
        p._camera.read.return_value = None
        p._check_camera_frame()
        p._check_camera_frame()
        assert p._cam_lost is True

    def test_intermittent_none_resets_count(self):
        """A valid frame between Nones should reset the failure counter."""
        p = _make_pipeline()
        # One None
        p._camera.read.return_value = None
        p._check_camera_frame()
        assert p._cam_fail_count == 1

        # Valid frame resets
        p._camera.read.return_value = _FAKE_FRAME
        p._check_camera_frame()
        assert p._cam_fail_count == 0
        assert p._cam_lost is False

        # One None again — still below threshold
        p._camera.read.return_value = None
        p._check_camera_frame()
        assert p._cam_fail_count == 1
        assert p._cam_lost is False
        p._mavlink.send_statustext.assert_not_called()
