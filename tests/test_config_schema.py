"""Tests for config schema validation and the /api/preflight endpoint."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.config_schema import (
    ValidationResult,
    validate_config,
)
from hydra_detect.web.server import app, configure_auth, stream_state, _auth_failures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(sections: dict[str, dict[str, str]]) -> configparser.ConfigParser:
    """Build a ConfigParser from a dict of sections."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    for section, keys in sections.items():
        cfg.add_section(section)
        for key, val in keys.items():
            cfg.set(section, key, val)
    return cfg


def _valid_config() -> configparser.ConfigParser:
    """Return a minimal config that passes validation."""
    return _make_config({
        "camera": {
            "source_type": "auto",
            "source": "auto",
            "width": "640",
            "height": "480",
            "fps": "30",
            "hfov_deg": "60.0",
            "video_standard": "ntsc",
        },
        "detector": {
            "yolo_model": "yolov8n.pt",
            "yolo_confidence": "0.45",
            "yolo_imgsz": "416",
            "yolo_classes": "",
        },
        "tracker": {
            "track_thresh": "0.5",
            "track_buffer": "30",
            "match_thresh": "0.8",
        },
        "mavlink": {
            "enabled": "true",
            "connection_string": "/dev/ttyTHS1",
            "baud": "921600",
            "source_system": "1",
            "alert_statustext": "true",
            "alert_interval_sec": "5.0",
            "severity": "2",
            "auto_loiter_on_detect": "false",
            "guided_roi_on_detect": "false",
            "geo_tracking": "true",
        },
        "alerts": {
            "global_max_per_sec": "2",
            "priority_labels": "person,vehicle",
        },
        "web": {
            "enabled": "true",
            "host": "0.0.0.0",
            "port": "8080",
            "mjpeg_quality": "70",
            "api_token": "",
        },
        "autonomous": {
            "enabled": "false",
            "geofence_lat": "0.0",
            "geofence_lon": "0.0",
            "geofence_radius_m": "500.0",
            "min_confidence": "0.85",
            "min_track_frames": "5",
            "strike_cooldown_sec": "30.0",
            "gps_max_stale_sec": "2.0",
            "require_operator_lock": "true",
        },
        "servo_tracking": {
            "enabled": "false",
            "pan_channel": "1",
            "pan_pwm_center": "1500",
            "pan_pwm_range": "500",
            "strike_channel": "2",
            "strike_pwm_fire": "1900",
            "strike_pwm_safe": "1100",
            "strike_duration": "0.5",
        },
        "watchdog": {
            "max_stall_sec": "30",
        },
        "rtsp": {
            "enabled": "true",
            "port": "8554",
        },
        "osd": {
            "enabled": "false",
            "mode": "statustext",
        },
        "tak": {
            "enabled": "false",
            "callsign": "HYDRA-1",
        },
        "logging": {
            "log_dir": "./output_data/logs",
            "log_format": "jsonl",
            "save_images": "true",
            "save_crops": "false",
        },
    })


# ---------------------------------------------------------------------------
# Unit tests — validate_config
# ---------------------------------------------------------------------------

class TestValidConfig:
    def test_valid_config_passes(self):
        cfg = _valid_config()
        result = validate_config(cfg)
        assert result.ok
        assert len(result.errors) == 0

    def test_valid_config_no_warnings_with_known_keys(self):
        cfg = _valid_config()
        result = validate_config(cfg)
        assert len(result.warnings) == 0


class TestInvalidFloat:
    def test_confidence_out_of_range(self):
        """yolo_confidence=90 is outside 0.0-1.0 range."""
        cfg = _valid_config()
        cfg.set("detector", "yolo_confidence", "90")
        result = validate_config(cfg)
        assert not result.ok
        assert any("yolo_confidence" in e and "at most" in e for e in result.errors)

    def test_confidence_negative(self):
        cfg = _valid_config()
        cfg.set("detector", "yolo_confidence", "-0.5")
        result = validate_config(cfg)
        assert not result.ok
        assert any("yolo_confidence" in e for e in result.errors)

    def test_float_not_a_number(self):
        cfg = _valid_config()
        cfg.set("detector", "yolo_confidence", "abc")
        result = validate_config(cfg)
        assert not result.ok
        assert any("yolo_confidence" in e and "a number" in e for e in result.errors)


class TestInvalidBool:
    def test_bad_bool_value(self):
        cfg = _valid_config()
        cfg.set("mavlink", "enabled", "maybe")
        result = validate_config(cfg)
        assert not result.ok
        assert any("enabled" in e and "true or false" in e for e in result.errors)

    def test_valid_bool_variants(self):
        """All standard bool strings should be accepted."""
        for val in ("true", "false", "yes", "no", "1", "0", "on", "off"):
            cfg = _valid_config()
            cfg.set("mavlink", "enabled", val)
            result = validate_config(cfg)
            assert result.ok, f"Bool value '{val}' should be valid"


