"""Regression coverage for the committed UGV strike vehicle profile."""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hydra_detect.approach import ApproachMode
from hydra_detect.capability_status import (
    CapabilityStatus,
    SystemState,
    _eval_autonomy_live,
    _eval_follow,
)
from hydra_detect.config_schema import validate_config
from hydra_detect.pipeline.bootstrap import PipelineBootstrap


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config.ini"

_skip_no_fcntl = pytest.mark.skipif(
    sys.platform == "win32",
    reason="hydra_detect.web.config_api requires fcntl (Linux-only)",
)


def _read_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(CONFIG_PATH)
    return cfg


def test_ugv_strike_profile_passes_schema_validation():
    result = validate_config(_read_config())
    profile_messages = [
        message for message in (*result.errors, *result.warnings)
        if "vehicle.ugv_strike" in message
    ]
    assert profile_messages == []


def test_ugv_strike_profile_resolves_dotted_overrides(monkeypatch):
    monkeypatch.setenv("HYDRA_VEHICLE", "ugv_strike")
    selected = os.environ["HYDRA_VEHICLE"]
    ctx = PipelineBootstrap().load_config(str(CONFIG_PATH), vehicle=selected)

    assert ctx.cfg.getboolean("autonomous", "enabled") is True
    assert ctx.cfg.getfloat("autonomous", "geofence_lat") == 35.15
    assert ctx.cfg.getfloat("autonomous", "geofence_lon") == -79.52
    assert ctx.cfg.getfloat("autonomous", "geofence_radius_m") == 25000
    assert ctx.cfg.get("autonomous", "allowed_classes") == "truck, car"
    assert ctx.cfg.get("autonomous", "allowed_vehicle_modes") == "GUIDED"
    assert ctx.cfg.getboolean("autonomous", "require_operator_lock") is True
    assert ctx.cfg.getint("autonomous", "arm_channel") == 0
    assert ctx.cfg.getint("autonomous", "hardware_arm_channel") == 0
    assert ctx.cfg.get("autonomous", "post_strike_mode") == "HOLD"
    assert ctx.cfg.get("autonomous", "safe_mode") == "HOLD"
    assert ctx.cfg.get("autonomous", "platform_role") == "ground_isr"
    assert ctx.cfg.getboolean("servo_tracking", "enabled") is True
    assert ctx.cfg.getfloat("approach", "strike_approach_m") == 0.5
    assert ctx.cfg.get("approach", "abort_mode") == "HOLD"
    assert ctx.cfg.getfloat("approach", "waypoint_interval") == 0.1


def test_ugv_strike_reserves_the_same_channels_as_ugv():
    """reserved_channels must be declared explicitly on this profile.

    Vehicle profiles do not inherit: bootstrap.py reads only the single
    [vehicle.<name>] section. reserved_channels is a vehicle-LOCAL key, and
    facade.py reads it with fallback="" — so if it is missing, the servo and
    light-bar channel-conflict check is skipped entirely rather than failing
    loudly. On the truck, 1 and 3 are steering and throttle.
    """
    cfg = _read_config()
    strike_reserved = cfg.get("vehicle.ugv_strike", "reserved_channels", fallback="")
    assert strike_reserved.strip(), (
        "ugv_strike must declare reserved_channels; absence silently disables "
        "the servo/light-bar channel-conflict guard"
    )
    ugv_reserved = cfg.get("vehicle.ugv", "reserved_channels", fallback="")
    assert {c.strip() for c in strike_reserved.split(",")} == {
        c.strip() for c in ugv_reserved.split(",")
    }, "ugv_strike must reserve the same airframe channels as ugv"


def test_autonomy_live_profile_state_gates_on_armed_and_geofence():
    state = SystemState(
        mavlink_connected=True,
        gps_fix=3,
        autonomy_enabled=True,
        autonomy_geofence_present=True,
        operating_mode="ARMED",
    )
    assert _eval_autonomy_live(state).status == CapabilityStatus.READY

    state.operating_mode = "OBSERVE"
    assert _eval_autonomy_live(state).status == CapabilityStatus.BLOCKED

    state.operating_mode = "ARMED"
    state.autonomy_geofence_present = False
    assert _eval_autonomy_live(state).status == CapabilityStatus.BLOCKED


def test_follow_ready_with_mavlink_3d_gps_and_locked_track():
    state = SystemState(
        mavlink_connected=True,
        gps_fix=3,
        servo_locked_track_id=42,
    )
    assert _eval_follow(state).status == CapabilityStatus.READY


def test_ugv_strike_autonomy_uses_continuous_approach_handler():
    from hydra_detect.pipeline.facade import Pipeline

    pipeline = Pipeline.__new__(Pipeline)
    pipeline._vehicle = "ugv_strike"
    pipeline._handle_approach_strike_command = MagicMock()
    pipeline._handle_strike_command = MagicMock()

    selected = pipeline._autonomous_strike_handler()
    assert selected is pipeline._handle_approach_strike_command


def test_strike_handler_survives_pipeline_without_vehicle_attr():
    """_autonomous_strike_handler runs in the detection loop every frame.

    Pre-existing fixtures (tests/test_pipeline_callbacks.py) build Pipeline by
    patching __init__ and never set _vehicle. A bare attribute access would
    raise AttributeError inside the loop and kill detection. Mirrors the
    defensive getattr in _refuse_approach_for_fw.
    """
    from hydra_detect.pipeline.facade import Pipeline

    pipeline = Pipeline.__new__(Pipeline)
    pipeline._handle_approach_strike_command = MagicMock()
    pipeline._handle_strike_command = MagicMock()

    selected = pipeline._autonomous_strike_handler()
    assert selected is pipeline._handle_strike_command


def test_strike_handler_profile_match_is_case_insensitive():
    """HYDRA_VEHICLE casing must not silently downgrade the strike path.

    _is_fw_profile normalizes case; this selector must match that contract or
    'UGV_STRIKE' quietly falls back to the one-shot handler.
    """
    from hydra_detect.pipeline.facade import Pipeline

    for name in ("UGV_STRIKE", " ugv_strike ", "Ugv_Strike"):
        pipeline = Pipeline.__new__(Pipeline)
        pipeline._vehicle = name
        pipeline._handle_approach_strike_command = MagicMock()
        pipeline._handle_strike_command = MagicMock()

        selected = pipeline._autonomous_strike_handler()
        assert selected is pipeline._handle_approach_strike_command, name


@_skip_no_fcntl
class TestUGVStrikePipeline:
    @staticmethod
    def _make_pipeline(vehicle):
        """Build the minimal Pipeline state needed by strike idempotency."""
        from hydra_detect.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        pipeline._vehicle = vehicle
        pipeline._mavlink = None
        pipeline._approach = MagicMock()
        return pipeline

    def test_same_track_strike_is_idempotent(self):
        p = self._make_pipeline("ugv_strike")
        p._approach.mode = ApproachMode.STRIKE
        p._approach.target_track_id = 7

        assert p._handle_approach_strike_command(7) is True
        p._approach.start_strike.assert_not_called()

    def test_different_track_strike_remains_refused(self):
        p = self._make_pipeline("ugv_strike")
        p._approach.mode = ApproachMode.STRIKE
        p._approach.target_track_id = 7

        assert p._handle_approach_strike_command(8) is False
        p._approach.start_strike.assert_not_called()


def test_headless_gcs_statustext_alert_defaults_remain_enabled():
    cfg = _read_config()
    assert cfg.getboolean("mavlink", "alert_statustext") is True
    alert_classes = {
        value.strip() for value in cfg.get("mavlink", "alert_classes").split(",")
    }
    assert "truck" in alert_classes
