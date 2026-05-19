"""Tests for issue #70: Fixed-wing detection-only profile.

Covers:
- Config: [vehicle.fw] resolves to min_track_frames=2 and CRUISE/LOITER/AUTO
  allowed_vehicle_modes after the dotted-key merge.
- Schema: [vehicle.fw] keys validate cleanly (no warnings on the FW profile).
- Pipeline gating: drop / follow / strike / pixel_lock are refused with a
  clear log + STATUSTEXT when ``_vehicle == "fw"``. Other vehicles and the
  no-vehicle baseline are unaffected.

Pipeline-import tests follow the same pattern as test_pipeline_callbacks.py
(monkey-patched __init__, no real subsystems). They collect only when the
hydra_detect.web.config_api module imports cleanly — i.e. on Linux. The
config-level tests here run on Windows too.
"""

from __future__ import annotations

import configparser
import sys
import tempfile

import pytest


# ── Config-level tests (no Pipeline import) ────────────────────────────────


def _base_ini_with_fw() -> str:
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read_dict({
        "camera": {
            "source_type": "auto", "source": "auto",
            "width": "640", "height": "480", "fps": "30",
        },
        "detector": {"yolo_model": "yolov8n.pt", "yolo_confidence": "0.45"},
        "tracker": {"track_thresh": "0.5", "track_buffer": "30", "match_thresh": "0.8"},
        "mavlink": {"enabled": "false"},
        "web": {"enabled": "false"},
        "logging": {"log_dir": "/tmp/hydra_test", "save_images": "false"},
        "tak": {"callsign": "HYDRA-1"},
        "autonomous": {
            "enabled": "false",
            "min_track_frames": "5",
            "allowed_vehicle_modes": "AUTO",
        },
        "vehicle.fw": {
            "reserved_channels": "1,2,3,4",
            "autonomous.post_action_mode": "LOITER",
            "autonomous.min_track_frames": "2",
            "autonomous.allowed_vehicle_modes": "AUTO,LOITER,CRUISE",
            "autonomous.platform_role": "aerial_isr",
            "autonomous.safe_mode": "LOITER",
            "autonomous.default_features": "detect,mavlink,tak_output,logging",
        },
    })
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ini", delete=False, encoding="utf-8",
    )
    cfg.write(tmp)
    tmp.close()
    return tmp.name


def _load_with_vehicle(vehicle: str | None) -> configparser.ConfigParser:
    """Run PipelineBootstrap.load_config and return the merged ConfigParser.

    PipelineBootstrap is import-safe on Windows — it does not touch fcntl.
    """
    from hydra_detect.pipeline.bootstrap import PipelineBootstrap
    bs = PipelineBootstrap()
    ctx = bs.load_config(_base_ini_with_fw(), vehicle=vehicle)
    return ctx.cfg


class TestFWConfigOverrides:
    def test_fw_lowers_min_track_frames_to_2(self):
        """FW profile drops the autonomy persistence threshold to 2 frames."""
        cfg = _load_with_vehicle("fw")
        assert cfg.getint("autonomous", "min_track_frames") == 2

    def test_fw_allowed_modes_restricted_to_autopilot_managed(self):
        """FW restricts allowed_vehicle_modes — no GUIDED/CIRCLE/etc."""
        cfg = _load_with_vehicle("fw")
        modes = [
            m.strip()
            for m in cfg.get("autonomous", "allowed_vehicle_modes").split(",")
            if m.strip()
        ]
        assert modes == ["AUTO", "LOITER", "CRUISE"]
        # Sanity: GUIDED is the close-engagement mode — must not be allowed.
        assert "GUIDED" not in modes

    def test_fw_post_action_mode_is_loiter(self):
        """FW collapses post_drop/post_strike into post_action_mode = LOITER."""
        cfg = _load_with_vehicle("fw")
        assert cfg.get("autonomous", "post_action_mode") == "LOITER"

    def test_fw_platform_role_aerial(self):
        cfg = _load_with_vehicle("fw")
        assert cfg.get("autonomous", "platform_role") == "aerial_isr"

    def test_fw_default_features_detect_and_tak_only(self):
        """FW default_features includes tak_output but no follow/strike features."""
        cfg = _load_with_vehicle("fw")
        feats = {
            f.strip()
            for f in cfg.get("autonomous", "default_features").split(",")
            if f.strip()
        }
        assert "detect" in feats
        assert "tak_output" in feats
        # No engagement features — no autonomous_strike, no follow, etc.
        assert "autonomous_strike" not in feats
        assert "follow" not in feats

    def test_fw_reserved_channels_preserved(self):
        """FW reserves 1-4 (typical airframe channel layout)."""
        from hydra_detect.pipeline.bootstrap import PipelineBootstrap
        bs = PipelineBootstrap()
        ctx = bs.load_config(_base_ini_with_fw(), vehicle="fw")
        raw = ctx.cfg.get("vehicle.fw", "reserved_channels", fallback="")
        assert raw.strip() == "1,2,3,4"

    def test_no_vehicle_baseline_unchanged(self):
        """Without --vehicle, autonomy keys keep their base values (regression)."""
        cfg = _load_with_vehicle(None)
        assert cfg.getint("autonomous", "min_track_frames") == 5
        assert cfg.get("autonomous", "allowed_vehicle_modes") == "AUTO"


