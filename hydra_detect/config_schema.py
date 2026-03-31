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
        "video_standard": FieldSpec(FieldType.ENUM, choices=["ntsc", "pal", "auto"], default="ntsc", description="Video standard"),
    },
    "detector": {
        "yolo_model": FieldSpec(FieldType.STRING, required=True, description="YOLO model filename"),
        "yolo_confidence": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1.0, default=0.45, description="Detection confidence threshold"),
        "yolo_imgsz": FieldSpec(FieldType.INT, min_val=32, max_val=1280, default=640, description="Inference resolution"),
        "yolo_classes": FieldSpec(FieldType.STRING, default="", description="Comma-separated class IDs to detect"),
        "low_light_luminance": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=255.0, default=40.0, description="Low-light luminance threshold for warnings"),
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
        "min_gps_fix": FieldSpec(FieldType.INT, min_val=0, max_val=6, default=3, description="Minimum GPS fix type required"),
        "alert_statustext": FieldSpec(FieldType.BOOL, default=True, description="Send detection alerts via STATUSTEXT"),
        "alert_interval_sec": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=60.0, default=5.0, description="Per-label alert throttle seconds"),
        "severity": FieldSpec(FieldType.INT, min_val=0, max_val=7, default=2, description="MAVLink severity level"),
        "alert_classes": FieldSpec(FieldType.STRING, default="", description="Comma-separated classes to alert on"),
        "auto_loiter_on_detect": FieldSpec(FieldType.BOOL, default=False, description="Auto-loiter on detection"),
        "guided_roi_on_detect": FieldSpec(FieldType.BOOL, default=False, description="Point vehicle at detection"),
        "strike_distance_m": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1000.0, default=20.0, description="Strike approach distance meters"),
        "geo_tracking": FieldSpec(FieldType.BOOL, default=True, description="Geo-tag detections"),
        "geo_tracking_interval": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=60.0, default=2.0, description="Geo-tracking update interval seconds"),
        "sim_gps_lat": FieldSpec(FieldType.FLOAT, min_val=-90.0, max_val=90.0, default=None, description="Simulated GPS latitude"),
        "sim_gps_lon": FieldSpec(FieldType.FLOAT, min_val=-180.0, max_val=180.0, default=None, description="Simulated GPS longitude"),
    },
    "alerts": {
        "light_bar_enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable light bar alerts"),
        "light_bar_channel": FieldSpec(FieldType.INT, min_val=1, max_val=16, default=4, description="Light bar servo channel"),
        "light_bar_pwm_on": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1900, description="Light bar on PWM"),
        "light_bar_pwm_off": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1100, description="Light bar off PWM"),
        "light_bar_flash_sec": FieldSpec(FieldType.FLOAT, min_val=0.05, max_val=10.0, default=0.5, description="Light bar flash duration seconds"),
        "global_max_per_sec": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=20.0, default=2.0, description="Global alert rate cap"),
        "priority_labels": FieldSpec(FieldType.STRING, default="", description="Labels that bypass rate cap"),
    },
    "web": {
        "enabled": FieldSpec(FieldType.BOOL, default=True, description="Enable web dashboard"),
        "host": FieldSpec(FieldType.STRING, default="0.0.0.0", description="Web server bind address"),
        "port": FieldSpec(FieldType.INT, min_val=1, max_val=65535, default=8080, description="Web server port"),
        "mjpeg_quality": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=70, description="MJPEG stream quality"),
        "api_token": FieldSpec(FieldType.STRING, default="", description="Bearer token for API auth"),
        "tls_enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable TLS for web server"),
        "tls_cert": FieldSpec(FieldType.STRING, default="", description="Path to TLS certificate"),
        "tls_key": FieldSpec(FieldType.STRING, default="", description="Path to TLS private key"),
        "web_password": FieldSpec(
            FieldType.STRING, default="",
            description="Password for web dashboard access (empty = no password required)"),
        "session_timeout_min": FieldSpec(
            FieldType.INT, min_val=5, max_val=1440, default=480,
            description="Login session timeout in minutes"),
    },
    "autonomous": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable autonomous controller"),
        "geofence_lat": FieldSpec(FieldType.FLOAT, min_val=-90.0, max_val=90.0, default=0.0, description="Geofence center latitude"),
        "geofence_lon": FieldSpec(FieldType.FLOAT, min_val=-180.0, max_val=180.0, default=0.0, description="Geofence center longitude"),
        "geofence_radius_m": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=50000.0, default=500.0, description="Geofence radius meters"),
        "geofence_polygon": FieldSpec(FieldType.STRING, default="", description="Geofence polygon coordinates"),
        "min_confidence": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1.0, default=0.85, description="Minimum confidence for strike"),
        "min_track_frames": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=5, description="Frames before qualifying track"),
        "allowed_classes": FieldSpec(FieldType.STRING, default="", description="Comma-separated classes allowed for autonomous action"),
        "strike_cooldown_sec": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=3600.0, default=30.0, description="Cooldown between strikes"),
        "allowed_vehicle_modes": FieldSpec(FieldType.STRING, default="AUTO", description="Comma-separated ArduPilot modes allowed"),
        "gps_max_stale_sec": FieldSpec(FieldType.FLOAT, min_val=0.5, max_val=30.0, default=2.0, description="GPS staleness threshold"),
        "require_operator_lock": FieldSpec(FieldType.BOOL, default=True, description="Require operator lock before strike"),
        "follow_speed_max": FieldSpec(FieldType.FLOAT, default=None, description="Maximum follow speed m/s"),
        "follow_min_distance": FieldSpec(FieldType.FLOAT, default=None, description="Minimum follow distance meters"),
        "approach_method": FieldSpec(FieldType.STRING, default="", description="Approach method for target"),
        "dogleg_distance_m": FieldSpec(FieldType.FLOAT, default=None, description="Dogleg maneuver distance meters"),
        "dogleg_bearing": FieldSpec(FieldType.STRING, default="perpendicular", description="Dogleg bearing: 'perpendicular' or compass degrees"),
        "dogleg_altitude_m": FieldSpec(FieldType.FLOAT, default=None, description="Dogleg maneuver altitude meters"),
        "arm_channel": FieldSpec(FieldType.INT, min_val=0, max_val=16, default=0, description="Servo channel for strike arm (0 = disabled)"),
        "arm_pwm_armed": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1900, description="Strike arm armed PWM value"),
        "arm_pwm_safe": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1100, description="Strike arm safe PWM value"),
        "hardware_arm_channel": FieldSpec(FieldType.INT, min_val=0, max_val=16, default=0, description="RC channel for hardware arm switch (0 = disabled)"),
    },
    "rf_homing": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable RF homing"),
        "mode": FieldSpec(FieldType.ENUM, choices=["wifi", "sdr", "rtl433"], default="wifi", description="RF homing mode"),
        "target_bssid": FieldSpec(FieldType.STRING, default="", description="Target BSSID for WiFi homing"),
        "target_freq_mhz": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=6000.0, default=915.0, description="Target frequency MHz"),
        "kismet_host": FieldSpec(FieldType.STRING, default="http://localhost:2501", description="Kismet server URL"),
        "kismet_user": FieldSpec(FieldType.STRING, default="", description="Kismet username"),
        "kismet_pass": FieldSpec(FieldType.STRING, default="", description="Kismet password"),
        "search_pattern": FieldSpec(FieldType.ENUM, choices=["lawnmower", "spiral", "expanding_square"], default="lawnmower", description="Search flight pattern"),
        "search_area_m": FieldSpec(FieldType.FLOAT, min_val=10.0, max_val=10000.0, default=100.0, description="Search area size meters"),
        "search_spacing_m": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=1000.0, default=20.0, description="Search leg spacing meters"),
        "search_alt_m": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=500.0, default=15.0, description="Search altitude meters"),
        "rssi_threshold_dbm": FieldSpec(FieldType.FLOAT, min_val=-120.0, max_val=0.0, default=-80.0, description="RSSI detection threshold dBm"),
        "rssi_converge_dbm": FieldSpec(FieldType.FLOAT, min_val=-120.0, max_val=0.0, default=-40.0, description="RSSI convergence threshold dBm"),
        "rssi_window": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=10, description="RSSI averaging window size"),
        "gradient_step_m": FieldSpec(FieldType.FLOAT, min_val=0.5, max_val=100.0, default=5.0, description="Gradient ascent step meters"),
        "gradient_rotation_deg": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=180.0, default=45.0, description="Gradient rotation degrees"),
        "poll_interval_sec": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=10.0, default=0.5, description="RSSI poll interval seconds"),
        "arrival_tolerance_m": FieldSpec(FieldType.FLOAT, min_val=0.5, max_val=50.0, default=3.0, description="Arrival tolerance meters"),
        "gps_required": FieldSpec(FieldType.BOOL, default=False, description="Require GPS for RF homing"),
        "kismet_source": FieldSpec(FieldType.STRING, default="rtl433-0", description="Kismet data source name"),
        "kismet_capture_dir": FieldSpec(FieldType.STRING, default="./output_data/kismet", description="Kismet capture directory"),
        "kismet_max_capture_mb": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=10000.0, default=100.0, description="Max Kismet capture size MB"),
        "kismet_auto_spawn": FieldSpec(FieldType.BOOL, default=False, description="Auto-spawn Kismet server"),
    },
    "servo_tracking": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable servo tracking"),
        "pan_channel": FieldSpec(FieldType.INT, min_val=1, max_val=16, default=1, description="Pan servo channel"),
        "pan_pwm_center": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1500, description="Pan center PWM"),
        "pan_pwm_range": FieldSpec(FieldType.INT, min_val=50, max_val=1000, default=500, description="Pan PWM range"),
        "pan_invert": FieldSpec(FieldType.BOOL, default=False, description="Invert pan direction"),
        "pan_dead_zone": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=0.5, default=0.05, description="Pan dead zone fraction"),
        "pan_smoothing": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=1.0, default=0.3, description="Pan smoothing factor"),
        "strike_channel": FieldSpec(FieldType.INT, min_val=1, max_val=16, default=2, description="Strike servo channel"),
        "strike_pwm_fire": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1900, description="Strike fire PWM"),
        "strike_pwm_safe": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1100, description="Strike safe PWM"),
        "strike_duration": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=10.0, default=0.5, description="Strike pulse duration"),
        "replaces_yaw": FieldSpec(FieldType.BOOL, default=False, description="Pan servo replaces yaw control"),
    },
    "watchdog": {
        "max_stall_sec": FieldSpec(FieldType.FLOAT, min_val=5.0, max_val=300.0, default=30.0, description="Force-exit after stall seconds"),
    },
    "logging": {
        "log_dir": FieldSpec(FieldType.STRING, default="./output_data/logs", description="Log directory"),
        "log_format": FieldSpec(FieldType.ENUM, choices=["jsonl", "csv"], default="jsonl", description="Detection log format"),
        "save_images": FieldSpec(FieldType.BOOL, default=True, description="Save annotated frames"),
        "image_dir": FieldSpec(FieldType.STRING, default="./output_data/images", description="Annotated image directory"),
        "image_quality": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=90, description="Saved image JPEG quality"),
        "save_crops": FieldSpec(FieldType.BOOL, default=False, description="Save target crops"),
        "crop_dir": FieldSpec(FieldType.STRING, default="./output_data/crops", description="Crop image directory"),
        "max_log_size_mb": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=1000.0, default=10.0, description="Max log file size MB"),
        "max_log_files": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=20, description="Max rotated log files"),
        "app_log_file": FieldSpec(FieldType.BOOL, default=True, description="Write application log to file"),
        "app_log_level": FieldSpec(FieldType.ENUM, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="INFO", description="Application log level"),
        "wipe_on_start": FieldSpec(FieldType.BOOL, default=False, description="Wipe logs on startup"),
    },
    "rtsp": {
        "enabled": FieldSpec(FieldType.BOOL, default=True, description="Enable RTSP output"),
        "port": FieldSpec(FieldType.INT, min_val=1, max_val=65535, default=8554, description="RTSP port"),
        "mount": FieldSpec(FieldType.STRING, default="/hydra", description="RTSP stream mount path"),
        "bitrate": FieldSpec(FieldType.INT, min_val=100000, max_val=50000000, default=2000000, description="RTSP stream bitrate"),
        "bind": FieldSpec(FieldType.STRING, default="", description="RTSP bind address"),
    },
    "osd": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable FPV OSD"),
        "mode": FieldSpec(FieldType.ENUM, choices=["statustext", "named_value", "msp_displayport"], default="statustext", description="OSD mode"),
        "update_interval": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=30.0, default=2.0, description="OSD update interval seconds"),
        "serial_port": FieldSpec(FieldType.STRING, default="/dev/ttyUSB0", description="OSD serial port"),
        "serial_baud": FieldSpec(FieldType.INT, min_val=9600, max_val=3000000, default=115200, description="OSD serial baud rate"),
        "canvas_cols": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=50, description="OSD canvas columns"),
        "canvas_rows": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=18, description="OSD canvas rows"),
    },
    "tak": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable TAK output"),
        "callsign": FieldSpec(FieldType.STRING, default="HYDRA-1", description="Vehicle callsign"),
        "multicast_group": FieldSpec(FieldType.STRING, default="239.2.3.1", description="TAK multicast group address"),
        "multicast_port": FieldSpec(FieldType.INT, min_val=1, max_val=65535, default=6969, description="TAK multicast port"),
        "unicast_targets": FieldSpec(FieldType.STRING, default="", description="Comma-separated unicast host:port targets"),
        "emit_interval": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=60.0, default=2.0, description="Detection emit interval seconds"),
        "sa_interval": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=60.0, default=5.0, description="SA position emit interval seconds"),
        "stale_detection": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=3600.0, default=60.0, description="Detection stale time seconds"),
        "stale_sa": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=3600.0, default=30.0, description="SA stale time seconds"),
        "advertise_host": FieldSpec(FieldType.STRING, default="", description="Host IP advertised in TAK video links"),
        "listen_commands": FieldSpec(FieldType.BOOL, default=False, description="Listen for TAK command messages"),
        "listen_port": FieldSpec(FieldType.INT, min_val=1, max_val=65535, default=6969, description="TAK command listen port"),
        "allowed_callsigns": FieldSpec(FieldType.STRING, default="", description="Comma-separated callsigns allowed to send commands"),
        "command_hmac_secret": FieldSpec(FieldType.STRING, default="", description="HMAC secret for TAK command authentication"),
    },
    "approach": {
        "follow_speed_min": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=30.0, default=2.0, description="Minimum follow speed m/s"),
        "follow_speed_max": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=30.0, default=10.0, description="Maximum follow speed m/s"),
        "follow_distance_m": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=500.0, default=15.0, description="Follow standoff distance meters"),
        "follow_yaw_rate_max": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=180.0, default=30.0, description="Maximum yaw rate degrees/sec"),
        "strike_approach_m": FieldSpec(FieldType.FLOAT, min_val=0.5, max_val=100.0, default=5.0, description="Strike close-approach distance meters (not standoff)"),
        "abort_mode": FieldSpec(FieldType.STRING, default="LOITER", description="ArduPilot mode on abort"),
        "waypoint_interval": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=10.0, default=0.5, description="Minimum seconds between waypoint updates"),
        "camera_hfov_deg": FieldSpec(FieldType.FLOAT, min_val=10.0, max_val=180.0, default=60.0, description="Camera horizontal FOV degrees"),
    },
    "drop": {
        "servo_channel": FieldSpec(FieldType.INT, min_val=0, max_val=16, default=0, description="Drop servo channel (0 = disabled)"),
        "pwm_release": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1900, description="Drop release PWM value"),
        "pwm_hold": FieldSpec(FieldType.INT, min_val=500, max_val=2500, default=1100, description="Drop hold PWM value"),
        "pulse_duration": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=30.0, default=1.0, description="Drop pulse duration seconds"),
        "drop_distance_m": FieldSpec(FieldType.FLOAT, min_val=0.5, max_val=100.0, default=3.0, description="Trigger release distance meters"),
    },
    "guidance": {
        "fwd_gain": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=20.0, default=2.0, description="Forward velocity gain"),
        "lat_gain": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=20.0, default=1.5, description="Lateral velocity gain"),
        "vert_gain": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=20.0, default=1.0, description="Vertical velocity gain"),
        "yaw_gain": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=180.0, default=30.0, description="Yaw rate gain deg/s"),
        "max_fwd_speed": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=30.0, default=5.0, description="Max forward speed m/s"),
        "max_lat_speed": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=20.0, default=2.0, description="Max lateral speed m/s"),
        "max_vert_speed": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=10.0, default=1.5, description="Max vertical speed m/s"),
        "max_yaw_rate": FieldSpec(FieldType.FLOAT, min_val=1.0, max_val=180.0, default=45.0, description="Max yaw rate deg/s"),
        "deadzone": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=0.5, default=0.05, description="Error deadzone fraction"),
        "smoothing": FieldSpec(FieldType.FLOAT, min_val=0.01, max_val=1.0, default=0.4, description="EMA smoothing alpha (higher = less smoothing)"),
        "target_bbox_ratio": FieldSpec(FieldType.FLOAT, min_val=0.01, max_val=1.0, default=0.15, description="Target bbox/frame ratio for approach"),
        "lost_track_timeout_s": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=30.0, default=2.0, description="Track loss timeout seconds"),
        "min_altitude_m": FieldSpec(FieldType.FLOAT, min_val=0.0, max_val=500.0, default=5.0, description="Minimum altitude floor metres"),
    },
    "mavlink_video": {
        "enabled": FieldSpec(FieldType.BOOL, default=False, description="Enable MAVLink video streaming"),
        "width": FieldSpec(FieldType.INT, min_val=32, max_val=1280, default=160, description="MAVLink video frame width"),
        "height": FieldSpec(FieldType.INT, min_val=32, max_val=720, default=120, description="MAVLink video frame height"),
        "jpeg_quality": FieldSpec(FieldType.INT, min_val=1, max_val=100, default=20, description="MAVLink video JPEG quality"),
        "max_fps": FieldSpec(FieldType.FLOAT, min_val=0.1, max_val=30.0, default=2.0, description="Max MAVLink video FPS"),
        "min_fps": FieldSpec(FieldType.FLOAT, min_val=0.01, max_val=10.0, default=0.2, description="Min MAVLink video FPS"),
        "link_budget_bytes_sec": FieldSpec(FieldType.INT, min_val=100, max_val=1000000, default=8000, description="MAVLink video link budget bytes/sec"),
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
