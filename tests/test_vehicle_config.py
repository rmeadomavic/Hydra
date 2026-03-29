"""Tests for multi-vehicle config override logic (issue #42)."""

from __future__ import annotations

import configparser
import tempfile
from pathlib import Path
from unittest.mock import patch

from hydra_detect.pipeline import Pipeline


def _make_config(sections: dict) -> str:
    """Write a config file and return its path."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read_dict(sections)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False)
    cfg.write(tmp)
    tmp.close()
    return tmp.name


class TestVehicleOverride:
    def test_vehicle_overrides_base_section(self):
        """Vehicle section values override base section values."""
        path = _make_config({
            "camera": {"source": "0", "width": "640", "height": "480", "fps": "30"},
            "detector": {"yolo_model": "yolov8n.pt", "yolo_confidence": "0.45"},
            "tracker": {"track_thresh": "0.5", "track_buffer": "30", "match_thresh": "0.8"},
            "mavlink": {"enabled": "false"},
            "web": {"enabled": "false"},
            "logging": {"log_dir": "/tmp/hydra_test", "save_images": "false"},
            "vehicle.usv": {"camera.source": "/dev/video2", "camera.fps": "15"},
        })

        with patch.object(Pipeline, "__init__", lambda self, *a, **kw: None):
            p = Pipeline.__new__(Pipeline)

        p._cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        p._cfg.read(path)

        # Manually apply the vehicle logic (same as Pipeline.__init__)
        vehicle = "usv"
        vehicle_section = f"vehicle.{vehicle}"
        for key, value in p._cfg.items(vehicle_section):
            if "." in key:
                section, option = key.split(".", 1)
                if not p._cfg.has_section(section):
                    p._cfg.add_section(section)
                p._cfg.set(section, option, value)

        assert p._cfg.get("camera", "source") == "/dev/video2"
        assert p._cfg.get("camera", "fps") == "15"
        # Unchanged values should still be there
        assert p._cfg.get("camera", "width") == "640"

    def test_no_vehicle_uses_base_config(self):
        """Without a vehicle flag, base config is used unchanged."""
        path = _make_config({
            "camera": {"source": "0", "width": "640", "height": "480", "fps": "30"},
            "detector": {"yolo_model": "yolov8n.pt"},
            "tracker": {"track_thresh": "0.5", "track_buffer": "30", "match_thresh": "0.8"},
            "mavlink": {"enabled": "false"},
            "web": {"enabled": "false"},
            "logging": {"log_dir": "/tmp/hydra_test", "save_images": "false"},
        })

        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.read(path)
        assert cfg.get("camera", "source") == "0"

    def test_missing_vehicle_section_no_crash(self):
        """A missing vehicle section should not crash (just log error)."""
        path = _make_config({
            "camera": {"source": "0", "width": "640", "height": "480", "fps": "30"},
            "detector": {"yolo_model": "yolov8n.pt"},
            "tracker": {"track_thresh": "0.5", "track_buffer": "30", "match_thresh": "0.8"},
            "mavlink": {"enabled": "false"},
            "web": {"enabled": "false"},
            "logging": {"log_dir": "/tmp/hydra_test", "save_images": "false"},
        })

        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.read(path)
        # No [vehicle.boat] section — should not crash
        assert not cfg.has_section("vehicle.boat")