class TestFWConfigSchema:
    """Schema tests — config_schema is fcntl-free, runs on Windows."""

    def test_vehicle_fw_validates_with_no_warnings(self):
        from hydra_detect.config_schema import validate_config
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.read("config.ini.factory")
        result = validate_config(cfg)
        # vehicle.fw entries (allowed_vehicle_modes, platform_role, etc.)
        # must all be recognised by the schema — no "unknown key" warnings
        # against [vehicle.fw].
        fw_warnings = [w for w in result.warnings if "[vehicle.fw]" in w]
        assert fw_warnings == [], f"unexpected vehicle.fw warnings: {fw_warnings}"
        assert result.errors == []

    def test_vehicle_fw_rejects_invalid_min_track_frames(self):
        """Schema catches a min_track_frames=0 typo."""
        from hydra_detect.config_schema import validate_config
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.add_section("vehicle.fw")
        cfg.set("vehicle.fw", "autonomous.min_track_frames", "0")
        result = validate_config(cfg)
        assert any(
            "min_track_frames" in e and "at least 1" in e
            for e in result.errors
        ), f"expected min_track_frames lower-bound error, got: {result.errors}"


# ── Pipeline-import tests (Linux-only via _skip_no_fcntl) ───────────────────

_skip_no_fcntl = pytest.mark.skipif(
    sys.platform == "win32",
    reason="hydra_detect.web.config_api requires fcntl (Linux-only)",
)


