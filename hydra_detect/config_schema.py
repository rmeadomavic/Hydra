"""Config schema — typed validation for config.ini with plain-English errors."""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class FieldType(Enum):
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    ENUM = "enum"


@dataclass
class FieldSpec:
    """Specification for a single config field."""
    type: FieldType
    required: bool = False
    default: Any = None
    min_val: float | None = None
    max_val: float | None = None
    choices: list[str] | None = None  # for ENUM type
    description: str = ""


@dataclass
class ValidationResult:
    """Result of config validation."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


# Schema definition — every config key with type, range, and description
SCHEMA: dict[str, dict[str, FieldSpec]] = {
    "camera": {
        "source_type": FieldSpec(FieldType.ENUM, choices=["auto", "usb", "rtsp", "file", "v4l2", "analog"], default="auto", description="Camera source type"),
        "source": FieldSpec(FieldType.STRING, default="auto", description="Camera source path or URL"),
        "width": FieldSpec(FieldType.INT, min_val=160, max_val=3840, default=640, description="Frame width"),
        "height": FieldSpec(FieldType.INT, min_val=120, max_val=2160, default=480, description="Frame height"),
        "fps": FieldSpec(FieldType.INT, min_val=1, max_val=120, default=30, description="Target frame rate"),
        "hfov_deg": FieldSpec(FieldType.FLOAT, min_val=10.0, max_val=180.0, default=60.0, description="Horizontal FOV degrees"),
        "video_standard": FieldSpec(FieldType.ENUM, choices=["ntsc", "pal"], default="ntsc", description="Video standard"),
    },
    "detector": {
        "yolo_model": FieldSpec(FieldType.STRING, required=True, description="YOLO model filename"),
        "yolo_confidence": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1.0, default=0.45, description="Detection confidence threshold"),
        "yolo_imgsz": FieldSpec(FieldType.INT, min_val=32, max_val=1280, default=416, description="Inference resolution"),
        "yolo_classes": FieldSpec(FieldType.STRING, default="", description="Comma-separated class IDs to detect"),
    },
    "tracker": {
        "track_thresh": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1.0, default=0.5, description="Track confidence threshold"),
        "track_buffer": FieldSpec(FieldType.INT, min_val=1, max_val=300, default=30, description="Frames to keep lost tracks"),
        "match_thresh": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1.0, default=0.8, description="IOU match threshold"),
    },
    "mavlink": {
        "enabled": FieldSpec(FieldType.BOOL, default=True, description="Enable MAVLink connection"),
        "connection_string": FieldSpec(FieldType.STRING, default="/dev/ttyTHS1", description="Serial port or UDP address"),
        "baud": FieldSpec(FieldType.INT, min_val=9600, max_val=3000000, default=921600, description="Serial baud rate"),
        "source_system": FieldSpec(FieldType.INT, min_val=1, max_val=255, default=1, description="MAVLink system ID"),
        "alert_statustext": FieldSpec(FieldType.BOOL, default=True, description="Send detection alerts via STATUSTEXT"),
        "alert_interval_sec": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=60.0, default=5.0, description="Per-label alert throttle seconds"),
        "severity": FieldSpec(FieldType.INT, min_val=0, max_val=7, default=2, description="MAVLink severity level"),
        "auto_loiter_on_detect": FieldSpec(FieldType.BOOL, default=False, description="Auto-loiter on detection"),
        "guided_roi_on_detect": FieldSpec(FieldType.BOOL, default=False, description="Point vehicle at detection"),
        "geo_tracking": FieldSpec(FieldType.BOOL, default=False, description="Geo-tag detections"),
    },
    "alerts": {
        "global_max_per_sec": FieldSpec(FieldType.INT, min_val=1, max_val=20, default=2, description="Global alert rate cap"),
        "priority_labels": FieldSpec(FieldType.STRING, default="person,vehicle", description="Labels that bypass rate cap"),
    },
    "web": {
        "enabled": FieldSpec(FieldType.BOOL, default=True, description="Enable web dashboard"),
        "host": FieldSpec(FieldType.STRING, default="0.0.0.0", description="Web server bind address"),
        "port": FieldSpec(FieldType.INT, min_val=1, max_val=65535, default=8080, description="Web server port"),
        "mjpeg_quality": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=70, description="MJPEG stream quality"),
        "api_token": FieldSpec(FieldType.STRING, default="", description="Bearer token for API auth"),
    },
    "autonomous": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable autonomous controller"),
        "geofence_lat": FieldSpec(FieldType.FLOAT, min_val=-90.0, max_val=90.0, default=0.0, description="Geofence center latitude"),
        "geofence_lon": FieldSpec(FieldType.FLOAT, min_val=-180.0, max_val=180.0, default=0.0, description="Geofence center longitude"),
        "geofence_radius_m": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=50000.0, default=500.0, description="Geofence radius meters"),
        "min_confidence": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1.0, default=0.85, description="Minimum confidence for strike"),
        "min_track_frames": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=5, description="Frames before qualifying track"),
        "strike_cooldown_sec": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=3600.0, default=30.0, description="Cooldown between strikes"),
        "gps_max_stale_sec": FieldSpec(FieldType.FLOAT, min_val=0.5, max_val=30.0, default=2.0, description="GPS staleness threshold"),
        "require_operator_lock": FieldSpec(FieldType.BOOL, default=True, description="Require operator lock before strike"),
    },
    "servo_tracking": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable servo tracking"),
        "pan_channel": FieldSpec(FieldType.INT, min_val=1, max_val=16, default=1, description="Pan servo channel"),
        "pan_pwm_center": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1500, description="Pan center PWM"),
        "pan_pwm_range": FieldSpec(FieldType.INT, min_val=50, max_val=1000, default=500, description="Pan PWM range"),
        "strike_channel": FieldSpec(FieldType.INT, min_val=1, max_val=16, default=2, description="Strike servo channel"),
        "strike_pwm_fire": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1900, description="Strike fire PWM"),
        "strike_pwm_safe": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1100, description="Strike safe PWM"),
        "strike_duration": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=10.0, default=0.5, description="Strike pulse duration"),
    },
    "watchdog": {
        "max_stall_sec": FieldSpec(FieldType.FLOAT, min_val=5.0, max_val=300.0, default=30.0, description="Force-exit after stall seconds"),
    },
    "rtsp": {
        "enabled": FieldSpec(FieldType.BOOL, default=True, description="Enable RTSP output"),
        "port": FieldSpec(FieldType.INT, min_val=1, max_val=65535, default=8554, description="RTSP port"),
    },
    "osd": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable FPV OSD"),
        "mode": FieldSpec(FieldType.ENUM, choices=["statustext", "named_value", "msp_displayport"], default="statustext", description="OSD mode"),
    },
    "tak": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable TAK output"),
        "callsign": FieldSpec(FieldType.STRING, default="HYDRA-1", description="Vehicle callsign"),
    },
    "logging": {
        "log_dir": FieldSpec(FieldType.STRING, default="./output_data/logs", description="Log directory"),
        "log_format": FieldSpec(FieldType.ENUM, choices=["jsonl", "csv"], default="jsonl", description="Detection log format"),
        "save_images": FieldSpec(FieldType.BOOL, default=True, description="Save annotated frames"),
        "save_crops": FieldSpec(FieldType.BOOL, default=False, description="Save target crops"),
    },
}


def validate_config(cfg: configparser.ConfigParser) -> ValidationResult:
    """Validate entire config against schema. Returns errors and warnings."""
    result = ValidationResult()

    for section, fields in SCHEMA.items():
        if not cfg.has_section(section):
            # Only error if section has required fields
            has_required = any(f.required for f in fields.values())
            if has_required:
                result.errors.append(f"Missing required section [{section}]")
            continue

        for key, spec in fields.items():
            if not cfg.has_option(section, key):
                if spec.required:
                    result.errors.append(
                        f"[{section}] missing required key '{key}' — {spec.description}"
                    )
                continue

            raw = cfg.get(section, key).strip()
            if not raw and not spec.required:
                continue

            # Type validation
            try:
                if spec.type == FieldType.BOOL:
                    if raw.lower() not in ("true", "false", "yes", "no", "1", "0", "on", "off"):
                        result.errors.append(
                            f"[{section}] {key} must be true or false, got \"{raw}\""
                        )

                elif spec.type == FieldType.INT:
                    val = int(raw)
                    if spec.min_val is not None and val < spec.min_val:
                        result.errors.append(
                            f"[{section}] {key} must be at least {int(spec.min_val)}, got {val}"
                        )
                    if spec.max_val is not None and val > spec.max_val:
                        result.errors.append(
                            f"[{section}] {key} must be at most {int(spec.max_val)}, got {val}"
                        )

                elif spec.type == FieldType.FLOAT:
                    val = float(raw)
                    if spec.min_val is not None and val < spec.min_val:
                        result.errors.append(
                            f"[{section}] {key} must be at least {spec.min_val}, got {val}"
                        )
                    if spec.max_val is not None and val > spec.max_val:
                        result.errors.append(
                            f"[{section}] {key} must be at most {spec.max_val}, got {val}"
                        )

                elif spec.type == FieldType.ENUM:
                    if spec.choices and raw.lower() not in [c.lower() for c in spec.choices]:
                        result.errors.append(
                            f"[{section}] {key} must be one of {spec.choices}, got \"{raw}\""
                        )

            except ValueError:
                expected = "a number" if spec.type in (FieldType.INT, FieldType.FLOAT) else spec.type.value
                result.errors.append(
                    f"[{section}] {key} must be {expected}, got \"{raw}\""
                )

        # Check for unknown keys (typo detection)
        for key in cfg.options(section):
            if key not in fields:
                result.warnings.append(
                    f"[{section}] unknown key '{key}' — possible typo?"
                )

    return result
