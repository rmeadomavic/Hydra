"""Main detection pipeline — orchestrates camera, detector, tracker, and outputs."""

from __future__ import annotations

import atexit
import configparser
import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from ..approach import ApproachConfig, ApproachController, ApproachMode
from ..guidance import GuidanceConfig
from ..autonomous import AutonomousController, parse_polygon
from ..servo_tracker import ServoTracker
from ..camera import Camera, list_video_sources
from ..rf.hunt import RFHuntController
from ..rf.kismet_manager import KismetManager
from ..detection_logger import DetectionLogger
from ..event_logger import EventLogger
from ..model_manifest import (
    auto_update_manifest,
    load_manifest,
    validate_model,
    MANIFEST_FILENAME,
)
from ..detectors.yolo_detector import YOLODetector
from ..mavlink_io import MAVLinkIO
from ..osd import FpvOsd, build_osd_state
from ..overlay import draw_tracks
from ..system import (
    list_models as _list_models,
    list_power_modes as _list_power_modes,
    query_nvpmodel_sync as _query_nvpmodel_sync,
    read_jetson_stats as _read_jetson_stats,
    refresh_nvpmodel_async as _refresh_nvpmodel_async,
    set_power_mode as _set_power_mode,
)
from ..rtsp_server import RTSPServer
from ..mavlink_video import MAVLinkVideoSender
from ..tak.mavlink_relay import MAVLinkRelayOutput
from ..tak.tak_input import TAKInput
from ..tak.tak_output import TAKOutput
from ..tracker import ByteTracker
from ..profiles import get_profile, load_profiles
from ..web.config_api import set_config_path, set_engagement_check
from ..web.server import (
    configure_auth,
    configure_web_password,
    run_server,
    set_tak_input,
    set_tak_output,
    stream_state,
)
from .bootstrap import build_detector
from .control import PipelineControlAdapter
from .integrations import PipelineIntegrations
from .runtime import PipelineRuntime

logger = logging.getLogger(__name__)


def _get_rf_hunt_controller_cls():
    """Return RFHuntController class, honoring package-level monkeypatches in tests."""
    pkg = sys.modules.get("hydra_detect.pipeline")
    if pkg is not None and hasattr(pkg, "RFHuntController"):
        return getattr(pkg, "RFHuntController")
    return RFHuntController


def _build_detector(cfg: configparser.ConfigParser, models_dir: Path | None = None) -> YOLODetector:
    """Backwards-compatible wrapper for detector construction."""
    return build_detector(cfg, models_dir)