@_skip_no_fcntl
class TestFWPipelineRefusal:
    """FW profile must refuse follow/drop/strike/pixel-lock at the pipeline."""

    @staticmethod
    def _make_pipeline(vehicle):
        """Build a mocked Pipeline with ``_vehicle = vehicle``.

        Same pattern as tests/test_pipeline_callbacks.py: monkey-patch
        __init__ and wire only the state needed for these handlers.
        """
        from unittest.mock import MagicMock, patch
        from hydra_detect.pipeline import Pipeline
        from hydra_detect.tracker import TrackedObject, TrackingResult

        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.read_dict({
            "camera": {
                "source": "0", "width": "640", "height": "480", "fps": "30",
                "source_type": "digital",
            },
            "detector": {"yolo_model": "yolov8n.pt", "yolo_confidence": "0.45"},
            "tracker": {
                "track_thresh": "0.5", "track_buffer": "30", "match_thresh": "0.8",
            },
            "mavlink": {"enabled": "false"},
            "web": {"enabled": "false"},
            "logging": {"log_dir": "/tmp/hydra_test", "save_images": "false"},
        })

        with patch.object(Pipeline, "__init__", lambda self, *a, **kw: None):
            p = Pipeline.__new__(Pipeline)

        from hydra_detect.detectors.yolo_detector import YOLODetector

        p._cfg = cfg
        p._callsign = "HYDRA-FW"
        p._vehicle = vehicle
        p._detector = MagicMock(spec=YOLODetector)
        p._camera = MagicMock()
        p._camera.has_frame = True
        p._camera.width = 640
        p._camera.source_type = "digital"
        p._mavlink = MagicMock()
        p._mavlink.estimate_target_position.return_value = (34.05, -118.25)
        p._mavlink.command_guided_to.return_value = True
        p._approach = MagicMock()
        # Use a real-ish IDLE marker. ApproachMode.IDLE is the IDLE state.
        from hydra_detect.approach import ApproachMode
        p._approach.mode = ApproachMode.IDLE
        p._approach.start_drop.return_value = True
        p._approach.start_follow.return_value = True
        p._approach.start_strike.return_value = True
        p._approach.start_pixel_lock.return_value = True
        p._autonomous = None
        p._event_logger = MagicMock()
        p._drop_distance_m = 3.0
        p._strike_distance_m = 3.0
        p._servo_tracker = MagicMock()
        p._init_target_state()
        p._last_track_result = TrackingResult(
            tracks=[TrackedObject(
                track_id=3, x1=100, y1=100, x2=200, y2=200,
                confidence=0.9, class_id=0, label="person",
            )],
            active_ids=1,
        )
        p._running = False
        return p

    def test_fw_refuses_drop(self):
        p = self._make_pipeline("fw")
        assert p._handle_drop_command(3) is False
        p._approach.start_drop.assert_not_called()
        # No lock should be set on refusal
        assert p._locked_track_id is None

    def test_fw_refuses_follow(self):
        p = self._make_pipeline("fw")
        assert p._handle_follow_command(3) is False
        p._approach.start_follow.assert_not_called()
        assert p._locked_track_id is None

    def test_fw_refuses_strike(self):
        p = self._make_pipeline("fw")
        assert p._handle_approach_strike_command(3) is False
        p._approach.start_strike.assert_not_called()
        assert p._locked_track_id is None

    def test_fw_refuses_pixel_lock(self):
        p = self._make_pipeline("fw")
        assert p._handle_pixel_lock_command(3) is False
        p._approach.start_pixel_lock.assert_not_called()
        assert p._locked_track_id is None

    def test_fw_refuses_strike_command(self):
        """Regression for issue #246: _handle_strike_command also bypasses GUIDED.

        Sibling handlers (_handle_drop_command, _handle_follow_command,
        _handle_approach_strike_command, _handle_pixel_lock_command) all carry
        the FW guard; _handle_strike_command was missing it and could still
        command GUIDED nav + fire the strike servo on fixed-wing.
        """
        p = self._make_pipeline("fw")
        assert p._handle_strike_command(3) is False
        # No GUIDED command should reach mavlink.
        p._mavlink.command_guided_to.assert_not_called()
        # Strike servo must not fire on refusal.
        p._servo_tracker.fire_strike.assert_not_called()
        # No lock should be set on refusal.
        assert p._locked_track_id is None

    def test_fw_strike_command_refusal_emits_audit_log(self, caplog):
        """Strike-command refusal lands on hydra.audit, same as siblings."""
        import logging
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            p = self._make_pipeline("fw")
            p._handle_strike_command(3)
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "FW_PROFILE_REFUSED" in joined
        assert "strike" in joined

    def test_drone_profile_still_allows_strike_command(self):
        """Regression: drone profile is unaffected — strike command still runs."""
        p = self._make_pipeline("drone")
        assert p._handle_strike_command(3) is True
        p._mavlink.command_guided_to.assert_called_once()
        p._servo_tracker.fire_strike.assert_called_once()

    def test_fw_refusal_emits_statustext(self):
        """Operator gets a STATUSTEXT explaining the refusal — not silent."""
        p = self._make_pipeline("fw")
        p._handle_follow_command(3)
        p._mavlink.send_statustext.assert_called()
        msg = p._mavlink.send_statustext.call_args[0][0]
        assert "FW" in msg
        assert "FOLLOW" in msg

    def test_fw_refusal_emits_audit_log(self, caplog):
        """Refusal is recorded on the hydra.audit logger for after-action review."""
        import logging
        with caplog.at_level(logging.INFO, logger="hydra.audit"):
            p = self._make_pipeline("fw")
            p._handle_drop_command(3)
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "FW_PROFILE_REFUSED" in joined
        assert "drop" in joined

    def test_drone_profile_still_allows_drop(self):
        """Regression: drone profile is unaffected by the FW gate."""
        p = self._make_pipeline("drone")
        assert p._handle_drop_command(3) is True
        p._approach.start_drop.assert_called_once_with(3, 34.05, -118.25)

    def test_no_vehicle_still_allows_follow(self):
        """Regression: vehicle=None (legacy/unset) is unaffected."""
        p = self._make_pipeline(None)
        assert p._handle_follow_command(3) is True
        p._approach.start_follow.assert_called_once_with(3)

    def test_ugv_profile_still_allows_strike(self):
        """Regression: UGV is an effector platform — strike must still work."""
        p = self._make_pipeline("ugv")
        assert p._handle_approach_strike_command(3) is True
        p._approach.start_strike.assert_called_once_with(3)

    def test_fw_refusal_does_not_require_mavlink(self):
        """If mavlink is None we still refuse cleanly (no statustext attempt)."""
        p = self._make_pipeline("fw")
        p._mavlink = None
        assert p._handle_follow_command(3) is False
        # No crash — refusal path tolerates missing mavlink.

    def test_fw_case_insensitive(self):
        """Profile name 'FW' (any casing) is recognised."""
        p = self._make_pipeline("FW")
        assert p._handle_drop_command(3) is False


@_skip_no_fcntl
class TestFWHelpers:
    """Direct tests for the FW gating helpers."""

    def test_is_fw_profile_true(self):
        from hydra_detect.pipeline.facade import _is_fw_profile
        assert _is_fw_profile("fw") is True
        assert _is_fw_profile("FW") is True
        assert _is_fw_profile("  fw  ") is True

    def test_is_fw_profile_false(self):
        from hydra_detect.pipeline.facade import _is_fw_profile
        assert _is_fw_profile(None) is False
        assert _is_fw_profile("") is False
        assert _is_fw_profile("drone") is False
        assert _is_fw_profile("usv") is False
        assert _is_fw_profile("ugv") is False

    def test_forbidden_modes_set(self):
        from hydra_detect.pipeline.facade import _FW_FORBIDDEN_APPROACH_MODES
        assert _FW_FORBIDDEN_APPROACH_MODES == frozenset(
            {"follow", "drop", "strike", "pixel_lock"}
        )