class TestEnumValidation:
    def test_invalid_source_type(self):
        cfg = _valid_config()
        cfg.set("camera", "source_type", "satellite")
        result = validate_config(cfg)
        assert not result.ok
        assert any("source_type" in e and "satellite" in e for e in result.errors)

    def test_valid_source_types(self):
        for val in ("auto", "usb", "rtsp", "file", "v4l2", "analog"):
            cfg = _valid_config()
            cfg.set("camera", "source_type", val)
            result = validate_config(cfg)
            errors_for_source = [e for e in result.errors if "source_type" in e]
            assert len(errors_for_source) == 0, f"source_type '{val}' should be valid"

    def test_enum_case_insensitive(self):
        cfg = _valid_config()
        cfg.set("camera", "source_type", "AUTO")
        result = validate_config(cfg)
        errors_for_source = [e for e in result.errors if "source_type" in e]
        assert len(errors_for_source) == 0


class TestUnknownKey:
    def test_unknown_key_produces_warning(self):
        cfg = _valid_config()
        cfg.set("camera", "fov_magic", "42")
        result = validate_config(cfg)
        assert result.ok  # warnings don't fail
        assert any("fov_magic" in w and "typo" in w for w in result.warnings)


class TestMissingRequired:
    def test_missing_yolo_model(self):
        cfg = _valid_config()
        cfg.remove_option("detector", "yolo_model")
        result = validate_config(cfg)
        assert not result.ok
        assert any("yolo_model" in e and "required" in e for e in result.errors)

    def test_missing_required_section(self):
        cfg = _valid_config()
        cfg.remove_section("detector")
        result = validate_config(cfg)
        assert not result.ok
        assert any("[detector]" in e for e in result.errors)


class TestRangeValidation:
    def test_int_below_min(self):
        cfg = _valid_config()
        cfg.set("camera", "width", "50")  # min is 160
        result = validate_config(cfg)
        assert not result.ok
        assert any("width" in e and "at least 160" in e for e in result.errors)

    def test_int_above_max(self):
        cfg = _valid_config()
        cfg.set("camera", "width", "5000")  # max is 3840
        result = validate_config(cfg)
        assert not result.ok
        assert any("width" in e and "at most 3840" in e for e in result.errors)

    def test_float_below_min(self):
        cfg = _valid_config()
        cfg.set("camera", "hfov_deg", "5.0")  # min is 10.0
        result = validate_config(cfg)
        assert not result.ok
        assert any("hfov_deg" in e for e in result.errors)

    def test_float_above_max(self):
        cfg = _valid_config()
        cfg.set("camera", "hfov_deg", "200.0")  # max is 180.0
        result = validate_config(cfg)
        assert not result.ok
        assert any("hfov_deg" in e for e in result.errors)

    def test_int_not_a_number(self):
        cfg = _valid_config()
        cfg.set("camera", "width", "wide")
        result = validate_config(cfg)
        assert not result.ok
        assert any("width" in e and "a number" in e for e in result.errors)

    def test_port_boundary_valid(self):
        """Port 1 and 65535 should both be valid."""
        for val in ("1", "65535"):
            cfg = _valid_config()
            cfg.set("web", "port", val)
            result = validate_config(cfg)
            port_errors = [e for e in result.errors if "port" in e.lower()]
            assert len(port_errors) == 0, f"Port {val} should be valid"


class TestValidationResult:
    def test_ok_when_no_errors(self):
        r = ValidationResult()
        assert r.ok

    def test_not_ok_with_errors(self):
        r = ValidationResult(errors=["something broke"])
        assert not r.ok

    def test_ok_with_only_warnings(self):
        r = ValidationResult(warnings=["heads up"])
        assert r.ok


class TestEmptyAndMissingSections:
    def test_missing_optional_section_no_error(self):
        """Sections with no required fields can be absent."""
        cfg = _valid_config()
        cfg.remove_section("watchdog")
        result = validate_config(cfg)
        # watchdog has no required fields, so no error
        assert not any("[watchdog]" in e for e in result.errors)

    def test_empty_optional_string_no_error(self):
        """Empty optional string fields should not error."""
        cfg = _valid_config()
        cfg.set("detector", "yolo_classes", "")
        result = validate_config(cfg)
        yolo_errors = [e for e in result.errors if "yolo_classes" in e]
        assert len(yolo_errors) == 0


# ---------------------------------------------------------------------------
# Integration test — /api/preflight endpoint
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    _auth_failures.clear()
    stream_state._callbacks.clear()
    yield
    # Cleanup after each test to avoid leaking auth state to other test modules
    configure_auth(None)
    _auth_failures.clear()
    stream_state._callbacks.clear()