class Pipeline:
    """Top-level orchestrator that ties all modules together."""

    def __init__(
        self,
        config_path: str = "config.ini",
        vehicle: str | None = None,
        cfg_override: configparser.ConfigParser | None = None,
    ):
        if cfg_override is not None:
            self._cfg = cfg_override
        else:
            self._cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
            self._cfg.read(config_path)

        # Apply vehicle-specific overrides from [vehicle.<name>] sections.
        # Keys use dotted notation: "camera.source" → override [camera] source.
        if vehicle:
            vehicle_section = f"vehicle.{vehicle}"
            if self._cfg.has_section(vehicle_section):
                logger.info("Applying vehicle profile: %s", vehicle)
                for key, value in self._cfg.items(vehicle_section):
                    # key format: "section.option" → override cfg[section][option]
                    if "." in key:
                        section, option = key.split(".", 1)
                        if not self._cfg.has_section(section):
                            self._cfg.add_section(section)
                        self._cfg.set(section, option, value)
                    else:
                        logger.warning(
                            "Vehicle config key %r missing section prefix "
                            "(expected section.option)",
                            key,
                        )
            else:
                logger.error(
                    "Vehicle profile %r not found (no [%s] section in config)",
                    vehicle, vehicle_section,
                )

        # Callsign-based identity: used in STATUSTEXT, logs, TAK, web UI
        self._callsign = self._cfg.get("tak", "callsign", fallback="HYDRA-1")
        # Auto-generate callsign from vehicle flag if still default
        if self._callsign == "HYDRA-1" and vehicle:
            self._callsign = f"HYDRA-{vehicle.upper()}"
            logger.info("Auto-callsign from vehicle flag: %s", self._callsign)
        # Sync derived callsign back to config so TAK output reads it
        if not self._cfg.has_section("tak"):
            self._cfg.add_section("tak")
        self._cfg.set("tak", "callsign", self._callsign)
        self._vehicle = vehicle

        # Wire the config API to the actual file so the web settings page
        # reads and writes the correct path when --config is non-default.
        set_config_path(Path(config_path).resolve())

        # Models search: /models (Docker mount), then ./models, then project root
        self._project_dir = Path(config_path).resolve().parent
        self._models_dir = self._project_dir / "models"

        # Mission profiles
        profiles_path = self._project_dir / "profiles.json"
        self._profiles = load_profiles(str(profiles_path))
        self._active_profile: str | None = None

        # Camera
        self._camera = Camera(
            source=self._cfg.get("camera", "source", fallback="auto"),
            width=self._cfg.getint("camera", "width", fallback=640),
            height=self._cfg.getint("camera", "height", fallback=480),
            fps=self._cfg.getint("camera", "fps", fallback=30),
            source_type=self._cfg.get("camera", "source_type", fallback="auto"),
            video_standard=self._cfg.get("camera", "video_standard", fallback="ntsc"),
        )

        # Detector
        self._detector = _build_detector(self._cfg, self._models_dir)

        # Tracker
        self._tracker = ByteTracker(
            track_thresh=self._cfg.getfloat("tracker", "track_thresh", fallback=0.5),
            track_buffer=self._cfg.getint("tracker", "track_buffer", fallback=30),
            match_thresh=self._cfg.getfloat("tracker", "match_thresh", fallback=0.8),
            frame_rate=self._cfg.getint("camera", "fps", fallback=30),
        )

        # Alert class filter (shared with MAVLink and overlay)
        alert_classes_raw = self._cfg.get("mavlink", "alert_classes", fallback="")
        alert_classes = None
        if alert_classes_raw.strip():
            alert_classes = {c.strip() for c in alert_classes_raw.split(",") if c.strip()}
            alert_classes = alert_classes or None
        self._alert_classes = alert_classes

        # MAVLink
        self._mavlink: Optional[MAVLinkIO] = None
        if self._cfg.getboolean("mavlink", "enabled", fallback=True):
            self._mavlink = MAVLinkIO(
                connection_string=self._cfg.get(
                    "mavlink", "connection_string", fallback="/dev/ttyTHS1"
                ),
                baud=self._cfg.getint("mavlink", "baud", fallback=921600),
                source_system=self._cfg.getint("mavlink", "source_system", fallback=1),
                alert_statustext=self._cfg.getboolean(
                    "mavlink", "alert_statustext", fallback=True
                ),
                alert_interval_sec=self._cfg.getfloat(
                    "mavlink", "alert_interval_sec", fallback=5.0
                ),
                severity=self._cfg.getint("mavlink", "severity", fallback=2),
                min_gps_fix=self._cfg.getint("mavlink", "min_gps_fix", fallback=3),
                auto_loiter=self._cfg.getboolean(
                    "mavlink", "auto_loiter_on_detect", fallback=False
                ),
                guided_roi=self._cfg.getboolean(
                    "mavlink", "guided_roi_on_detect", fallback=False
                ),
                alert_classes=alert_classes,
                global_max_per_sec=self._cfg.getfloat(
                    "alerts", "global_max_per_sec", fallback=2.0
                ),
                priority_labels=[
                    lbl.strip() for lbl in self._cfg.get(
                        "alerts", "priority_labels", fallback=""
                    ).split(",") if lbl.strip()
                ] or None,
                sim_gps_lat=self._cfg.getfloat("mavlink", "sim_gps_lat", fallback=None)
                if self._cfg.get("mavlink", "sim_gps_lat", fallback="").strip()
                else None,
                sim_gps_lon=self._cfg.getfloat("mavlink", "sim_gps_lon", fallback=None)
                if self._cfg.get("mavlink", "sim_gps_lon", fallback="").strip()
                else None,
            )

        # FPV OSD overlay (requires MAVLink and FC with OSD chip)
        self._osd: FpvOsd | None = None
        if (
            self._mavlink is not None
            and self._cfg.getboolean("osd", "enabled", fallback=False)
        ):
            self._osd = FpvOsd(
                self._mavlink,
                mode=self._cfg.get("osd", "mode", fallback="statustext"),
                update_interval=self._cfg.getfloat(
                    "osd", "update_interval", fallback=2.0
                ),
                serial_port=self._cfg.get(
                    "osd", "serial_port", fallback="/dev/ttyUSB0"
                ),
                serial_baud=self._cfg.getint(
                    "osd", "serial_baud", fallback=115200
                ),
                canvas_rows=self._cfg.getint(
                    "osd", "canvas_rows", fallback=18
                ),
                canvas_cols=self._cfg.getint(
                    "osd", "canvas_cols", fallback=50
                ),
            )

        # Light bar / strobe on detection (requires MAVLink)
        self._light_bar_enabled = False
        self._light_bar_channel = 4
        self._light_bar_pwm_on = 1900
        self._light_bar_pwm_off = 1100
        self._light_bar_flash_sec = 0.5
        self._light_bar_last_flash: float = 0.0
        self._auto_loiter_last: float = 0.0
        self._auto_loiter_cooldown: float = 5.0  # seconds between loiter commands
        if (
            self._mavlink is not None
            and self._cfg.getboolean("alerts", "light_bar_enabled", fallback=False)
        ):
            self._light_bar_enabled = True
            self._light_bar_channel = self._cfg.getint("alerts", "light_bar_channel", fallback=4)
            self._light_bar_pwm_on = self._cfg.getint("alerts", "light_bar_pwm_on", fallback=1900)
            self._light_bar_pwm_off = self._cfg.getint("alerts", "light_bar_pwm_off", fallback=1100)
            self._light_bar_flash_sec = self._cfg.getfloat(
                "alerts", "light_bar_flash_sec", fallback=0.5
            )
            logger.info(
                "Light bar enabled: channel=%d, on=%d, off=%d, flash=%.1fs",
                self._light_bar_channel, self._light_bar_pwm_on,
                self._light_bar_pwm_off, self._light_bar_flash_sec,
            )

        # Pixel-lock servo tracker
        self._pan_ch: int = 1
        self._strike_ch: int = 2
        self._servo_tracker: ServoTracker | None = None
        if (
            self._mavlink is not None
            and self._cfg.getboolean("servo_tracking", "enabled", fallback=False)
        ):
            self._pan_ch = self._cfg.getint("servo_tracking", "pan_channel", fallback=1)
            self._strike_ch = self._cfg.getint("servo_tracking", "strike_channel", fallback=2)
            pan_ch = self._pan_ch
            strike_ch = self._strike_ch
            # Channel collision check
            channels = [pan_ch, strike_ch]
            if self._light_bar_enabled:
                channels.append(self._light_bar_channel)
            if len(channels) != len(set(channels)):
                logger.error(
                    "Servo tracking DISABLED: channel collision detected "
                    "(pan=%d, strike=%d, light_bar=%d)",
                    pan_ch, strike_ch, self._light_bar_channel,
                )
            else:
                self._servo_tracker = ServoTracker(
                    self._mavlink,
                    pan_channel=pan_ch,
                    pan_pwm_center=self._cfg.getint(
                        "servo_tracking", "pan_pwm_center", fallback=1500
                    ),
                    pan_pwm_range=self._cfg.getint(
                        "servo_tracking", "pan_pwm_range", fallback=500
                    ),
                    pan_invert=self._cfg.getboolean(
                        "servo_tracking", "pan_invert", fallback=False
                    ),
                    pan_dead_zone=self._cfg.getfloat(
                        "servo_tracking", "pan_dead_zone", fallback=0.05
                    ),
                    pan_smoothing=self._cfg.getfloat(
                        "servo_tracking", "pan_smoothing", fallback=0.3
                    ),
                    strike_channel=strike_ch,
                    strike_pwm_fire=self._cfg.getint(
                        "servo_tracking", "strike_pwm_fire", fallback=1900
                    ),
                    strike_pwm_safe=self._cfg.getint(
                        "servo_tracking", "strike_pwm_safe", fallback=1100
                    ),
                    strike_duration=self._cfg.getfloat(
                        "servo_tracking", "strike_duration", fallback=0.5
                    ),
                    replaces_yaw=self._cfg.getboolean(
                        "servo_tracking", "replaces_yaw", fallback=False
                    ),
                )
                logger.info(
                    "Pixel-lock servo tracking ENABLED: pan_ch=%d, strike_ch=%d, replaces_yaw=%s",
                    pan_ch, strike_ch, self._servo_tracker.replaces_yaw,
                )

        # Validate servo channels against vehicle reserved channels
        self._servo_channel_error: str | None = None
        if self._servo_tracker is not None and vehicle:
            vehicle_section = f"vehicle.{vehicle}"
            reserved_raw = self._cfg.get(vehicle_section, "reserved_channels", fallback="")
            if reserved_raw.strip():
                try:
                    reserved = {int(c.strip()) for c in reserved_raw.split(",") if c.strip()}
                    conflicts = []
                    if pan_ch in reserved:
                        conflicts.append(f"pan channel {pan_ch}")
                    if strike_ch in reserved:
                        conflicts.append(f"strike channel {strike_ch}")
                    if conflicts:
                        conflict_msg = ", ".join(conflicts)
                        logger.critical(
                            "SAFETY: %s conflicts with %s reserved channels %s — "
                            "servo tracking DISABLED",
                            conflict_msg, vehicle, reserved,
                        )
                        self._servo_tracker = None
                        # Store error for pre-flight checklist
                        self._servo_channel_error = (
                            f"SAFETY: {conflict_msg} conflicts with {vehicle} reserved channels"
                        )
                except ValueError:
                    logger.warning(
                        "Invalid reserved_channels in [%s]: %s",
                        vehicle_section, reserved_raw,
                    )

        # Autonomous strike controller
        self._autonomous: AutonomousController | None = None
        if self._cfg.getboolean("autonomous", "enabled", fallback=False):
            poly_raw = self._cfg.get("autonomous", "geofence_polygon", fallback="").strip()
            polygon = parse_polygon(poly_raw) if poly_raw else None
            classes_raw = self._cfg.get("autonomous", "allowed_classes", fallback="")
            allowed_classes = [c.strip() for c in classes_raw.split(",") if c.strip()] or None
            modes_raw = self._cfg.get("autonomous", "allowed_vehicle_modes", fallback="AUTO")
            allowed_modes = [m.strip() for m in modes_raw.split(",") if m.strip()] or None
            self._autonomous = AutonomousController(
                enabled=True,
                geofence_lat=self._cfg.getfloat("autonomous", "geofence_lat", fallback=0.0),
                geofence_lon=self._cfg.getfloat("autonomous", "geofence_lon", fallback=0.0),
                geofence_radius_m=self._cfg.getfloat(
                    "autonomous", "geofence_radius_m", fallback=500.0
                ),
                geofence_polygon=polygon,
                min_confidence=self._cfg.getfloat(
                    "autonomous", "min_confidence", fallback=0.85
                ),
                min_track_frames=self._cfg.getint(
                    "autonomous", "min_track_frames", fallback=5
                ),
                allowed_classes=allowed_classes,
                strike_cooldown_sec=self._cfg.getfloat(
                    "autonomous", "strike_cooldown_sec", fallback=30.0
                ),
                allowed_vehicle_modes=allowed_modes,
                gps_max_stale_sec=self._cfg.getfloat(
                    "autonomous", "gps_max_stale_sec", fallback=2.0
                ),
                require_operator_lock=self._cfg.getboolean(
                    "autonomous", "require_operator_lock", fallback=True
                ),
            )
            logger.info(
                "Autonomous strike ENABLED: fence_radius=%.0fm, min_conf=%.2f, "
                "min_frames=%d, classes=%s",
                self._cfg.getfloat("autonomous", "geofence_radius_m", fallback=500.0),
                self._cfg.getfloat("autonomous", "min_confidence", fallback=0.85),
                self._cfg.getint("autonomous", "min_track_frames", fallback=5),
                classes_raw or "NONE (fail-closed)",
            )

        # Approach controller (Follow / Drop / Strike)
        self._approach: ApproachController | None = None
        if self._mavlink is not None:
            _hfov_default = 120.0 if self._cfg.get(
                "camera", "source_type", fallback="auto"
            ) == "analog" else 60.0
            approach_cfg = ApproachConfig(
                follow_speed_min=self._cfg.getfloat(
                    "approach", "follow_speed_min", fallback=2.0),
                follow_speed_max=self._cfg.getfloat(
                    "approach", "follow_speed_max", fallback=10.0),
                follow_distance_m=self._cfg.getfloat(
                    "approach", "follow_distance_m", fallback=15.0),
                follow_yaw_rate_max=self._cfg.getfloat(
                    "approach", "follow_yaw_rate_max", fallback=30.0),
                strike_approach_m=self._cfg.getfloat(
                    "approach", "strike_approach_m", fallback=5.0),
                drop_channel=self._cfg.getint(
                    "drop", "servo_channel", fallback=0) or None,
                drop_pwm_release=self._cfg.getint(
                    "drop", "pwm_release", fallback=1900),
                drop_pwm_hold=self._cfg.getint(
                    "drop", "pwm_hold", fallback=1100),
                drop_duration=self._cfg.getfloat(
                    "drop", "pulse_duration", fallback=1.0),
                drop_distance_m=self._cfg.getfloat(
                    "drop", "drop_distance_m", fallback=3.0),
                arm_channel=self._cfg.getint(
                    "autonomous", "arm_channel", fallback=0) or None,
                arm_pwm_armed=self._cfg.getint(
                    "autonomous", "arm_pwm_armed", fallback=1900),
                arm_pwm_safe=self._cfg.getint(
                    "autonomous", "arm_pwm_safe", fallback=1100),
                hw_arm_channel=self._cfg.getint(
                    "autonomous", "hardware_arm_channel", fallback=0) or None,
                camera_hfov_deg=self._cfg.getfloat(
                    "camera", "hfov_deg", fallback=_hfov_default),
                abort_mode=self._cfg.get(
                    "approach", "abort_mode", fallback="LOITER"),
                waypoint_interval=self._cfg.getfloat(
                    "approach", "waypoint_interval", fallback=0.5),
                guidance_cfg=GuidanceConfig(
                    fwd_gain=self._cfg.getfloat("guidance", "fwd_gain", fallback=2.0),
                    lat_gain=self._cfg.getfloat("guidance", "lat_gain", fallback=1.5),
                    vert_gain=self._cfg.getfloat("guidance", "vert_gain", fallback=1.0),
                    yaw_gain=self._cfg.getfloat("guidance", "yaw_gain", fallback=30.0),
                    max_fwd_speed=self._cfg.getfloat("guidance", "max_fwd_speed", fallback=5.0),
                    max_lat_speed=self._cfg.getfloat("guidance", "max_lat_speed", fallback=2.0),
                    max_vert_speed=self._cfg.getfloat("guidance", "max_vert_speed", fallback=1.5),
                    max_yaw_rate=self._cfg.getfloat("guidance", "max_yaw_rate", fallback=45.0),
                    deadzone=self._cfg.getfloat("guidance", "deadzone", fallback=0.05),
                    smoothing=self._cfg.getfloat("guidance", "smoothing", fallback=0.4),
                    target_bbox_ratio=self._cfg.getfloat(
                        "guidance", "target_bbox_ratio", fallback=0.15),
                    lost_track_timeout_s=self._cfg.getfloat(
                        "guidance", "lost_track_timeout_s", fallback=2.0),
                    min_altitude_m=self._cfg.getfloat(
                        "guidance", "min_altitude_m", fallback=5.0),
                ),
            )
            self._approach = ApproachController(self._mavlink, approach_cfg)
            logger.info(
                "Approach controller initialized (drop_ch=%s, arm_ch=%s, hw_arm_ch=%s)",
                approach_cfg.drop_channel,
                approach_cfg.arm_channel,
                approach_cfg.hw_arm_channel,
            )

        # RF homing controller
        self._rf_hunt: RFHuntController | None = None
        self._kismet_manager: KismetManager | None = None
        if self._cfg.getboolean("rf_homing", "enabled", fallback=False):
            if self._mavlink is not None:
                kismet_host = self._cfg.get(
                    "rf_homing", "kismet_host",
                    fallback="http://localhost:2501",
                )
                self._kismet_manager = KismetManager(
                    source=self._cfg.get(
                        "rf_homing", "kismet_source", fallback="rtl433-0"
                    ),
                    capture_dir=self._cfg.get(
                        "rf_homing", "kismet_capture_dir",
                        fallback="./output_data/kismet",
                    ),
                    host=kismet_host,
                    user=self._cfg.get(
                        "rf_homing", "kismet_user", fallback=""
                    ),
                    password=self._cfg.get(
                        "rf_homing", "kismet_pass", fallback=""
                    ),
                    log_dir=self._cfg.get(
                        "logging", "log_dir",
                        fallback="./output_data/logs",
                    ),
                    max_capture_mb=self._cfg.getfloat(
                        "rf_homing", "kismet_max_capture_mb",
                        fallback=100.0,
                    ),
                    auto_spawn=self._cfg.getboolean(
                        "rf_homing", "kismet_auto_spawn", fallback=False
                    ),
                )
                if self._kismet_manager.start():
                    # Pass geofence callbacks if autonomous controller is available
                    geofence_check = None
                    geofence_clip = None
                    if self._autonomous is not None:
                        geofence_check = self._autonomous.check_geofence
                        geofence_clip = self._autonomous.clip_to_geofence
                    self._rf_hunt = _get_rf_hunt_controller_cls()(
                        self._mavlink,
                        mode=self._cfg.get(
                            "rf_homing", "mode", fallback="wifi"
                        ),
                        target_bssid=self._cfg.get(
                            "rf_homing", "target_bssid", fallback=""
                        ).strip() or None,
                        target_freq_mhz=self._cfg.getfloat(
                            "rf_homing", "target_freq_mhz",
                            fallback=915.0,
                        ),
                        kismet_host=kismet_host,
                        kismet_user=self._cfg.get(
                            "rf_homing", "kismet_user", fallback=""
                        ),
                        kismet_pass=self._cfg.get(
                            "rf_homing", "kismet_pass", fallback=""
                        ),
                        search_pattern=self._cfg.get(
                            "rf_homing", "search_pattern",
                            fallback="lawnmower",
                        ),
                        search_area_m=self._cfg.getfloat(
                            "rf_homing", "search_area_m", fallback=100.0
                        ),
                        search_spacing_m=self._cfg.getfloat(
                            "rf_homing", "search_spacing_m",
                            fallback=20.0,
                        ),
                        search_alt_m=self._cfg.getfloat(
                            "rf_homing", "search_alt_m", fallback=15.0
                        ),
                        rssi_threshold_dbm=self._cfg.getfloat(
                            "rf_homing", "rssi_threshold_dbm",
                            fallback=-80.0,
                        ),
                        rssi_converge_dbm=self._cfg.getfloat(
                            "rf_homing", "rssi_converge_dbm",
                            fallback=-40.0,
                        ),
                        rssi_window=self._cfg.getint(
                            "rf_homing", "rssi_window", fallback=10
                        ),
                        gradient_step_m=self._cfg.getfloat(
                            "rf_homing", "gradient_step_m", fallback=5.0
                        ),
                        gradient_rotation_deg=self._cfg.getfloat(
                            "rf_homing", "gradient_rotation_deg",
                            fallback=45.0,
                        ),
                        poll_interval_sec=self._cfg.getfloat(
                            "rf_homing", "poll_interval_sec",
                            fallback=0.5,
                        ),
                        arrival_tolerance_m=self._cfg.getfloat(
                            "rf_homing", "arrival_tolerance_m",
                            fallback=3.0,
                        ),
                        kismet_manager=self._kismet_manager,
                        geofence_check=geofence_check,
                        geofence_clip=geofence_clip,
                    )
                    logger.info(
                        "RF homing configured: mode=%s target=%s",
                        self._cfg.get("rf_homing", "mode", fallback="wifi"),
                        self._cfg.get(
                            "rf_homing", "target_bssid", fallback=""
                        ) or (
                            f"{self._cfg.getfloat('rf_homing', 'target_freq_mhz', fallback=915.0)}"
                            "MHz"
                        ),
                    )
                else:
                    logger.warning("Kismet failed to start — RF homing disabled")
                    self._kismet_manager = None
            else:
                logger.warning("RF homing requires MAVLink — skipping")

        # Geo-tracking for GCS map markers
        self._geo_tracker = None
        if (
            self._mavlink is not None
            and self._cfg.getboolean("mavlink", "geo_tracking", fallback=True)
        ):
            from ..geo_tracking import GeoTracker
            self._geo_tracker = GeoTracker(
                self._mavlink,
                camera_hfov_deg=self._cfg.getfloat("camera", "hfov_deg", fallback=60.0),
                min_interval=self._cfg.getfloat("mavlink", "geo_tracking_interval", fallback=2.0),
            )
            logger.info("Geo-tracking enabled (CAMERA_TRACKING_GEO_STATUS)")

        # TAK / ATAK CoT output
        self._tak: TAKOutput | None = None
        self._mav_relay: MAVLinkRelayOutput | None = None
        if self._cfg.getboolean("tak", "enabled", fallback=False):
            if self._mavlink is not None:
                tak_mode = self._cfg.get("tak", "mode", fallback="direct").lower()
                want_direct = tak_mode in ("direct", "both")
                want_relay = tak_mode in ("relay", "both")
                emit_interval = self._cfg.getfloat(
                    "tak", "emit_interval", fallback=2.0,
                )
                hfov = self._cfg.getfloat("camera", "hfov_deg", fallback=60.0)
                if want_direct:
                    rtsp_url = None
                    tak_host = self._cfg.get("tak", "advertise_host", fallback="").strip()
                    if tak_host and self._cfg.getboolean("rtsp", "enabled", fallback=True):
                        rtsp_port = self._cfg.getint("rtsp", "port", fallback=8554)
                        rtsp_mount = self._cfg.get("rtsp", "mount", fallback="/hydra")
                        rtsp_url = f"rtsp://{tak_host}:{rtsp_port}{rtsp_mount}"
                    mcast_group = self._cfg.get(
                        "tak", "multicast_group", fallback="239.2.3.1",
                    )
                    mcast_port = self._cfg.getint(
                        "tak", "multicast_port", fallback=6969,
                    )
                    callsign = self._cfg.get(
                        "tak", "callsign", fallback="HYDRA-1",
                    )
                    self._tak = TAKOutput(
                        mavlink_io=self._mavlink,
                        callsign=callsign,
                        multicast_group=mcast_group,
                        multicast_port=mcast_port,
                        emit_interval=emit_interval,
                        sa_interval=self._cfg.getfloat(
                            "tak", "sa_interval", fallback=5.0,
                        ),
                        stale_detection=self._cfg.getfloat(
                            "tak", "stale_detection", fallback=60.0,
                        ),
                        stale_sa=self._cfg.getfloat(
                            "tak", "stale_sa", fallback=30.0,
                        ),
                        camera_hfov_deg=hfov,
                        unicast_targets=self._cfg.get(
                            "tak", "unicast_targets", fallback="",
                        ),
                        rtsp_url=rtsp_url,
                    )
                    set_tak_output(self._tak)
                    logger.info(
                        "TAK/ATAK direct output configured: %s:%d callsign=%s",
                        mcast_group, mcast_port, callsign,
                    )
                if want_relay:
                    self._mav_relay = MAVLinkRelayOutput(
                        mavlink_io=self._mavlink,
                        emit_interval=emit_interval,
                        camera_hfov_deg=hfov,
                    )
                    logger.info(
                        "TAK MAVLink relay configured (mode=%s)", tak_mode,
                    )
            else:
                logger.warning("TAK output requires MAVLink for GPS — skipping")

        # TAK / ATAK CoT command listener
        self._tak_input: TAKInput | None = None
        if (
            self._cfg.getboolean("tak", "enabled", fallback=False)
            and self._cfg.getboolean("tak", "listen_commands", fallback=False)
        ):
            # Parse allowed callsigns (comma-separated, empty = disabled)
            _allowed_raw = self._cfg.get("tak", "allowed_callsigns", fallback="")
            _allowed_list = [
                cs.strip() for cs in _allowed_raw.split(",") if cs.strip()
            ] or None
            _hmac_secret = self._cfg.get(
                "tak", "command_hmac_secret", fallback=""
            ).strip() or None

            self._tak_input = TAKInput(
                listen_port=self._cfg.getint("tak", "listen_port", fallback=6969),
                multicast_group=self._cfg.get("tak", "multicast_group", fallback="239.2.3.1"),
                on_lock=lambda tid: self._handle_target_lock(tid, mode="track"),
                on_strike=self._handle_strike_command,
                on_unlock=self._handle_target_unlock,
                allowed_callsigns=_allowed_list,
                hmac_secret=_hmac_secret,
                my_callsign=self._callsign,
            )
            logger.info(
                "TAK command listener configured: port=%d, allowed=%s, hmac=%s",
                self._cfg.getint("tak", "listen_port", fallback=6969),
                _allowed_list or "NONE (commands disabled)",
                "enabled" if _hmac_secret else "disabled",
            )

        # Logger
        # Compute model file hash for detection log chain-of-custody
        _model_hash = ""
        try:
            import hashlib
            _mp = Path(self._detector.model_path)
            if _mp.exists():
                _model_hash = hashlib.sha256(_mp.read_bytes()).hexdigest()
                logger.info("Model hash (%s): %s", _mp.name, _model_hash[:16])
        except Exception as exc:
            logger.warning("Could not compute model hash: %s", exc)

        # Use callsign in log directory path for multi-instance separation
        _base_log_dir = self._cfg.get("logging", "log_dir", fallback="./output_data/logs")
        _base_image_dir = self._cfg.get("logging", "image_dir", fallback="./output_data/images")
        _base_crop_dir = self._cfg.get("logging", "crop_dir", fallback="./output_data/crops")
        if self._callsign and self._callsign != "HYDRA-1":
            _base_log_dir = str(
                Path(_base_log_dir).parent
                / self._callsign / Path(_base_log_dir).name
            )
            _base_image_dir = str(
                Path(_base_image_dir).parent
                / self._callsign / Path(_base_image_dir).name
            )
            _base_crop_dir = str(
                Path(_base_crop_dir).parent
                / self._callsign / Path(_base_crop_dir).name
            )

        self._det_logger = DetectionLogger(
            log_dir=_base_log_dir,
            log_format=self._cfg.get("logging", "log_format", fallback="jsonl"),
            save_images=self._cfg.getboolean("logging", "save_images", fallback=True),
            image_dir=_base_image_dir,
            image_quality=self._cfg.getint("logging", "image_quality", fallback=90),
            save_crops=self._cfg.getboolean("logging", "save_crops", fallback=False),
            crop_dir=_base_crop_dir,
            max_log_size_mb=self._cfg.getfloat("logging", "max_log_size_mb", fallback=10.0),
            max_log_files=self._cfg.getint("logging", "max_log_files", fallback=20),
            model_hash=_model_hash,
            queue_size=self._cfg.getint("logging", "log_queue_size", fallback=100),
        )

        # Event timeline logger (operator actions + vehicle track)
        self._event_logger = EventLogger(
            log_dir=self._cfg.get(
                "logging", "log_dir", fallback="./output_data/logs"
            ),
            callsign=self._cfg.get("tak", "callsign", fallback="HYDRA-1"),
        )
        self._event_logger.start_mission("default")

        # Web UI
        self._web_enabled = self._cfg.getboolean("web", "enabled", fallback=True)
        self._web_host = self._cfg.get("web", "host", fallback="0.0.0.0")
        self._web_port = self._cfg.getint("web", "port", fallback=8080)

        # RTSP output
        self._rtsp: RTSPServer | None = None
        self._rtsp_enabled = self._cfg.getboolean("rtsp", "enabled", fallback=True)
        self._rtsp_port = self._cfg.getint("rtsp", "port", fallback=8554)
        self._rtsp_mount = self._cfg.get("rtsp", "mount", fallback="/hydra")
        self._rtsp_bitrate = self._cfg.getint("rtsp", "bitrate", fallback=2_000_000)

        # MAVLink video thumbnails
        self._mavlink_video: MAVLinkVideoSender | None = None
        self._mavlink_video_enabled = self._cfg.getboolean(
            "mavlink_video", "enabled", fallback=False
        )

        self._running = False
        self._paused = False
        self._restart_requested = False
        self._total_detections = 0
        self._frame_count = 0
        # Camera loss detection (degraded mode)
        self._cam_fail_count: int = 0
        self._cam_lost: bool = False
        self._CAM_FAIL_THRESHOLD: int = 2
        # Watchdog: last frame processed timestamp
        self._last_frame_time: float = time.monotonic()
        self._watchdog_max_stall_sec: float = self._cfg.getfloat(
            "watchdog", "max_stall_sec", fallback=30.0
        )
        # Low-light brightness monitoring
        self._last_brightness: float = 0.0
        self._low_light: bool = False
        self._low_light_warned: bool = False
        self._low_light_threshold: float = self._cfg.getfloat(
            "detector", "low_light_luminance", fallback=40.0
        )
        # Cache engagement distances (read once at init; avoids config reads on web thread)
        self._strike_distance_m: float = self._cfg.getfloat(
            "mavlink", "strike_distance_m", fallback=20.0
        )
        self._drop_distance_m: float = self._cfg.getfloat(
            "drop", "drop_distance_m", fallback=3.0
        )
        # Pre-populate the nvpmodel cache synchronously at startup (not in the
        # hot loop, so blocking here is fine) then read sysfs stats.
        _query_nvpmodel_sync()
        self._jetson_stats: dict = _read_jetson_stats()
        self._runtime = PipelineRuntime(self)
        self._integrations = PipelineIntegrations(self)
        self._control_adapter = PipelineControlAdapter(self)
        self._init_target_state()

    def _init_target_state(self) -> None:
        """Initialise target-lock state. Safe to call from tests."""
        self._state_lock = threading.Lock()
        self._locked_track_id: Optional[int] = None
        self._lock_mode: Optional[str] = None  # "track" or "strike"
        self._last_track_result = None  # Most recent TrackingResult for web API
        # Only set _servo_tracker if not already initialized (tests may call
        # _init_target_state without __init__). Never reset on restart —
        # it is a hardware object built in __init__.
        if not hasattr(self, "_servo_tracker"):
            self._servo_tracker = None

        # Engagement distances — read from config in __init__; fall back to defaults
        # here so tests that bypass __init__ still have these attributes defined.
        if not hasattr(self, "_strike_distance_m"):
            _cfg = getattr(self, "_cfg", None)
            self._strike_distance_m = (
                _cfg.getfloat("mavlink", "strike_distance_m", fallback=20.0)
                if _cfg is not None else 20.0
            )
        if not hasattr(self, "_drop_distance_m"):
            _cfg = getattr(self, "_cfg", None)
            self._drop_distance_m = (
                _cfg.getfloat("drop", "drop_distance_m", fallback=3.0)
                if _cfg is not None else 3.0
            )

        # Mission tagging state
        self._mission_name: str | None = None
        self._mission_start_time: float | None = None

    def _is_engagement_active(self) -> bool:
        """Return True if autonomous has active tracks or operator has a lock."""
        if self._locked_track_id is not None:
            return True
        if self._autonomous is not None and self._autonomous.has_active_evaluation():
            return True
        return False

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Initialise all subsystems and run the main loop."""
        # OPSEC: wipe previous session data on start if configured
        # Must run BEFORE opening log files to avoid deleting a just-opened hydra.log
        if self._cfg.getboolean("logging", "wipe_on_start", fallback=False):
            import shutil
            log_dir = Path(self._cfg.get("logging", "log_dir", fallback="./output_data/logs"))
            image_dir = Path(self._cfg.get("logging", "image_dir", fallback="./output_data/images"))
            for d in [log_dir, image_dir]:
                if d.exists():
                    for item in d.iterdir():
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item)
                    logger.info("Wiped previous session data from %s", d)

        # Persistent log file for remote debugging access
        if self._cfg.getboolean("logging", "app_log_file", fallback=True):
            from logging.handlers import RotatingFileHandler
            log_dir = Path(self._cfg.get("logging", "log_dir", fallback="./output_data/logs"))
            log_dir.mkdir(parents=True, exist_ok=True)
            app_log_level = getattr(
                logging,
                self._cfg.get("logging", "app_log_level", fallback="INFO").upper(),
                logging.INFO,
            )
            file_handler = RotatingFileHandler(
                str(log_dir / "hydra.log"),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
            )
            file_handler.setLevel(app_log_level)
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
            ))
            logging.getLogger().addHandler(file_handler)
            logger.info("App log file enabled: %s", log_dir / "hydra.log")

        logger.info("=== Hydra Detect v2.0 starting ===")

        # Backup current config on boot for recovery
        from ..web.config_api import backup_on_boot
        backup_on_boot()

        # Validate config
        from ..config_schema import validate_config
        validation = validate_config(self._cfg)
        for err in validation.errors:
            logger.error("Config error: %s", err)
        for warn in validation.warnings:
            logger.warning("Config warning: %s", warn)
        if not validation.ok:
            logger.critical(
                "Config validation failed with %d errors — check config.ini",
                len(validation.errors),
            )
            # Don't hard-exit — let pre-flight checklist show errors on dashboard

        # Auto-update model manifest with any new .pt files
        auto_update_manifest(self._models_dir)

        # Init subsystems — clean up on partial failure
        try:
            self._detector.load()
        except Exception:
            logger.exception("Detector failed to load")
            sys.exit(1)
        logger.info("Detector engine: %s", type(self._detector).__name__)

        self._tracker.init()

        # Wire config safety lock so safety-critical fields are frozen during engagement
        set_engagement_check(self._is_engagement_active)

        # Camera.open() always returns True — it starts a reconnect loop in
        # the background if the device isn't present yet (issue #122). The
        # pipeline enters degraded mode until frames start flowing.
        self._camera.open()

        # Push a placeholder frame so the MJPEG stream has something immediately.
        # May be None at boot if the camera isn't plugged in yet — that's fine.
        preview = self._camera.read()
        if preview is not None:
            stream_state.update_frame(preview)

        if self._mavlink is not None:
            if not self._mavlink.connect():
                logger.warning("MAVLink connection failed — continuing without.")
                self._mavlink = None
                self._osd = None

        # Wire MAVLink command callbacks (lock/strike/unlock over telemetry radio)
        if self._mavlink is not None:
            self._mavlink.set_command_callbacks(
                on_lock=lambda tid: self._handle_target_lock(tid, mode="track"),
                on_strike=self._handle_strike_command,
                on_unlock=self._handle_target_unlock,
            )
            logger.info("MAVLink reader enabled (GPS/telemetry + CMD_USER_1/2/3 + NAMED_VALUE_INT)")

        if self._osd is not None:
            logger.info("FPV OSD enabled (mode=%s, interval=%.2fs)",
                        self._osd.mode, self._cfg.getfloat(
                            "osd", "update_interval", fallback=2.0))

        self._det_logger.start()

        if self._web_enabled:
            # Auto-generate API token on first boot if not configured
            api_token = self._cfg.get("web", "api_token", fallback="").strip()
            if not api_token:
                from ..web.config_api import generate_api_token, get_config_path
                api_token = generate_api_token()
                self._cfg.set("web", "api_token", api_token)
                # Persist to config.ini so the token survives restarts
                try:
                    cfg_path = get_config_path()
                    disk_cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
                    disk_cfg.read(cfg_path)
                    if not disk_cfg.has_section("web"):
                        disk_cfg.add_section("web")
                    disk_cfg.set("web", "api_token", api_token)
                    with open(cfg_path, "w") as f:
                        disk_cfg.write(f)
                    # Restrict config.ini permissions (contains API token)
                    try:
                        os.chmod(cfg_path, 0o600)
                    except OSError:
                        pass  # Docker or non-POSIX — best effort
                    logger.info(
                        "Auto-generated API token (first 8 chars): %s... — saved to %s",
                        api_token[:8], cfg_path,
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not persist auto-generated API token:"
                        " %s — auth DISABLED", exc,
                    )
                    self._cfg.set("web", "api_token", "")
                    api_token = ""
            require_auth = self._cfg.getboolean(
                "web", "require_auth_for_control", fallback=False,
            )
            configure_auth(api_token or None, require_auth_for_control=require_auth)

            # Web password login (empty = disabled, current behavior preserved)
            web_password = self._cfg.get("web", "web_password", fallback="").strip()
            session_timeout = self._cfg.getint("web", "session_timeout_min", fallback=480)
            tls_on = self._cfg.getboolean("web", "tls_enabled", fallback=False)
            configure_web_password(web_password or None, session_timeout, tls_on)

            # Set initial runtime config for web UI
            stream_state.update_runtime_config({
                "threshold": self._cfg.getfloat("detector", "yolo_confidence", fallback=0.45),
                "auto_loiter": self._cfg.getboolean(
                    "mavlink", "auto_loiter_on_detect", fallback=False
                ),
                "alert_classes": list(self._alert_classes) if self._alert_classes else [],
                "active_profile": None,
            })

            # Apply default mission profile if configured
            default_id = self._profiles.get("default_profile")
            if default_id:
                if not self._handle_profile_switch(default_id):
                    logger.warning("Default profile '%s' failed to apply — "
                                   "using config.ini defaults.", default_id)

            # Wire runtime config callbacks through dedicated adapter
            self._integrations.register_web_callbacks(self._control_adapter)

            stream_state.update_stats(
                detector="yolo",
                mavlink=self._mavlink is not None and self._mavlink.connected,
            )

            # TLS: generate self-signed cert if enabled
            tls_enabled = self._cfg.getboolean("web", "tls_enabled", fallback=False)
            ssl_cert = None
            ssl_key = None
            if tls_enabled:
                from ..tls import ensure_tls_cert
                cert_path = self._cfg.get("web", "tls_cert", fallback="")
                key_path = self._cfg.get("web", "tls_key", fallback="")
                if ensure_tls_cert(cert_path, key_path):
                    ssl_cert = cert_path
                    ssl_key = key_path
                else:
                    logger.warning("TLS cert generation failed — falling back to HTTP")

            run_server(self._web_host, self._web_port,
                       ssl_certfile=ssl_cert, ssl_keyfile=ssl_key)

        # Start RTSP output
        if self._rtsp_enabled:
            rtsp_bind = self._cfg.get("rtsp", "bind", fallback="")
            self._rtsp = RTSPServer(
                port=self._rtsp_port,
                mount=self._rtsp_mount,
                bitrate=self._rtsp_bitrate,
                width=self._cfg.getint("camera", "width", fallback=640),
                height=self._cfg.getint("camera", "height", fallback=480),
                bind_address=rtsp_bind,
            )
            if not self._rtsp.start():
                logger.warning("RTSP server failed to start — continuing without.")
                self._rtsp = None

        # Start MAVLink video thumbnails
        if self._mavlink_video_enabled and self._mavlink is not None:
            self._mavlink_video = MAVLinkVideoSender(
                self._mavlink,
                width=self._cfg.getint("mavlink_video", "width", fallback=160),
                height=self._cfg.getint("mavlink_video", "height", fallback=120),
                jpeg_quality=self._cfg.getint("mavlink_video", "jpeg_quality", fallback=20),
                max_fps=self._cfg.getfloat("mavlink_video", "max_fps", fallback=2.0),
                min_fps=self._cfg.getfloat("mavlink_video", "min_fps", fallback=0.2),
                link_budget_bytes_sec=self._cfg.getint(
                    "mavlink_video", "link_budget_bytes_sec",
                    fallback=8000,
                ),
            )
            if not self._mavlink_video.start():
                logger.warning("MAVLink video failed to start — continuing without.")
                self._mavlink_video = None

        # Start RF hunt if configured
        if self._rf_hunt is not None:
            if self._rf_hunt.start():
                logger.info("RF hunt started in background thread")
            else:
                logger.warning("RF hunt failed to start — continuing without")
                self._rf_hunt = None

        # Start TAK/ATAK CoT output
        if self._tak is not None:
            if self._tak.start():
                logger.info("TAK/ATAK CoT output started")
            else:
                logger.warning("TAK output failed to start — continuing without")
                self._tak = None

        # Start MAVLink-based CoT relay
        if self._mav_relay is not None:
            if self._mav_relay.start():
                logger.info("TAK MAVLink relay started")
            else:
                logger.warning("MAVLink relay failed to start — continuing without")
                self._mav_relay = None

        # Start TAK command listener
        if self._tak_input is not None:
            if self._tak_input.start():
                logger.info("TAK command listener started")
                set_tak_input(self._tak_input)
            else:
                logger.warning("TAK command listener failed to start — continuing without")
                self._tak_input = None
                set_tak_input(None)

        # Register signal handlers after init is complete
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        # atexit covers normal sys.exit() and unhandled-exception exits —
        # signal handlers only fire on SIGINT/SIGTERM. See #54.
        atexit.register(self._atexit_safe_servo)

        self._running = True
        # Reset watchdog baseline so the cumulative init time isn't counted
        # as a stall when the watchdog thread starts.
        self._last_frame_time = time.monotonic()
        # Start watchdog thread (daemon — dies with main process)
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="watchdog"
        )
        self._watchdog_thread.start()

        # Restart-capable outer loop
        while True:
            self._restart_requested = False
            self._running = True
            self._run_loop()
            if not self._restart_requested:
                break
            # Restart: shut down subsystems, re-read config, re-init, loop back
            logger.info("=== Pipeline restart requested — reinitializing ===")
            self._shutdown()
            try:
                from ..web.config_api import get_config_path
                self._cfg.read(get_config_path())
                self._detector = _build_detector(self._cfg, self._models_dir)
                self._detector.load()
                self._tracker = ByteTracker(
                    track_thresh=self._cfg.getfloat("tracker", "track_thresh", fallback=0.5),
                    track_buffer=self._cfg.getint("tracker", "track_buffer", fallback=30),
                    match_thresh=self._cfg.getfloat("tracker", "match_thresh", fallback=0.8),
                    frame_rate=self._cfg.getint("camera", "fps", fallback=30),
                )
                self._tracker.init()
                self._camera = Camera(
                    source=self._cfg.get("camera", "source", fallback="auto"),
                    width=self._cfg.getint("camera", "width", fallback=640),
                    height=self._cfg.getint("camera", "height", fallback=480),
                    fps=self._cfg.getint("camera", "fps", fallback=30),
                    source_type=self._cfg.get("camera", "source_type", fallback="auto"),
                    video_standard=self._cfg.get("camera", "video_standard", fallback="ntsc"),
                )
                if not self._camera.open():
                    logger.error("Camera failed to reopen after restart — aborting.")
                    break
                self._det_logger.start()
                self._cam_fail_count = 0
                self._cam_lost = False
                self._total_detections = 0
                self._frame_count = 0
                self._last_frame_time = time.monotonic()
                self._low_light_warned = False
                self._init_target_state()

                # Reconnect MAVLink (may have dropped during operation)
                if self._mavlink is not None and not self._mavlink.connected:
                    logger.info("Reconnecting MAVLink ...")
                    if self._mavlink.connect():
                        logger.info("MAVLink reconnected")
                        self._mavlink.set_command_callbacks(
                            on_lock=lambda tid: self._handle_target_lock(tid, mode="track"),
                            on_strike=self._handle_strike_command,
                            on_unlock=self._handle_target_unlock,
                        )
                    else:
                        logger.warning("MAVLink reconnect failed — continuing without")

                # Restart TAK output if thread died
                if self._tak is not None:
                    _tak_thread = getattr(self._tak, "_thread", None)
                    if _tak_thread is None or not _tak_thread.is_alive():
                        logger.info("Restarting TAK output ...")
                        if self._tak.start():
                            logger.info("TAK output restarted")
                        else:
                            logger.warning("TAK output restart failed")

                # Restart TAK command listener if thread died
                if self._tak_input is not None:
                    _tak_in_thread = getattr(self._tak_input, "_thread", None)
                    if _tak_in_thread is None or not _tak_in_thread.is_alive():
                        logger.info("Restarting TAK command listener ...")
                        if self._tak_input.start():
                            logger.info("TAK command listener restarted")
                        else:
                            logger.warning("TAK command listener restart failed")

                # Restart RF hunt if it was stopped (check thread, not state)
                if self._rf_hunt is not None:
                    _rf_thread = getattr(self._rf_hunt, "_thread", None)
                    if _rf_thread is None or not _rf_thread.is_alive():
                        logger.info("Restarting RF hunt ...")
                        if self._rf_hunt.start():
                            logger.info("RF hunt restarted")
                        else:
                            logger.warning("RF hunt restart failed")

                # Restart RTSP if thread died
                if self._rtsp is not None and not self._rtsp._running:
                    logger.info("Restarting RTSP server ...")
                    if self._rtsp.start():
                        logger.info("RTSP server restarted")
                    else:
                        logger.warning("RTSP restart failed")

                # Restart MAVLink video if thread died
                if self._mavlink_video is not None and not self._mavlink_video._running:
                    logger.info("Restarting MAVLink video ...")
                    if self._mavlink_video.start():
                        logger.info("MAVLink video restarted")
                    else:
                        logger.warning("MAVLink video restart failed")

                logger.info("=== Pipeline restarted successfully ===")
            except Exception as exc:
                logger.error("Restart failed: %s — shutting down.", exc)
                break

    # ------------------------------------------------------------------
    def _watchdog_loop(self) -> None:
        """Background thread: force-exit if pipeline stalls."""
        interval = self._watchdog_max_stall_sec / 2
        while self._running:
            time.sleep(interval)
            if self._paused:
                continue
            # Known camera-loss is a degraded state, not a stall — operator
            # is already notified via STATUSTEXT / preflight. Don't crash-loop
            # while waiting for hardware (issue #122).
            if self._cam_lost:
                continue
            stall = time.monotonic() - self._last_frame_time
            if stall > self._watchdog_max_stall_sec:
                logger.critical(
                    "Watchdog: pipeline stalled for %.1fs — force-exiting.", stall
                )
                import os
                os._exit(1)

    # ------------------------------------------------------------------
    def _check_camera_frame(self):
        """Read a frame and manage camera loss state. Returns frame or None."""
        frame = self._camera.read()
        if frame is None:
            self._cam_fail_count += 1
            if self._cam_fail_count >= self._CAM_FAIL_THRESHOLD and not self._cam_lost:
                self._cam_lost = True
                logger.warning("Camera lost — entering degraded mode.")
                self._event_logger.log_state_change("camera_lost")
                if self._mavlink is not None:
                    self._mavlink.send_statustext(f"{self._callsign}: CAM LOST", severity=4)
                if self._autonomous is not None:
                    self._autonomous.suppressed = True
            return None

        if self._cam_lost:
            self._cam_lost = False
            self._cam_fail_count = 0
            # Watchdog baseline — avoid immediate stall kill when the first
            # detection after restore takes longer than max_stall_sec.
            self._last_frame_time = time.monotonic()
            logger.info("Camera restored — exiting degraded mode.")
            self._event_logger.log_state_change("camera_restored")
            if self._mavlink is not None:
                self._mavlink.send_statustext(f"{self._callsign}: CAM RESTORED", severity=5)
            if self._autonomous is not None:
                self._autonomous.suppressed = False
        else:
            self._cam_fail_count = 0
        return frame

    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Core detect -> track -> alert -> render loop."""
        fps_counter = _FPSCounter()

        while self._running:
            if self._paused:
                time.sleep(0.1)
                continue

            frame = self._check_camera_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            # Brightness monitoring: green channel mean — zero allocation, no copy
            self._last_brightness = float(frame[:, :, 1].mean())
            was_low = self._low_light
            self._low_light = self._last_brightness < self._low_light_threshold
            if self._low_light and not self._low_light_warned:
                self._low_light_warned = True
                logger.warning(
                    "Low light detected: brightness=%.1f (threshold=%.1f)",
                    self._last_brightness, self._low_light_threshold,
                )
                if self._mavlink is not None:
                    self._mavlink.send_statustext(
                        f"HYDRA: LOW LIGHT ({self._last_brightness:.0f})", severity=4
                    )
            elif not self._low_light and was_low:
                self._low_light_warned = False
                logger.info("Light level restored: brightness=%.1f", self._last_brightness)

            # Detect
            det_result = self._detector.detect(frame)
            # Watchdog: stamp immediately after inference so the watchdog knows
            # the pipeline is alive even if render/RTSP/stats takes time.
            self._last_frame_time = time.monotonic()

            # Track
            track_result = self._tracker.update(det_result)
            with self._state_lock:
                self._last_track_result = track_result
                self._total_detections += len(track_result)
                current_lock_id = self._locked_track_id
                current_lock_mode = self._lock_mode

            # Cache GPS and telemetry once per frame — reused in stats and logging
            cached_gps = self._mavlink.get_gps() if self._mavlink is not None else None
            cached_telem: dict = self._mavlink.get_telemetry() if self._mavlink is not None else {}

            # Vehicle track logging at 1 Hz (rate-limited inside)
            if cached_gps is not None and cached_gps.get("fix", 0) >= 3:
                self._event_logger.log_vehicle_track(
                    lat=cached_gps.get("lat", 0.0),
                    lon=cached_gps.get("lon", 0.0),
                    alt=cached_gps.get("alt", 0.0),
                    heading=cached_telem.get("heading"),
                    speed=cached_telem.get("groundspeed"),
                    mode=cached_telem.get("vehicle_mode"),
                )

            # MAVLink alerts (per-label throttled)
            if self._mavlink is not None and len(track_result) > 0:
                # Deduplicate by label — one alert per unique class per frame
                alerted_labels: set[str] = set()
                for track in track_result:
                    if track.label not in alerted_labels:
                        self._mavlink.alert_detection(track.label, track.confidence)
                        alerted_labels.add(track.label)
                if not alerted_labels and self._alert_classes:
                    logger.debug(
                        "No alert-class matches (active=%s)", self._alert_classes
                    )

                # Flash light bar when detections are present (throttled)
                if self._light_bar_enabled:
                    now = time.monotonic()
                    interval = self._light_bar_flash_sec + 0.2
                    if (now - self._light_bar_last_flash) >= interval:
                        self._light_bar_last_flash = now
                        self._mavlink.flash_servo(
                            self._light_bar_channel,
                            self._light_bar_pwm_on,
                            self._light_bar_pwm_off,
                            self._light_bar_flash_sec,
                        )

                # Auto-loiter on detection (throttled)
                if self._mavlink.auto_loiter:
                    now_l = time.monotonic()
                    if (now_l - self._auto_loiter_last) >= self._auto_loiter_cooldown:
                        self._auto_loiter_last = now_l
                        self._mavlink.command_loiter()

            # Geo-tracking map markers
            if self._geo_tracker is not None:
                self._geo_tracker.send(
                    track_result,
                    alert_classes=self._alert_classes,
                    locked_track_id=current_lock_id,
                    frame_w=frame.shape[1],
                )

            # TAK/ATAK CoT output
            if self._tak is not None:
                self._tak.push(track_result, self._alert_classes, current_lock_id)
            if self._mav_relay is not None:
                self._mav_relay.push(track_result, self._alert_classes, current_lock_id)

            # Autonomous strike evaluation
            if self._autonomous is not None and self._mavlink is not None:
                self._autonomous.evaluate(
                    track_result, self._mavlink,
                    self._handle_target_lock, self._handle_strike_command,
                )

            # Update approach controller with locked track
            if self._approach is not None and self._approach.active:
                locked_track_for_approach = None
                if current_lock_id is not None:
                    locked_track_for_approach = track_result.find(
                        current_lock_id,
                    )
                self._approach.update(
                    locked_track_for_approach,
                    frame.shape[1],
                    frame.shape[0],
                )

            if current_lock_id is not None and self._mavlink is not None:
                locked_track = track_result.find(current_lock_id)

                if locked_track is not None:
                    # Compute normalised horizontal error from frame center
                    frame_w = frame.shape[1]
                    cx = (locked_track.x1 + locked_track.x2) / 2.0
                    error_x = (cx - frame_w / 2.0) / (frame_w / 2.0)  # -1..+1

                    # Yaw correction (skip if servo tracker replaces it)
                    if current_lock_mode == "track":
                        if self._servo_tracker is None or not self._servo_tracker.replaces_yaw:
                            self._mavlink.adjust_yaw(error_x)
                    elif current_lock_mode == "strike":
                        if self._servo_tracker is None or not self._servo_tracker.replaces_yaw:
                            self._mavlink.adjust_yaw(error_x, yaw_rate_max=15.0)

                    # Pixel-lock servo tracking
                    if self._servo_tracker is not None:
                        self._servo_tracker.update(error_x)
                else:
                    # In pixel-lock mode, let the guidance controller's
                    # track-loss timeout handle it (sends zero velocity,
                    # then auto-aborts).  Other modes unlock immediately.
                    if current_lock_mode != "pixel_lock":
                        self._handle_target_unlock(reason="lost")

            # Log with GPS data
            self._det_logger.log(track_result, frame, gps=cached_gps)

            # Render overlay
            fps = fps_counter.tick()
            with self._state_lock:
                render_lock_id = self._locked_track_id
                render_lock_mode = self._lock_mode
                total_det = self._total_detections
            # Store raw (un-annotated) frame for Ops HUD canvas overlay
            if self._web_enabled:
                stream_state.update_raw_frame(frame.copy())

            annotated = draw_tracks(
                frame, track_result,
                inference_ms=det_result.inference_ms,
                fps=fps,
                locked_track_id=render_lock_id,
                lock_mode=render_lock_mode,
                alert_classes=self._alert_classes,
            )

            # FPV OSD update (sends to FC onboard OSD chip via MAVLink)
            if self._osd is not None:
                osd_state = build_osd_state(
                    track_result, fps, det_result.inference_ms,
                    render_lock_id, render_lock_mode, cached_gps,
                )
                self._osd.update(osd_state)

            # Push to RTSP stream (independent of web UI)
            if self._rtsp is not None:
                self._rtsp.push_frame(annotated)

            # Push to MAVLink video (thumbnail over telemetry radio)
            if self._mavlink_video is not None:
                self._mavlink_video.push_frame(annotated)

            # Push to web stream
            if self._web_enabled:
                stream_state.update_frame(annotated)
                self._frame_count += 1
                stats_update = {
                    "fps": fps,
                    "inference_ms": det_result.inference_ms,
                    "active_tracks": len(track_result),
                    "total_detections": total_det,
                    "mavlink": self._mavlink is not None and self._mavlink.connected,
                    "brightness": round(self._last_brightness, 1),
                    "low_light": self._low_light,
                    "camera_source": str(self._camera.source),
                    "camera_ok": not self._cam_lost,
                    "callsign": self._callsign,
                    "mission_name": self._mission_name,
                }
                # Expose duplicate callsign flag from TAK input
                if self._tak_input is not None:
                    stats_update["duplicate_callsign"] = self._tak_input._duplicate_callsign
                if self._mavlink is not None:
                    stats_update["vehicle_mode"] = cached_telem.get("vehicle_mode")
                    stats_update["armed"] = cached_telem.get("armed", False)
                    stats_update["battery_v"] = cached_telem.get("battery_v")
                    stats_update["battery_pct"] = cached_telem.get("battery_pct")
                    stats_update["groundspeed"] = cached_telem.get("groundspeed")
                    stats_update["altitude_m"] = cached_telem.get("altitude")
                    stats_update["heading_deg"] = cached_telem.get("heading")
                    stats_update["gps_fix"] = cached_gps.get("fix", 0) if cached_gps else 0
                    stats_update["position"] = self._mavlink.get_position_string()
                    stats_update["is_sim_gps"] = self._mavlink.is_sim_gps
                # Refresh Jetson stats every ~5 seconds (not every frame).
                # sysfs reads (temp, RAM, GPU load) happen inline; the
                # nvpmodel subprocess is dispatched to a background thread
                # so it never blocks the detection loop.
                if self._frame_count % 150 == 0:
                    _refresh_nvpmodel_async()
                    self._jetson_stats = _read_jetson_stats()
                    # VIDEO_STREAM_INFORMATION disabled — causes MP to display
                    # garbled STATUSTEXT. Needs investigation before re-enabling.
                    # See docs/superpowers/specs/2026-03-19-rtsp-output-design.md
                if self._rtsp is not None:
                    stats_update["rtsp_clients"] = self._rtsp.client_count
                if self._mavlink_video is not None:
                    mv_status = self._mavlink_video.get_status()
                    stats_update["mavlink_video_fps"] = mv_status["current_fps"]
                    stats_update["mavlink_video_kbps"] = round(mv_status["bytes_per_sec"] / 1024, 1)
                if self._rf_hunt is not None:
                    stats_update["rf_hunt"] = self._rf_hunt.get_status()
                if self._servo_tracker is not None:
                    stats_update["servo_tracking"] = self._servo_tracker.get_status()
                if self._approach is not None:
                    stats_update["approach"] = self._approach.get_status()
                stats_update.update(self._jetson_stats)
                stream_state.update_stats(**stats_update)

                # Update target lock state for web UI
                # (reuse render_lock_id/render_lock_mode from above)
                if render_lock_id is not None:
                    locked_obj = track_result.find(render_lock_id)
                    stream_state.set_target_lock({
                        "locked": True,
                        "track_id": render_lock_id,
                        "mode": render_lock_mode,
                        "label": locked_obj.label if locked_obj else None,
                    })
                else:
                    stream_state.set_target_lock({
                        "locked": False,
                        "track_id": None,
                        "mode": None,
                        "label": None,
                    })

        self._shutdown()

    # ------------------------------------------------------------------
    # Runtime config handlers (called from web UI)
    # ------------------------------------------------------------------
    def _handle_threshold_change(self, threshold: float) -> None:
        """Update detector confidence threshold at runtime."""
        self._detector.set_threshold(threshold)
        logger.info("Detection threshold updated: %.2f", threshold)
        self._active_profile = None
        stream_state.update_runtime_config({"active_profile": None})

    def _handle_loiter_command(self) -> None:
        """Manual loiter command from web UI."""
        if self._mavlink is not None:
            for mode_name in ("LOITER", "HOLD"):
                if self._mavlink.set_mode(mode_name):
                    logger.info("Manual LOITER command from web UI.")
                    return

    _ALLOWED_MODES = {"AUTO", "RTL", "LOITER", "HOLD", "GUIDED"}

    def _handle_set_mode_command(self, mode: str) -> bool:
        """Set vehicle flight mode from web UI."""
        if mode not in self._ALLOWED_MODES:
            logger.warning("Mode %s not in allowlist.", mode)
            return False
        if self._mavlink is None:
            return False
        success = self._mavlink.set_mode(mode)
        if success:
            self._mavlink.send_statustext(f"{self._callsign}: MODE {mode}", severity=5)
        return success

    def _handle_alert_classes_change(self, classes: list[str]) -> None:
        """Update alert class filter from web UI."""
        if not classes:
            self._alert_classes = None
        else:
            self._alert_classes = set(classes)
        if self._mavlink is not None:
            self._mavlink.alert_classes = self._alert_classes
        stream_state.update_runtime_config({
            "alert_classes": classes,
        })
        logger.info("Alert classes updated: %s", classes or "ALL")
        self._active_profile = None
        stream_state.update_runtime_config({"active_profile": None})

    def _handle_target_lock(self, track_id: int, mode: str = "track") -> bool:
        """Lock onto a tracked object for keep-in-frame or strike."""
        with self._state_lock:
            if self._last_track_result is None:
                return False
            t = self._last_track_result.find(track_id)
            if t is None:
                logger.warning("Target lock failed: track #%d not found.", track_id)
                return False
            self._locked_track_id = track_id
            self._lock_mode = mode
        # Notify autonomous controller of operator lock
        if self._autonomous is not None:
            self._autonomous._operator_locked_track = track_id
        logger.info(
            "Target LOCKED: #%d (%s) mode=%s", track_id, t.label, mode
        )
        if self._mavlink is not None:
            self._mavlink.send_statustext(
                f"{self._callsign}: TGT LOCK #{track_id} {t.label} [{mode.upper()}]", severity=5
            )
        self._event_logger.log_action("lock", {
            "track_id": track_id, "label": t.label, "mode": mode,
        })
        return True

    def _handle_target_unlock(self, reason: str = "") -> None:
        """Release target lock.

        Args:
            reason: If "lost", sends a TGT LOST message instead of generic release.
        """
        with self._state_lock:
            prev_id = self._locked_track_id
            self._locked_track_id = None
            self._lock_mode = None
        # Clear autonomous controller lock
        if self._autonomous is not None:
            self._autonomous._operator_locked_track = None
        if prev_id is not None:
            self._event_logger.log_action("unlock", {
                "track_id": prev_id, "reason": reason or "operator",
            })
            if reason == "lost":
                logger.warning(
                    "Locked target #%d lost from tracker — auto-unlocking.",
                    prev_id,
                )
                if self._mavlink is not None:
                    self._mavlink.send_statustext(
                        f"{self._callsign}: TGT LOST #{prev_id}", severity=4
                    )
            else:
                logger.info("Target UNLOCKED: #%d", prev_id)
                if self._mavlink is not None:
                    self._mavlink.send_statustext(f"{self._callsign}: TGT RELEASED", severity=5)
                    self._mavlink.clear_roi()
        if self._servo_tracker is not None:
            self._servo_tracker.safe()
        # Abort any active approach mode
        if self._approach is not None and self._approach.mode != ApproachMode.IDLE:
            self._approach.abort()

    def _handle_strike_command(self, track_id: int) -> bool:
        """Command vehicle to navigate toward a tracked target.

        Always sets the visual lock to 'strike' mode if the track exists.
        If MAVLink is connected, also estimates target GPS, switches to
        GUIDED, and sends the waypoint. Without MAVLink, the overlay
        still shows strike mode for visual confirmation.
        """
        with self._state_lock:
            if self._last_track_result is None:
                return False
            target_track = self._last_track_result.find(track_id)
        if target_track is None:
            logger.warning("Strike failed: track #%d not found.", track_id)
            return False

        # Save previous lock state so we can revert on failure
        with self._state_lock:
            prev_lock_id = self._locked_track_id
            prev_lock_mode = self._lock_mode
            self._locked_track_id = track_id
            self._lock_mode = "strike"
        logger.info("STRIKE LOCK: #%d (%s)", track_id, target_track.label)
        self._event_logger.log_action("strike", {
            "track_id": track_id, "label": target_track.label,
        })

        # If no MAVLink, just visual — no vehicle command
        if self._mavlink is None:
            logger.warning("Strike visual only — MAVLink not connected.")
            if self._servo_tracker is not None:
                self._servo_tracker.fire_strike()
            return True

        # Compute target bearing from frame position
        if self._camera.has_frame:
            frame_w = self._camera.width
        else:
            frame_w = 640
        cx = (target_track.x1 + target_track.x2) / 2.0
        error_x = (cx - frame_w / 2.0) / (frame_w / 2.0)

        # Estimate target GPS position
        _hfov_default = 120.0 if self._camera.source_type == "analog" else 60.0
        camera_hfov = self._cfg.getfloat("camera", "hfov_deg", fallback=_hfov_default)
        target_pos = self._mavlink.estimate_target_position(
            error_x, self._strike_distance_m, camera_hfov
        )
        if target_pos is None:
            logger.warning("Strike failed: no GPS fix or heading.")
            with self._state_lock:
                self._locked_track_id = prev_lock_id
                self._lock_mode = prev_lock_mode
            # Fire strike servo even if GPS failed — servo doesn't need GPS
            if self._servo_tracker is not None:
                self._servo_tracker.fire_strike()
            return False

        target_lat, target_lon = target_pos

        # Command GUIDED to estimated position
        success = self._mavlink.command_guided_to(target_lat, target_lon)
        if success:
            logger.info(
                "STRIKE initiated: #%d (%s) -> %.6f, %.6f",
                track_id, target_track.label, target_lat, target_lon,
            )
        else:
            logger.error(
                "STRIKE failed: GUIDED command rejected for #%d (%s)",
                track_id, target_track.label,
            )
            with self._state_lock:
                self._locked_track_id = prev_lock_id
                self._lock_mode = prev_lock_mode
        # Fire strike servo (works even without GPS — it's a direct PWM command)
        if self._servo_tracker is not None:
            self._servo_tracker.fire_strike()
        return success

    def _get_preflight(self) -> dict:
        """Run pre-flight checks and return structured results."""
        import os
        checks = []

        # 1. Camera check
        if self._cam_lost:
            checks.append({
                "name": "camera",
                "status": "fail",
                "message": "Camera lost — no video feed",
            })
        else:
            source = str(self._camera.source)
            checks.append({
                "name": "camera",
                "status": "pass",
                "message": f"Camera active on {source}",
            })

        # 2. MAVLink check
        if self._mavlink is not None and self._mavlink.connected:
            gps = self._mavlink.get_gps()
            fix = gps.get("fix", 0) if gps else 0
            if fix >= 3:
                checks.append({
                    "name": "mavlink",
                    "status": "pass",
                    "message": f"MAVLink connected, GPS fix type {fix}",
                })
            else:
                checks.append({
                    "name": "mavlink",
                    "status": "warn",
                    "message": "No GPS fix — autonomous features unavailable",
                })
        elif self._mavlink is not None:
            checks.append({
                "name": "mavlink",
                "status": "fail",
                "message": "MAVLink configured but not connected",
            })
        else:
            checks.append({
                "name": "mavlink",
                "status": "warn",
                "message": "MAVLink disabled in config",
            })

        # 3. Config validation
        from ..config_schema import validate_config, SCHEMA as _SCHEMA
        validation = validate_config(self._cfg)
        if validation.ok and not validation.warnings:
            checks.append({
                "name": "config",
                "status": "pass",
                "message": f"Config valid ({len(_SCHEMA)} sections checked)",
            })
        elif validation.ok:
            checks.append({
                "name": "config",
                "status": "warn",
                "message": f"{len(validation.warnings)} warning(s): {validation.warnings[0]}",
            })
        else:
            checks.append({
                "name": "config",
                "status": "fail",
                "message": f"{len(validation.errors)} error(s): {validation.errors[0]}",
            })

        # 4. Model file check
        model_path = Path(self._detector.model_path)
        if model_path.exists():
            size_mb = model_path.stat().st_size / (1024 * 1024)
            checks.append({
                "name": "models",
                "status": "pass",
                "message": f"{model_path.name} loaded ({size_mb:.1f} MB)",
            })
        else:
            checks.append({
                "name": "models",
                "status": "fail",
                "message": f"Model file not found: {model_path.name}",
            })

        # 5. Disk space check
        log_dir = self._cfg.get("logging", "log_dir", fallback="./output_data/logs")
        try:
            log_path = Path(log_dir)
            if not log_path.exists():
                log_path = Path(".")
            stat = os.statvfs(str(log_path))
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            if free_gb < 1.0:
                checks.append({
                    "name": "disk",
                    "status": "fail",
                    "message": f"{free_gb:.1f} GB free — critically low",
                })
            elif free_gb < 5.0:
                checks.append({
                    "name": "disk",
                    "status": "warn",
                    "message": f"{free_gb:.1f} GB free — consider cleanup",
                })
            else:
                checks.append({
                    "name": "disk",
                    "status": "pass",
                    "message": f"{free_gb:.1f} GB free",
                })
        except OSError:
            checks.append({
                "name": "disk",
                "status": "warn",
                "message": "Could not check disk space",
            })

        # 6. TAK duplicate callsign check
        if self._tak_input is not None and self._tak_input._duplicate_callsign:
            checks.append({
                "name": "callsign",
                "status": "warn",
                "message": "Duplicate callsign detected on TAK network — change in config",
            })

        # 7. Auth hardening check — only warn when web is enabled and
        #    control endpoints are actually exposed without authentication.
        web_enabled = self._cfg.getboolean("web", "enabled", fallback=True)
        has_token = bool(
            self._cfg.get("web", "api_token", fallback="").strip()
        )
        if web_enabled and not has_token:
            checks.append({
                "name": "auth",
                "status": "warn",
                "message": (
                    "Control endpoints are unauthenticated."
                    " Set api_token in config.ini for production use."
                ),
            })

        # Compute overall status (worst of all checks)
        statuses = [c["status"] for c in checks]
        if "fail" in statuses:
            overall = "fail"
        elif "warn" in statuses:
            overall = "warn"
        else:
            overall = "pass"

        return {"checks": checks, "overall": overall}

    def _handle_drop_command(self, track_id: int) -> bool:
        """Command vehicle to approach target and release drop servo on arrival."""
        if self._approach is None:
            logger.warning("Drop failed: approach controller not available")
            return False
        if self._approach.mode != ApproachMode.IDLE:
            logger.warning("Drop failed: approach already active in %s mode",
                           self._approach.mode.value)
            return False

        with self._state_lock:
            if self._last_track_result is None:
                return False
            target_track = self._last_track_result.find(track_id)
        if target_track is None:
            logger.warning("Drop failed: track #%d not found.", track_id)
            return False

        # Estimate target GPS position
        if self._mavlink is None:
            return False
        if self._camera.has_frame:
            frame_w = self._camera.width
        else:
            frame_w = 640
        cx = (target_track.x1 + target_track.x2) / 2.0
        error_x = (cx - frame_w / 2.0) / (frame_w / 2.0)
        _hfov_default = 120.0 if self._camera.source_type == "analog" else 60.0
        camera_hfov = self._cfg.getfloat("camera", "hfov_deg", fallback=_hfov_default)
        target_pos = self._mavlink.estimate_target_position(
            error_x, self._drop_distance_m, camera_hfov,
        )
        if target_pos is None:
            logger.warning("Drop failed: no GPS fix or heading.")
            return False

        target_lat, target_lon = target_pos

        # Set lock mode
        with self._state_lock:
            self._locked_track_id = track_id
            self._lock_mode = "drop"

        success = self._approach.start_drop(track_id, target_lat, target_lon)
        if not success:
            # Rollback lock on failure
            self._handle_target_unlock()
            return False

        logger.info(
            "DROP initiated: #%d (%s) -> %.6f, %.6f",
            track_id, target_track.label, target_lat, target_lon,
        )
        if self._mavlink is not None:
            self._mavlink.send_statustext(
                f"DROP: #{track_id} {target_track.label}", severity=1,
            )
        return True

    def _handle_follow_command(self, track_id: int) -> bool:
        """Command vehicle to follow a tracked target continuously."""
        if self._approach is None:
            logger.warning("Follow failed: approach controller not available")
            return False
        if self._approach.mode != ApproachMode.IDLE:
            logger.warning("Follow failed: approach already active in %s mode",
                           self._approach.mode.value)
            return False

        with self._state_lock:
            if self._last_track_result is None:
                return False
            target_track = self._last_track_result.find(track_id)
        if target_track is None:
            logger.warning("Follow failed: track #%d not found.", track_id)
            return False

        # Set lock mode
        with self._state_lock:
            self._locked_track_id = track_id
            self._lock_mode = "follow"

        success = self._approach.start_follow(track_id)
        if not success:
            # Rollback lock on failure
            self._handle_target_unlock()
            return False

        logger.info("FOLLOW initiated: #%d (%s)", track_id, target_track.label)
        if self._mavlink is not None:
            self._mavlink.send_statustext(
                f"FOLLOW: #{track_id} {target_track.label}", severity=5,
            )
        return True

    def _handle_approach_strike_command(self, track_id: int) -> bool:
        """Command vehicle into continuous strike approach mode."""
        if self._approach is None:
            logger.warning("Approach strike failed: approach controller not available")
            return False
        if self._approach.mode != ApproachMode.IDLE:
            logger.warning("Approach strike failed: approach already active in %s mode",
                           self._approach.mode.value)
            return False

        with self._state_lock:
            if self._last_track_result is None:
                return False
            target_track = self._last_track_result.find(track_id)
        if target_track is None:
            logger.warning("Approach strike failed: track #%d not found.", track_id)
            return False

        # Set lock mode
        with self._state_lock:
            self._locked_track_id = track_id
            self._lock_mode = "strike"

        success = self._approach.start_strike(track_id)
        if not success:
            # Rollback lock on failure
            self._handle_target_unlock()
            return False

        logger.info(
            "APPROACH STRIKE initiated: #%d (%s)", track_id, target_track.label,
        )
        if self._mavlink is not None:
            self._mavlink.send_statustext(
                f"STRIKE ARM: #{track_id} {target_track.label}", severity=1,
            )
        return True

    def _handle_pixel_lock_command(self, track_id: int) -> bool:
        """Command vehicle into pixel-lock visual servoing mode."""
        if self._approach is None:
            logger.warning("Pixel-lock failed: approach controller not available")
            return False
        if self._approach.mode != ApproachMode.IDLE:
            logger.warning(
                "Pixel-lock failed: approach already active in %s mode",
                self._approach.mode.value,
            )
            return False

        with self._state_lock:
            if self._last_track_result is None:
                return False
            target_track = self._last_track_result.find(track_id)
        if target_track is None:
            logger.warning("Pixel-lock failed: track #%d not found.", track_id)
            return False

        # Set lock mode
        with self._state_lock:
            self._locked_track_id = track_id
            self._lock_mode = "pixel_lock"

        success = self._approach.start_pixel_lock(track_id)
        if not success:
            self._handle_target_unlock()
            return False

        logger.info(
            "PIXEL-LOCK initiated: #%d (%s)", track_id, target_track.label,
        )
        if self._mavlink is not None:
            self._mavlink.send_statustext(
                f"PIXEL-LOCK: #{track_id} {target_track.label}", severity=5,
            )
        self._event_logger.log_action("pixel_lock", {
            "track_id": track_id, "label": target_track.label,
        })
        return True

    def _handle_approach_abort(self) -> None:
        """Abort the current approach mode."""
        if self._approach is not None:
            self._approach.abort()
        self._handle_target_unlock()

    def _get_approach_status(self) -> dict:
        """Return approach controller status for the web API."""
        if self._approach is not None:
            return self._approach.get_status()
        return {"mode": "idle", "active": False}

    def _get_active_tracks(self) -> list[dict]:
        """Return current tracked objects as dicts for the web API."""
        with self._state_lock:
            result = self._last_track_result
        if result is None:
            return []
        return [
            {
                "track_id": t.track_id,
                "label": t.label,
                "confidence": round(t.confidence, 3),
                "bbox": [round(t.x1, 1), round(t.y1, 1), round(t.x2, 1), round(t.y2, 1)],
            }
            for t in result
        ]

    def _get_events(self) -> dict:
        """Return recent events from the current mission."""
        events = self._event_logger.get_recent_events(max_events=200)
        status = self._event_logger.get_status()
        return {"events": events, **status}

    def _get_camera_sources(self) -> list[dict]:
        """Return available video sources with current source marked."""
        current = self._camera.source
        sources = list_video_sources(current_source=current)
        for s in sources:
            s["active"] = s["index"] == current
        return sources

    def _handle_camera_switch(self, source: str | int) -> bool:
        """Switch camera source at runtime."""
        return self._camera.switch_source(source)

    def _handle_set_power_mode(self, mode_id: int) -> dict:
        """Set Jetson power mode by ID."""
        result = _set_power_mode(mode_id)
        if result["status"] == "ok":
            # This runs on a web-request thread, not the hot loop — synchronous
            # nvpmodel query is acceptable here to get the freshest reading.
            _query_nvpmodel_sync()
            self._jetson_stats = _read_jetson_stats()
        return result

    def _get_power_modes(self) -> list[dict]:
        """Return available power modes with current marked."""
        modes = _list_power_modes()
        current = self._jetson_stats.get("power_mode", "")
        for m in modes:
            m["active"] = m["name"] == current
        return modes

    def _get_models(self) -> list[dict]:
        """Return available YOLO model files with current marked.

        If a manifest.json exists in the models directory, model metadata
        (classes, description, validation status) is merged into the result.
        """
        models = _list_models("/models", str(self._models_dir), str(self._project_dir))
        current = Path(self._detector.model_path).name

        # Load manifest if available
        manifest = None
        for d in [Path("/models"), self._models_dir]:
            mp = d / MANIFEST_FILENAME
            manifest = load_manifest(mp)
            if manifest is not None:
                break
        manifest_by_name = {e["filename"]: e for e in (manifest or [])}

        for m in models:
            m["active"] = m["name"] == current
            entry = manifest_by_name.get(m["name"])
            if entry:
                m["classes"] = entry.get("classes", [])
                m["description"] = entry.get("description", "")
                m["validated"] = True
            else:
                m["validated"] = manifest is None  # no manifest = all valid
        return models

    def _handle_model_switch(self, model_name: str) -> bool:
        """Switch YOLO model at runtime with optional manifest validation."""
        if Path(model_name).name != model_name:
            logger.warning("Rejected model name with path components: %s", model_name)
            return False

        # Check manifest for validation (if manifest exists)
        model_dirs = [Path("/models"), self._models_dir, self._project_dir]
        for d in model_dirs:
            manifest = load_manifest(d / MANIFEST_FILENAME)
            if manifest is not None:
                entry = next((e for e in manifest if e["filename"] == model_name), None)
                if entry is None:
                    logger.error("Model %s not in manifest — rejecting", model_name)
                    if self._mavlink is not None:
                        self._mavlink.send_statustext(
                            f"{self._callsign}: MODEL REJECTED {model_name}", severity=3
                        )
                    return False
                ok, reason = validate_model(entry, model_dirs)
                if not ok:
                    logger.error("Model validation failed for %s: %s", model_name, reason)
                    if self._mavlink is not None:
                        self._mavlink.send_statustext(
                            f"{self._callsign}: MODEL FAIL {model_name}", severity=3
                        )
                    return False
                break  # only check first manifest found

        # Search /models (Docker), then local models/, then project root
        for candidate_dir in model_dirs:
            candidate = candidate_dir / model_name
            if candidate.exists():
                success = self._detector.switch_model(str(candidate))
                if success:
                    self._active_profile = None
                    stream_state.update_runtime_config({"active_profile": None})
                    # Update detection logger's model hash for chain-of-custody
                    try:
                        import hashlib as _hl
                        new_hash = _hl.sha256(candidate.read_bytes()).hexdigest()
                        self._det_logger.set_model_hash(new_hash)
                        logger.info("Model hash updated (%s): %s", candidate.name, new_hash[:16])
                    except Exception as exc:
                        logger.warning("Could not update model hash: %s", exc)
                return success
        logger.error("Model not found: %s", model_name)
        return False

    def _get_profiles(self) -> dict:
        """Return profiles data for the web API."""
        models_on_disk = {m["name"] for m in self._get_models()}
        profiles_list = []
        for p in self._profiles.get("profiles", []):
            profiles_list.append({
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "model": p["model"],
                "confidence": p["confidence"],
                "alert_classes": p["alert_classes"],
                "auto_loiter_on_detect": p["auto_loiter_on_detect"],
                "model_exists": p["model"] in models_on_disk,
            })
        return {
            "profiles": profiles_list,
            "active_profile": self._active_profile,
        }

    def _handle_profile_switch(self, profile_id: str) -> bool:
        """Apply a mission profile: model + confidence + classes + engagement."""
        profile = get_profile(self._profiles, profile_id)
        if profile is None:
            logger.warning("Unknown profile: %s", profile_id)
            return False

        # 1. Switch model
        model_name = profile["model"]
        if Path(self._detector.model_path).name != model_name:
            if not self._handle_model_switch(model_name):
                logger.error("Profile %s: model switch failed for %s",
                             profile_id, model_name)
                return False

        # 2. Set confidence threshold
        self._detector.set_threshold(profile["confidence"])

        # 3. Set YOLO class filter
        self._detector.set_classes(profile["yolo_classes"])

        # 4. Set alert classes
        alert_classes = profile["alert_classes"]
        if alert_classes:
            self._alert_classes = set(alert_classes)
        else:
            self._alert_classes = None
        if self._mavlink is not None:
            self._mavlink.alert_classes = self._alert_classes

        # 5. Set engagement settings
        if self._mavlink is not None:
            self._mavlink.auto_loiter = profile["auto_loiter_on_detect"]

        # 6. Update runtime config for web UI
        self._active_profile = profile_id
        stream_state.update_runtime_config({
            "threshold": profile["confidence"],
            "alert_classes": alert_classes,
            "auto_loiter": profile["auto_loiter_on_detect"],
            "active_profile": profile_id,
        })
        logger.info("Profile switched: %s (%s)", profile["name"], profile_id)
        return True

    # ------------------------------------------------------------------
    # RF Hunt handlers (web UI)
    # ------------------------------------------------------------------
    def _get_rf_status(self) -> dict:
        """Return RF hunt status for the web API."""
        if self._rf_hunt is not None:
            return self._rf_hunt.get_status()
        return {"state": "unavailable"}

    def _get_rf_rssi_history(self) -> list[dict]:
        """Return RSSI history for the web API."""
        if self._rf_hunt is not None:
            return self._rf_hunt.get_rssi_history()
        return []

    def _handle_rf_start(self, params: dict) -> bool:
        """Start (or restart) an RF hunt from the web UI."""
        # Re-read config so web UI changes (e.g. gps_required) take effect
        from ..web.config_api import get_config_path
        self._cfg.read(get_config_path())

        if self._mavlink is None:
            logger.error("RF hunt requires MAVLink")
            return False

        # Stop any existing hunt
        if self._rf_hunt is not None:
            self._rf_hunt.stop()

        # Auto-start Kismet if no manager exists
        if self._kismet_manager is None:
            self._kismet_manager = KismetManager(
                source=self._cfg.get("rf_homing", "kismet_source", fallback="rtl433-0"),
                capture_dir=self._cfg.get(
                    "rf_homing", "kismet_capture_dir",
                    fallback="./output_data/kismet",
                ),
                host=self._cfg.get("rf_homing", "kismet_host", fallback="http://localhost:2501"),
                user=self._cfg.get("rf_homing", "kismet_user", fallback=""),
                password=self._cfg.get("rf_homing", "kismet_pass", fallback=""),
                log_dir=self._cfg.get("logging", "log_dir", fallback="./output_data/logs"),
                max_capture_mb=self._cfg.getfloat(
                    "rf_homing", "kismet_max_capture_mb",
                    fallback=100.0,
                ),
                auto_spawn=self._cfg.getboolean("rf_homing", "kismet_auto_spawn", fallback=False),
            )
            if not self._kismet_manager.start():
                logger.error("Kismet auto-start failed — RF hunt aborted")
                self._kismet_manager = None
                return False
            logger.info("Kismet auto-started for RF hunt")

        # Build a new controller from the web-submitted params
        self._rf_hunt = _get_rf_hunt_controller_cls()(
            self._mavlink,
            mode=params.get("mode", "wifi"),
            target_bssid=params.get("target_bssid", "").strip() or None,
            target_freq_mhz=float(params.get("target_freq_mhz", 915.0)),
            kismet_host=self._cfg.get("rf_homing", "kismet_host", fallback="http://localhost:2501"),
            kismet_user=self._cfg.get("rf_homing", "kismet_user", fallback=""),
            kismet_pass=self._cfg.get("rf_homing", "kismet_pass", fallback=""),
            search_pattern=params.get("search_pattern", "lawnmower"),
            search_area_m=float(params.get("search_area_m", 100.0)),
            search_spacing_m=float(params.get("search_spacing_m", 20.0)),
            search_alt_m=float(params.get("search_alt_m", 15.0)),
            rssi_threshold_dbm=float(params.get("rssi_threshold_dbm", -80.0)),
            rssi_converge_dbm=float(params.get("rssi_converge_dbm", -40.0)),
            gradient_step_m=float(params.get("gradient_step_m", 5.0)),
            gradient_rotation_deg=self._cfg.getfloat(
                "rf_homing", "gradient_rotation_deg",
                fallback=45.0,
            ),
            rssi_window=self._cfg.getint("rf_homing", "rssi_window", fallback=10),
            poll_interval_sec=self._cfg.getfloat("rf_homing", "poll_interval_sec", fallback=0.5),
            arrival_tolerance_m=self._cfg.getfloat(
                "rf_homing", "arrival_tolerance_m",
                fallback=3.0,
            ),
            kismet_manager=self._kismet_manager,
            gps_required=self._cfg.getboolean("rf_homing", "gps_required", fallback=False),
        )
        return self._rf_hunt.start()

    def _handle_rf_stop(self) -> None:
        """Stop the active RF hunt from the web UI."""
        if self._rf_hunt is not None:
            self._rf_hunt.stop()
            logger.info("RF hunt stopped from web UI")

    def _handle_rtsp_toggle(self, enabled: bool) -> dict:
        """Start or stop the RTSP server at runtime."""
        if enabled and self._rtsp is None:
            rtsp_bind = self._cfg.get(
                "rtsp", "bind", fallback="",
            )
            self._rtsp = RTSPServer(
                port=self._rtsp_port,
                mount=self._rtsp_mount,
                bitrate=self._rtsp_bitrate,
                width=self._cfg.getint("camera", "width", fallback=640),
                height=self._cfg.getint("camera", "height", fallback=480),
                bind_address=rtsp_bind,
            )
            if self._rtsp.start():
                return {"status": "ok", "running": True, "url": self._rtsp.url}
            self._rtsp = None
            return {"status": "error", "message": "RTSP server failed to start"}
        elif not enabled and self._rtsp is not None:
            self._rtsp.stop()
            self._rtsp = None
            return {"status": "ok", "running": False}
        return {
            "status": "ok",
            "running": self._rtsp is not None and self._rtsp.running,
        }

    def _get_rtsp_status(self) -> dict:
        """Return RTSP server status for the web API."""
        if self._rtsp is not None and self._rtsp.running:
            return {
                "enabled": True,
                "running": True,
                "url": self._rtsp.url,
                "clients": self._rtsp.client_count,
            }
        return {
            "enabled": self._rtsp_enabled,
            "running": False,
            "url": f"rtsp://0.0.0.0:{self._rtsp_port}{self._rtsp_mount}",
            "clients": 0,
        }

    def _handle_mavlink_video_toggle(self, enabled: bool) -> dict:
        """Start or stop MAVLink video at runtime."""
        if enabled and self._mavlink_video is None:
            if self._mavlink is None:
                return {"status": "error", "message": "MAVLink not connected"}
            self._mavlink_video = MAVLinkVideoSender(
                self._mavlink,
                width=self._cfg.getint("mavlink_video", "width", fallback=160),
                height=self._cfg.getint("mavlink_video", "height", fallback=120),
                jpeg_quality=self._cfg.getint("mavlink_video", "jpeg_quality", fallback=20),
                max_fps=self._cfg.getfloat("mavlink_video", "max_fps", fallback=2.0),
                min_fps=self._cfg.getfloat("mavlink_video", "min_fps", fallback=0.2),
                link_budget_bytes_sec=self._cfg.getint(
                    "mavlink_video", "link_budget_bytes_sec",
                    fallback=8000,
                ),
            )
            if self._mavlink_video.start():
                return {"status": "ok", "running": True}
            self._mavlink_video = None
            return {"status": "error", "message": "Failed to start"}
        elif not enabled and self._mavlink_video is not None:
            self._mavlink_video.stop()
            self._mavlink_video = None
            return {"status": "ok", "running": False}
        return {"status": "ok", "running": self._mavlink_video is not None}

    def _handle_mavlink_video_tune(self, params: dict) -> dict:
        """Live-tune MAVLink video parameters."""
        if self._mavlink_video is None:
            return {"status": "error", "message": "Not running"}
        if self._mavlink_video.set_params(**params):
            return {"status": "ok", **self._mavlink_video.get_status()}
        return {"status": "error", "message": "Invalid parameter value"}

    def _get_mavlink_video_status(self) -> dict:
        """Return MAVLink video status for web API."""
        if self._mavlink_video is not None:
            return self._mavlink_video.get_status()
        return {
            "enabled": self._mavlink_video_enabled,
            "running": False,
            "width": 0, "height": 0, "quality": 0,
            "current_fps": 0, "bytes_per_sec": 0,
        }

    def _handle_tak_toggle(self, enabled: bool) -> dict:
        """Start or stop TAK CoT output at runtime.

        Honors ``[tak] mode`` so the toggle drives both the direct UDP sink
        and the MAVLink relay sink. Disabling TAK in the UI must stop *every*
        CoT path, otherwise detections keep transmitting over MAVLink.
        """
        tak_mode = self._cfg.get("tak", "mode", fallback="direct").lower()
        want_direct = tak_mode in ("direct", "both")
        want_relay = tak_mode in ("relay", "both")
        emit_interval = self._cfg.getfloat("tak", "emit_interval", fallback=2.0)
        hfov = self._cfg.getfloat("camera", "hfov_deg", fallback=60.0)

        if enabled:
            if self._mavlink is None:
                return {"status": "error", "message": "MAVLink not connected"}
            if want_direct and self._tak is None:
                rtsp_url = None
                tak_host = self._cfg.get("tak", "advertise_host", fallback="").strip()
                if tak_host and self._rtsp is not None and self._rtsp.running:
                    rtsp_url = (
                        f"rtsp://{tak_host}:{self._rtsp_port}{self._rtsp_mount}"
                    )
                self._tak = TAKOutput(
                    mavlink_io=self._mavlink,
                    callsign=self._cfg.get("tak", "callsign", fallback="HYDRA-1"),
                    multicast_group=self._cfg.get(
                        "tak", "multicast_group", fallback="239.2.3.1",
                    ),
                    multicast_port=self._cfg.getint(
                        "tak", "multicast_port", fallback=6969,
                    ),
                    emit_interval=emit_interval,
                    sa_interval=self._cfg.getfloat(
                        "tak", "sa_interval", fallback=5.0,
                    ),
                    stale_detection=self._cfg.getfloat(
                        "tak", "stale_detection", fallback=60.0,
                    ),
                    stale_sa=self._cfg.getfloat(
                        "tak", "stale_sa", fallback=30.0,
                    ),
                    camera_hfov_deg=hfov,
                    unicast_targets=self._cfg.get(
                        "tak", "unicast_targets", fallback="",
                    ),
                    rtsp_url=rtsp_url,
                )
                if not self._tak.start():
                    self._tak = None
                    return {
                        "status": "error",
                        "message": "Failed to start TAK output",
                    }
                set_tak_output(self._tak)

            if want_relay and self._mav_relay is None:
                self._mav_relay = MAVLinkRelayOutput(
                    mavlink_io=self._mavlink,
                    emit_interval=emit_interval,
                    camera_hfov_deg=hfov,
                )
                if not self._mav_relay.start():
                    self._mav_relay = None
                    return {
                        "status": "error",
                        "message": "Failed to start TAK MAVLink relay",
                    }

            return {
                "status": "ok",
                "running": self._tak is not None or self._mav_relay is not None,
            }

        # enabled == False: stop every CoT path, regardless of mode.
        if self._tak is not None:
            self._tak.stop()
            self._tak = None
            set_tak_output(None)
        if self._mav_relay is not None:
            self._mav_relay.stop()
            self._mav_relay = None
        return {"status": "ok", "running": False}

    def _get_tak_status(self) -> dict:
        """Return TAK output status for the web API."""
        if self._tak is not None:
            status = dict(self._tak.get_status())
        else:
            status = {
                "enabled": self._cfg.getboolean("tak", "enabled", fallback=False),
                "running": False,
                "callsign": self._cfg.get("tak", "callsign", fallback="HYDRA-1"),
                "events_sent": 0,
            }
        if self._mav_relay is not None:
            relay_status = self._mav_relay.get_status()
            status["relay_running"] = relay_status["enabled"]
            status["relay_events_sent"] = relay_status["events_sent"]
            if not status.get("running"):
                status["running"] = relay_status["enabled"]
        else:
            status["relay_running"] = False
            status["relay_events_sent"] = 0
        status["mode"] = self._cfg.get("tak", "mode", fallback="direct")
        return status

    def _play_tune(self, tune: str = "alert") -> bool:
        """Play a tune on the Pixhawk buzzer."""
        if self._mavlink is None:
            return False
        return self._mavlink.play_tune(tune)

    def _handle_restart_command(self) -> None:
        """Request a pipeline restart. Sets a flag; the main loop handles the actual restart."""
        logger.info("Restart command received from web UI.")
        self._restart_requested = True
        self._running = False

    def _get_tak_targets(self) -> list[dict]:
        """Return current TAK unicast targets."""
        if self._tak is not None:
            return self._tak.get_unicast_targets()
        return []

    def _add_tak_target(self, host: str, port: int) -> None:
        """Add a TAK unicast target at runtime."""
        if self._tak is not None:
            self._tak.add_unicast_target(host, port)

    def _remove_tak_target(self, host: str, port: int) -> None:
        """Remove a TAK unicast target at runtime."""
        if self._tak is not None:
            self._tak.remove_unicast_target(host, port)

    def _handle_stop_command(self) -> None:
        """Stop the pipeline gracefully from the web UI."""
        logger.info("Stop command received from web UI.")
        self._running = False

    def _handle_pause_command(self) -> None:
        """Pause the detection loop (camera stays open, inference stops)."""
        self._paused = True
        logger.info("Pipeline PAUSED from web UI.")

    def _handle_resume_command(self) -> None:
        """Resume the detection loop."""
        self._paused = False
        logger.info("Pipeline RESUMED from web UI.")

    # ------------------------------------------------------------------
    # Mission tagging
    # ------------------------------------------------------------------
    def _handle_mission_start(self, name: str) -> None:
        """Start a named mission session."""
        self._mission_name = name
        self._mission_start_time = time.monotonic()
        logger.info("Mission STARTED: %s", name)
        if self._mavlink is not None:
            callsign = self._cfg.get("tak", "callsign", fallback="HYDRA-1")
            self._mavlink.send_statustext(
                f"{callsign}: MISSION START - {name}", severity=5
            )

    def _handle_mission_end(self) -> None:
        """End the current mission session."""
        if self._mission_name:
            elapsed = time.monotonic() - (self._mission_start_time or 0)
            logger.info("Mission ENDED: %s (%.0fs)", self._mission_name, elapsed)
            if self._mavlink is not None:
                callsign = self._cfg.get("tak", "callsign", fallback="HYDRA-1")
                self._mavlink.send_statustext(
                    f"{callsign}: MISSION END - {self._mission_name}", severity=5
                )
        self._mission_name = None
        self._mission_start_time = None

    # ------------------------------------------------------------------
    def _shutdown(self) -> None:
        logger.info("Shutting down ...")
        if self._approach is not None:
            self._approach.abort()
        if self._rf_hunt is not None:
            self._rf_hunt.stop()
        if self._kismet_manager is not None:
            self._kismet_manager.stop()
        if self._rtsp is not None:
            self._rtsp.stop()
        if self._mavlink_video is not None:
            self._mavlink_video.stop()
        if self._tak is not None:
            self._tak.stop()
        if self._mav_relay is not None:
            self._mav_relay.stop()
        if self._tak_input is not None:
            self._tak_input.stop()
            set_tak_input(None)
        if self._servo_tracker is not None:
            self._servo_tracker.safe()
        self._camera.close()
        self._detector.unload()
        self._det_logger.stop(timeout=5.0)
        self._event_logger.stop()
        # Send STATUSTEXT shutdown message before closing MAVLink
        callsign = self._cfg.get("tak", "callsign", fallback="HYDRA-1")
        if self._mavlink is not None and self._mavlink.connected:
            try:
                self._mavlink.send_statustext(f"{callsign}: SHUTDOWN", severity=5)
            except Exception:
                pass  # Best-effort on shutdown
        if self._mavlink is not None:
            self._mavlink.close()
        logger.info("=== Hydra Detect stopped ===")

    def _signal_handler(self, sig, frame) -> None:
        logger.info("Signal %s received, stopping.", sig)
        # Best-effort servo safe on signal
        if self._servo_tracker is not None:
            try:
                self._servo_tracker.safe()
            except Exception:
                pass
        self._running = False

    def _atexit_safe_servo(self) -> None:
        """Belt-and-suspenders: drive strike/arm servos to safe on any exit.

        Signal handlers only cover SIGINT/SIGTERM. atexit covers:
        - normal sys.exit()
        - unhandled exceptions (propagated to interpreter exit)
        - end-of-main-thread exits

        Does nothing on os._exit() / SIGKILL — those bypass atexit. Idempotent
        with _signal_handler and _shutdown since servo.safe() just re-drives
        PWM to the same safe value.
        """
        if self._servo_tracker is not None:
            try:
                self._servo_tracker.safe()
            except Exception:
                pass  # best-effort — interpreter is tearing down


class _FPSCounter:
    """Simple rolling FPS counter."""

    def __init__(self, window: int = 30):
        self._times: deque[float] = deque(maxlen=window)

    def tick(self) -> float:
        now = time.perf_counter()
        self._times.append(now)
        if len(self._times) < 2:
            return 0.0
        return (len(self._times) - 1) / (self._times[-1] - self._times[0])
