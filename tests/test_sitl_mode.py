"""Tests for --sim (SITL simulation mode) and --camera-source CLI flags."""

from __future__ import annotations

import configparser

from hydra_detect.__main__ import _apply_sim_overrides, _apply_camera_source_override


def _base_cfg() -> configparser.ConfigParser:
    """Return a minimal config with required sections."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read_dict({
        "camera": {"source_type": "auto", "source": "auto"},
        "mavlink": {
            "enabled": "false",
            "connection_string": "/dev/ttyTHS1",
            "baud": "921600",
            "sim_gps_lat": "",
            "sim_gps_lon": "",
        },
        "osd": {"enabled": "true"},
        "servo_tracking": {"enabled": "true"},
        "rf_homing": {"enabled": "true"},
    })
    return cfg


class TestSimMode:
    def test_sim_sets_camera_to_file(self):
        cfg = _base_cfg()
        _apply_sim_overrides(cfg)
        assert cfg.get("camera", "source_type") == "file"
        assert cfg.get("camera", "source") == "sim_video.mp4"

    def test_sim_sets_mavlink_udp(self):
        cfg = _base_cfg()
        _apply_sim_overrides(cfg)
        assert cfg.get("mavlink", "enabled") == "true"
        assert cfg.get("mavlink", "connection_string") == "udp:127.0.0.1:14550"
        assert cfg.get("mavlink", "baud") == "115200"

    def test_sim_sets_default_gps(self):
        cfg = _base_cfg()
        _apply_sim_overrides(cfg)
        assert cfg.get("mavlink", "sim_gps_lat") == "35.0527"
        assert cfg.get("mavlink", "sim_gps_lon") == "-79.4927"

    def test_sim_preserves_existing_gps(self):
        cfg = _base_cfg()
        cfg.set("mavlink", "sim_gps_lat", "40.0")
        cfg.set("mavlink", "sim_gps_lon", "-80.0")
        _apply_sim_overrides(cfg)
        assert cfg.get("mavlink", "sim_gps_lat") == "40.0"
        assert cfg.get("mavlink", "sim_gps_lon") == "-80.0"

    def test_sim_disables_osd(self):
        cfg = _base_cfg()
        _apply_sim_overrides(cfg)
        assert cfg.get("osd", "enabled") == "false"

    def test_sim_disables_servo_tracking(self):
        cfg = _base_cfg()
        _apply_sim_overrides(cfg)
        assert cfg.get("servo_tracking", "enabled") == "false"

    def test_sim_disables_rf_homing(self):
        cfg = _base_cfg()
        _apply_sim_overrides(cfg)
        assert cfg.get("rf_homing", "enabled") == "false"

    def test_sim_works_without_hardware_sections(self):
        """Sim mode should not crash if osd/servo/rf sections are missing."""
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.read_dict({
            "camera": {"source_type": "auto", "source": "auto"},
            "mavlink": {
                "enabled": "false",
                "connection_string": "/dev/ttyTHS1",
                "baud": "921600",
                "sim_gps_lat": "",
                "sim_gps_lon": "",
            },
        })
        _apply_sim_overrides(cfg)
        assert cfg.get("camera", "source_type") == "file"
        assert cfg.get("mavlink", "connection_string") == "udp:127.0.0.1:14550"


class TestCameraSourceOverride:
    def test_webcam_digit(self):
        cfg = _base_cfg()
        _apply_camera_source_override(cfg, "0")
        assert cfg.get("camera", "source") == "0"
        assert cfg.get("camera", "source_type") == "usb"

    def test_video_file_mp4(self):
        cfg = _base_cfg()
        _apply_camera_source_override(cfg, "test_video.mp4")
        assert cfg.get("camera", "source") == "test_video.mp4"
        assert cfg.get("camera", "source_type") == "file"

    def test_video_file_avi(self):
        cfg = _base_cfg()
        _apply_camera_source_override(cfg, "recording.avi")
        assert cfg.get("camera", "source") == "recording.avi"
        assert cfg.get("camera", "source_type") == "file"

    def test_video_file_mkv(self):
        cfg = _base_cfg()
        _apply_camera_source_override(cfg, "footage.mkv")
        assert cfg.get("camera", "source") == "footage.mkv"
        assert cfg.get("camera", "source_type") == "file"

    def test_video_file_mov(self):
        cfg = _base_cfg()
        _apply_camera_source_override(cfg, "clip.mov")
        assert cfg.get("camera", "source") == "clip.mov"
        assert cfg.get("camera", "source_type") == "file"

    def test_rtsp_stream(self):
        cfg = _base_cfg()
        _apply_camera_source_override(cfg, "rtsp://192.168.1.10:554/stream")
        assert cfg.get("camera", "source") == "rtsp://192.168.1.10:554/stream"
        assert cfg.get("camera", "source_type") == "rtsp"

    def test_camera_source_overrides_sim_default(self):
        """--camera-source should override the --sim default file source."""
        cfg = _base_cfg()
        _apply_sim_overrides(cfg)
        assert cfg.get("camera", "source") == "sim_video.mp4"
        # Now apply camera-source override (like CLI ordering: --sim then --camera-source)
        _apply_camera_source_override(cfg, "0")
        assert cfg.get("camera", "source") == "0"
        assert cfg.get("camera", "source_type") == "usb"