@pytest.fixture
def client():
    return TestClient(app)


class TestPreflightEndpoint:
    def test_preflight_returns_structure(self, client):
        """The endpoint returns checks array and overall status."""
        # Set up a mock preflight callback
        stream_state.set_callbacks(
            get_preflight=lambda: {
                "checks": [
                    {"name": "camera", "status": "pass", "message": "Camera OK"},
                    {"name": "config", "status": "pass", "message": "Config valid"},
                ],
                "overall": "pass",
            }
        )
        resp = client.get("/api/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert "checks" in data
        assert "overall" in data
        assert isinstance(data["checks"], list)
        assert data["overall"] in ("pass", "warn", "fail")

    def test_preflight_no_callback(self, client):
        """Without a pipeline callback, returns empty fail."""
        resp = client.get("/api/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"] == []
        assert data["overall"] == "fail"

    def test_preflight_check_fields(self, client):
        """Each check has name, status, and message."""
        stream_state.set_callbacks(
            get_preflight=lambda: {
                "checks": [
                    {"name": "camera", "status": "pass", "message": "USB camera on /dev/video0"},
                    {"name": "mavlink", "status": "warn", "message": "No GPS fix"},
                    {"name": "config", "status": "fail", "message": "1 error"},
                    {"name": "models", "status": "pass", "message": "yolov8n.pt loaded"},
                    {"name": "disk", "status": "pass", "message": "12.4 GB free"},
                ],
                "overall": "fail",
            }
        )
        resp = client.get("/api/preflight")
        data = resp.json()
        for check in data["checks"]:
            assert "name" in check
            assert "status" in check
            assert "message" in check
            assert check["status"] in ("pass", "warn", "fail")

    def test_preflight_overall_is_worst(self, client):
        """Overall status should be the worst status across all checks."""
        stream_state.set_callbacks(
            get_preflight=lambda: {
                "checks": [
                    {"name": "camera", "status": "pass", "message": "OK"},
                    {"name": "mavlink", "status": "warn", "message": "No GPS"},
                ],
                "overall": "warn",
            }
        )
        resp = client.get("/api/preflight")
        assert resp.json()["overall"] == "warn"

    def test_preflight_no_auth_required(self, client):
        """Preflight is a read-only check — no auth needed."""
        configure_auth("super-secret-token")
        stream_state.set_callbacks(
            get_preflight=lambda: {
                "checks": [],
                "overall": "pass",
            }
        )
        resp = client.get("/api/preflight")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Fallback / schema default alignment
# ---------------------------------------------------------------------------

class TestFallbackAlignment:
    """Meta-test: pipeline.py fallback values must match config_schema.py defaults."""

    def test_pipeline_fallbacks_match_schema_defaults(self):
        import ast
        import re as _re

        from hydra_detect.config_schema import SCHEMA

        pipeline_path = (
            Path(__file__).resolve().parent.parent
            / "hydra_detect"
            / "pipeline.py"
        )
        source = pipeline_path.read_text()

        # Match self._cfg.get*("section", "key", fallback=VALUE)
        pattern = _re.compile(
            r'self\._cfg\.get(int|float|boolean)?'
            r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,'
            r'\s*fallback\s*=\s*(.+?)\s*\)'
        )

        mismatches: list[str] = []
        for match in pattern.finditer(source):
            getter_type = match.group(1)
            section = match.group(2)
            key = match.group(3)
            fallback_raw = match.group(4).rstrip(",").strip()

            if section not in SCHEMA or key not in SCHEMA[section]:
                continue

            spec = SCHEMA[section][key]
            if spec.default is None:
                continue

            try:
                fallback_val = ast.literal_eval(fallback_raw)
            except (ValueError, SyntaxError):
                continue  # dynamic expression, skip

            schema_default = spec.default
            if getter_type == "boolean" or spec.type.value == "bool":
                fallback_val = bool(fallback_val)
                schema_default = bool(schema_default)
            elif getter_type == "int":
                fallback_val = int(fallback_val)
                schema_default = int(schema_default)
            elif getter_type == "float":
                fallback_val = float(fallback_val)
                schema_default = float(schema_default)
            elif getter_type is None and spec.type.value != "string":
                # Plain .get() returns strings; coerce schema default
                # to string for comparison with non-string fields.
                schema_default = str(schema_default)

            if fallback_val != schema_default:
                mismatches.append(
                    f"[{section}] {key}: "
                    f"fallback={fallback_val!r} "
                    f"!= schema default={schema_default!r}"
                )

        assert not mismatches, (
            "Pipeline fallback / schema default mismatches:\n"
            + "\n".join(mismatches)
        )
