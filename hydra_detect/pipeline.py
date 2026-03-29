"""Main detection pipeline — orchestrates camera, detector, tracker, and outputs."""

from __future__ import annotations

import configparser
import logging
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .autonomous import AutonomousController, parse_polygon
from .servo_tracker import ServoTracker
from .camera import Camera, list_video_sources
from .rf.hunt import RFHuntController
from .rf.kismet_manager import KismetManager
from .detection_logger import DetectionLogger
from .model_manifest import load_manifest, validate_model, MANIFEST_FILENAME
from .detectors.yolo_detector import YOLODetector
from .mavlink_io import MAVLinkIO
from .osd import FpvOsd, build_osd_state
from .overlay import draw_tracks
from .system import (
    list_models as _list_models,
    list_power_modes as _list_power_modes,
    query_nvpmodel_sync as _query_nvpmodel_sync,
    read_jetson_stats as _read_jetson_stats,
    refresh_nvpmodel_async as _refresh_nvpmodel_async,
    set_power_mode as _set_power_mode,
)
from .rtsp_server import RTSPServer
from .mavlink_video import MAVLinkVideoSender
from .tak.tak_input import TAKInput
from .tak.tak_output import TAKOutput
from .tracker import ByteTracker
from .profiles import get_profile, load_profiles
from .web.config_api import set_config_path
from .web.server import configure_auth, run_server, stream_state

logger = logging.getLogger(__name__)


def _build_detector(cfg: configparser.ConfigParser, models_dir: Path | None = None) -> YOLODetector:
    """Build a YOLO detector from config."""
    classes_raw = cfg.get("detector", "yolo_classes", fallback="")
    classes = None
    if classes_raw.strip():
        try:
            classes = [int(c.strip()) for c in classes_raw.split(",") if c.strip()]
            if any(c < 0 for c in classes):
                logger.warning("Negative YOLO class IDs removed from filter list.")
                classes = [c for c in classes if c >= 0]
            classes = classes or None
        except ValueError:
            logger.error("Invalid yolo_classes config (comma-separated ints): %s",
                         classes_raw)
            classes = None
    model_name = cfg.get("detector", "yolo_model", fallback="yolov8n.pt")
    # Search for the model in /models (Docker), then local models/, then project root
    model_path = model_name
    project_dir = models_dir.parent if models_dir is not None else None
    for candidate_dir in [Path("/models"), models_dir, project_dir]:
        if candidate_dir is not None:
            candidate = candidate_dir / model_name
            if candidate.exists():
                model_path = str(candidate)
                break
    imgsz_raw = cfg.get("detector", "yolo_imgsz", fallback="")
    imgsz = int(imgsz_raw) if imgsz_raw.strip() else None
    return YOLODetector(
        model_path=model_path,
        confidence=cfg.getfloat("detector", "yolo_confidence", fallback=0.45),
        classes=classes,
        imgsz=imgsz,
    )


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
                            "Vehicle config key %r missing section prefix (expected section.option)",
                            key,
                        )
            else:
                logger.error(
                    "Vehicle profile %r not found (no [%s] section in config)",
                    vehicle, vehicle_section,
                )

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
            source=self._cfg.get("camera", "source", fallback="0"),
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
        if self._cfg.getboolean("mavlink", "enabled", fallback=False):
            self._mavlink = MAVLinkIO(
                connection_string=self._cfg.get(
                    "mavlink", "connection_string", fallback="/dev/ttyACM0"
                ),
                baud=self._cfg.getint("mavlink", "baud", fallback=115200),
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
                    l.strip() for l in self._cfg.get(
                        "alerts", "priority_labels", fallback=""
                    ).split(",") if l.strip()
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
                    "osd", "update_interval", fallback=0.2
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
        if (
            self._mavlink is not None
            and self._cfg.getboolean("alerts", "light_bar_enabled", fallback=False)
        ):
            self._light_bar_enabled = True
            self._light_bar_channel = self._cfg.getint("alerts", "light_bar_channel", fallback=4)
            self._light_bar_pwm_on = self._cfg.getint("alerts", "light_bar_pwm_on", fallback=1900)
            self._light_bar_pwm_off = self._cfg.getint("alerts", "light_bar_pwm_off", fallback=1100)
            self._light_bar_flash_sec = self._cfg.getfloat("alerts", "light_bar_flash_sec", fallback=0.5)
            logger.info(
                "Light bar enabled: channel=%d, on=%d, off=%d, flash=%.1fs",
                self._light_bar_channel, self._light_bar_pwm_on,
                self._light_bar_pwm_off, self._light_bar_flash_sec,
            )

        # Pixel-lock servo tracker
        self._servo_tracker: ServoTracker | None = None
        if (
            self._mavlink is not None
            and self._cfg.getboolean("servo_tracking", "enabled", fallback=False)
        ):
            pan_ch = self._cfg.getint("servo_tracking", "pan_channel", fallback=1)
            strike_ch = self._cfg.getint("servo_tracking", "strike_channel", fallback=2)
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
                    pan_pwm_center=self._cfg.getint("servo_tracking", "pan_pwm_center", fallback=1500),
                    pan_pwm_range=self._cfg.getint("servo_tracking", "pan_pwm_range", fallback=500),
                    pan_invert=self._cfg.getboolean("servo_tracking", "pan_invert", fallback=False),
                    pan_dead_zone=self._cfg.getfloat("servo_tracking", "pan_dead_zone", fallback=0.05),
                    pan_smoothing=self._cfg.getfloat("servo_tracking", "pan_smoothing", fallback=0.3),
                    strike_channel=strike_ch,
                    strike_pwm_fire=self._cfg.getint("servo_tracking", "strike_pwm_fire", fallback=1900),
                    strike_pwm_safe=self._cfg.getint("servo_tracking", "strike_pwm_safe", fallback=1100),
                    strike_duration=self._cfg.getfloat("servo_tracking", "strike_duration", fallback=0.5),
                    replaces_yaw=self._cfg.getboolean("servo_tracking", "replaces_yaw", fallback=False),
                )
                logger.info(
                    "Pixel-lock servo tracking ENABLED: pan_ch=%d, strike_ch=%d, replaces_yaw=%s",
                    pan_ch, strike_ch, self._servo_tracker.replaces_yaw,
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
                geofence_radius_m=self._cfg.getfloat("autonomous", "geofence_radius_m", fallback=100.0),
                geofence_polygon=polygon,
                min_confidence=self._cfg.getfloat("autonomous", "min_confidence", fallback=0.85),
                min_track_frames=self._cfg.getint("autonomous", "min_track_frames", fallback=5),
                allowed_classes=allowed_classes,
                strike_cooldown_sec=self._cfg.getfloat("autonomous", "strike_cooldown_sec", fallback=30.0),
                allowed_vehicle_modes=allowed_modes,
                gps_max_stale_sec=self._cfg.getfloat("autonomous", "gps_max_stale_sec", fallback=2.0),
                require_operator_lock=self._cfg.getboolean(
                    "autonomous", "require_operator_lock", fallback=False
                ),
            )
            logger.info(
                "Autonomous strike ENABLED: fence_radius=%.0fm, min_conf=%.2f, "
                "min_frames=%d, classes=%s",
                self._cfg.getfloat("autonomous", "geofence_radius_m", fallback=100.0),
                self._cfg.getfloat("autonomous", "min_confidence", fallback=0.85),
                self._cfg.getint("autonomous", "min_track_frames", fallback=5),
                classes_raw or "NONE (fail-closed)",
            )

        # RF homing controller
        self._rf_hunt: RFHuntController | None = None
        self._kismet_manager: KismetManager | None = None
        if self._cfg.getboolean("rf_homing", "enabled", fallback=False):
            if self._mavlink is not None:
                kismet_host = self._cfg.get("rf_homing", "kismet_host", fallback="http://localhost:2501")
                self._kismet_manager = KismetManager(
                    source=self._cfg.get("rf_homing", "kismet_source", fallback="rtl433-0"),
                    capture_dir=self._cfg.get("rf_homing", "kismet_capture_dir", fallback="./output_data/kismet"),
                    host=kismet_host,
                    user=self._cfg.get("rf_homing", "kismet_user", fallback=""),
                    password=self._cfg.get("rf_homing", "kismet_pass", fallback=""),
                    log_dir=self._cfg.get("logging", "log_dir", fallback="./output_data/logs"),
                    max_capture_mb=self._cfg.getfloat("rf_homing", "kismet_max_capture_mb", fallback=100.0),
                    auto_spawn=self._cfg.getboolean("rf_homing", "kismet_auto_spawn", fallback=False),
                )
                if self._kismet_manager.start():
                    self._rf_hunt = RFHuntController(
                        self._mavlink,
                        mode=self._cfg.get("rf_homing", "mode", fallback="wifi"),
                        target_bssid=self._cfg.get("rf_homing", "target_bssid", fallback="").strip() or None,
                        target_freq_mhz=self._cfg.getfloat("rf_homing", "target_freq_mhz", fallback=915.0),
                        kismet_host=kismet_host,
                        kismet_user=self._cfg.get("rf_homing", "kismet_user", fallback=""),
                        kismet_pass=self._cfg.get("rf_homing", "kismet_pass", fallback=""),
                        search_pattern=self._cfg.get("rf_homing", "search_pattern", fallback="lawnmower"),
                        search_area_m=self._cfg.getfloat("rf_homing", "search_area_m", fallback=100.0),
                        search_spacing_m=self._cfg.getfloat("rf_homing", "search_spacing_m", fallback=20.0),
                        search_alt_m=self._cfg.getfloat("rf_homing", "search_alt_m", fallback=15.0),
                        rssi_threshold_dbm=self._cfg.getfloat("rf_homing", "rssi_threshold_dbm", fallback=-80.0),
                        rssi_converge_dbm=self._cfg.getfloat("rf_homing", "rssi_converge_dbm", fallback=-40.0),
                        rssi_window=self._cfg.getint("rf_homing", "rssi_window", fallback=10),
                        gradient_step_m=self._cfg.getfloat("rf_homing", "gradient_step_m", fallback=5.0),
                        gradient_rotation_deg=self._cfg.getfloat("rf_homing", "gradient_rotation_deg", fallback=45.0),
                        poll_interval_sec=self._cfg.getfloat("rf_homing", "poll_interval_sec", fallback=0.5),
                        arrival_tolerance_m=self._cfg.getfloat("rf_homing", "arrival_tolerance_m", fallback=3.0),
                        kismet_manager=self._kismet_manager,
                    )
                    logger.info(
                        "RF homing configured: mode=%s target=%s",
                        self._cfg.get("rf_homing", "mode", fallback="wifi"),
                        self._cfg.get("rf_homing", "target_bssid", fallback="")
                        or f"{self._cfg.getfloat('rf_homing', 'target_freq_mhz', fallback=915.0)}MHz",
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
            from .geo_tracking import GeoTracker
            self._geo_tracker = GeoTracker(
                self._mavlink,
                camera_hfov_deg=self._cfg.getfloat("camera", "hfov_deg", fallback=60.0),
                min_interval=self._cfg.getfloat("mavlink", "geo_tracking_interval", fallback=2.0),
            )
            logger.info("Geo-tracking enabled (CAMERA_TRACKING_GEO_STATUS)")

        # TAK / ATAK CoT output
        self._tak: TAKOutput | None = None
        if self._cfg.getboolean("tak", "enabled", fallback=False):
            if self._mavlink is not None:
                rtsp_url = None
                tak_host = self._cfg.get("tak", "advertise_host", fallback="").strip()
                if tak_host and self._cfg.getboolean("rtsp", "enabled", fallback=True):
                    rtsp_port = self._cfg.getint("rtsp", "port", fallback=8554)
                    rtsp_mount = self._cfg.get("rtsp", "mount", fallback="/hydra")
                    rtsp_url = f"rtsp://{tak_host}:{rtsp_port}{rtsp_mount}"
                self._tak = TAKOutput(
                    mavlink_io=self._mavlink,
                    callsign=self._cfg.get("tak", "callsign", fallback="HYDRA-1"),
                    multicast_group=self._cfg.get("tak", "multicast_group", fallback="239.2.3.1"),
                    multicast_port=self._cfg.getint("tak", "multicast_port", fallback=6969),
                    emit_interval=self._cfg.getfloat("tak", "emit_interval", fallback=2.0),
                    sa_interval=self._cfg.getfloat("tak", "sa_interval", fallback=5.0),
                    stale_detection=self._cfg.getfloat("tak", "stale_detection", fallback=60.0),
                    stale_sa=self._cfg.getfloat("tak", "stale_sa", fallback=30.0),
                    camera_hfov_deg=self._cfg.getfloat("camera", "hfov_deg", fallback=60.0),
                    unicast_targets=self._cfg.get("tak", "unicast_targets", fallback=""),
                    rtsp_url=rtsp_url,
                )
                logger.info(
                    "TAK/ATAK output configured: %s:%d callsign=%s",
                    self._cfg.get("tak", "multicast_group", fallback="239.2.3.1"),
                    self._cfg.getint("tak", "multicast_port", fallback=6969),
                    self._cfg.get("tak", "callsign", fallback="HYDRA-1"),
                )
            else:
                logger.warning("TAK output requires MAVLink for GPS — skipping")

        # TAK / ATAK CoT command listener
        self._tak_input: TAKInput | None = None
        if (
            self._cfg.getboolean("tak", "enabled", fallback=False)
            and self._cfg.getboolean("tak", "listen_commands", fallback=False)
        ):
            self._tak_input = TAKInput(
                listen_port=self._cfg.getint("tak", "listen_port", fallback=6969),
                multicast_group=self._cfg.get("tak", "multicast_group", fallback="239.2.3.1"),
                on_lock=lambda tid: self._handle_target_lock(tid, mode="track"),
                on_strike=self._handle_strike_command,
                on_unlock=self._handle_target_unlock,
            )
            logger.info(
                "TAK command listener configured: port=%d",
                self._cfg.getint("tak", "listen_port", fallback=6969),
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

        self._det_logger = DetectionLogger(
            log_dir=self._cfg.get("logging", "log_dir", fallback="/data/logs"),
            log_format=self._cfg.get("logging", "log_format", fallback="jsonl"),
            save_images=self._cfg.getboolean("logging", "save_images", fallback=True),
            image_dir=self._cfg.get("logging", "image_dir", fallback="/data/images"),
            image_quality=self._cfg.getint("logging", "image_quality", fallback=90),
            save_crops=self._cfg.getboolean("logging", "save_crops", fallback=False),
            crop_dir=self._cfg.get("logging", "crop_dir", fallback="/data/crops"),
            max_log_size_mb=self._cfg.getfloat("logging", "max_log_size_mb", fallback=10.0),
            max_log_files=self._cfg.getint("logging", "max_log_files", fallback=20),
            model_hash=_model_hash,
        )

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
        self._total_detections = 0
        self._frame_count = 0
        # Camera loss detection (degraded mode)
        self._cam_fail_count: int = 0
        self._cam_lost: bool = False
        self._CAM_FAIL_THRESHOLD: int = 2
        # Watchdog: last frame processed timestamp
        self._last_frame_time: float = time.monotonic()
        self._watchdog_max_stall_sec: float = float(
            self._cfg.get("watchdog", "max_stall_sec", fallback="30")
        )
        # Pre-populate the nvpmodel cache synchronously at startup (not in the
        # hot loop, so blocking here is fine) then read sysfs stats.
        _query_nvpmodel_sync()
        self._jetson_stats: dict = _read_jetson_stats()
        self._init_target_state()

    def _init_target_state(self) -> None:
        """Initialise target-lock state. Safe to call from tests."""
        self._state_lock = threading.Lock()
        self._locked_track_id: Optional[int] = None
        self._lock_mode: Optional[str] = None  # "track" or "strike"
        self._last_track_result = None  # Most recent TrackingResult for web API
        self._servo_tracker = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Initialise all subsystems and run the main loop."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

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

        # Init subsystems — clean up on partial failure
        try:
            self._detector.load()
        except Exception as exc:
            logger.error("Detector failed to load: %s", exc)
            sys.exit(1)
        logger.info("Detector engine: yolo")

        self._tracker.init()

        if not self._camera.open():
            logger.error("Failed to open camera — aborting.")
            self._detector.unload()
            sys.exit(1)

        # Push a placeholder frame so the MJPEG stream has something immediately
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
                            "osd", "update_interval", fallback=0.2))

        self._det_logger.start()

        if self._web_enabled:
            # Configure API auth
            api_token = self._cfg.get("web", "api_token", fallback="").strip()
            if not api_token:
                logger.warning(
                    "WARNING: No API token configured — web control endpoints are "
                    "unauthenticated. Set [web] api_token in config.ini for production use."
                )
            configure_auth(api_token or None)

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

            # Wire runtime config callbacks
            stream_state.set_callbacks(
                on_threshold_change=self._handle_threshold_change,
                on_loiter_command=self._handle_loiter_command,
                on_target_lock=self._handle_target_lock,
                on_target_unlock=self._handle_target_unlock,
                on_strike_command=self._handle_strike_command,
                get_recent_detections=self._det_logger.get_recent,
                get_active_tracks=self._get_active_tracks,
                on_stop_command=self._handle_stop_command,
                on_pause_command=self._handle_pause_command,
                on_resume_command=self._handle_resume_command,
                get_camera_sources=self._get_camera_sources,
                on_camera_switch=self._handle_camera_switch,
                on_set_power_mode=self._handle_set_power_mode,
                get_power_modes=self._get_power_modes,
                get_models=self._get_models,
                on_model_switch=self._handle_model_switch,
                get_log_dir=lambda: self._cfg.get("logging", "log_dir", fallback="/data/logs"),
                get_image_dir=lambda: self._cfg.get("logging", "image_dir", fallback="/data/images"),
                get_rf_status=self._get_rf_status,
                get_rf_rssi_history=self._get_rf_rssi_history,
                on_rf_start=self._handle_rf_start,
                on_rf_stop=self._handle_rf_stop,
                on_set_mode_command=self._handle_set_mode_command,
                on_alert_classes_change=self._handle_alert_classes_change,
                get_class_names=self._detector.get_class_names,
                on_rtsp_toggle=self._handle_rtsp_toggle,
                get_rtsp_status=self._get_rtsp_status,
                on_mavlink_video_toggle=self._handle_mavlink_video_toggle,
                on_mavlink_video_tune=self._handle_mavlink_video_tune,
                get_mavlink_video_status=self._get_mavlink_video_status,
                on_tak_toggle=self._handle_tak_toggle,
                get_tak_status=self._get_tak_status,
                get_profiles=self._get_profiles,
                on_profile_switch=self._handle_profile_switch,
            )

            stream_state.update_stats(
                detector="yolo",
                mavlink=self._mavlink is not None and self._mavlink.connected,
            )
            run_server(self._web_host, self._web_port)

        # Start RTSP output
        if self._rtsp_enabled:
            self._rtsp = RTSPServer(
                port=self._rtsp_port,
                mount=self._rtsp_mount,
                bitrate=self._rtsp_bitrate,
                width=self._cfg.getint("camera", "width", fallback=640),
                height=self._cfg.getint("camera", "height", fallback=480),
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
                link_budget_bytes_sec=self._cfg.getint("mavlink_video", "link_budget_bytes_sec", fallback=8000),
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

        # Start TAK command listener
        if self._tak_input is not None:
            if self._tak_input.start():
                logger.info("TAK command listener started")
            else:
                logger.warning("TAK command listener failed to start — continuing without")
                self._tak_input = None

        # Register signal handlers after init is complete
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self._running = True
        # Start watchdog thread (daemon — dies with main process)
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="watchdog"
        )
        self._watchdog_thread.start()
        self._run_loop()

    # ------------------------------------------------------------------
    def _watchdog_loop(self) -> None:
        """Background thread: force-exit if pipeline stalls."""
        interval = self._watchdog_max_stall_sec / 2
        while self._running:
            time.sleep(interval)
            if self._paused:
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
                if self._mavlink is not None:
                    self._mavlink.send_statustext("HYDRA: CAM LOST", severity=4)
                if self._autonomous is not None:
                    self._autonomous.suppressed = True
            return None

        if self._cam_lost:
            self._cam_lost = False
            self._cam_fail_count = 0
            logger.info("Camera restored — exiting degraded mode.")
            if self._mavlink is not None:
                self._mavlink.send_statustext("HYDRA: CAM RESTORED", severity=5)
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

            # Detect
            det_result = self._detector.detect(frame)

            # Track
            track_result = self._tracker.update(det_result)
            with self._state_lock:
                self._last_track_result = track_result
                self._total_detections += len(track_result)
                current_lock_id = self._locked_track_id
                current_lock_mode = self._lock_mode

            # Get GPS state for logging and alerts
            gps = None
            if self._mavlink is not None:
                gps = self._mavlink.get_gps()

            # MAVLink alerts (per-label throttled)
            if self._mavlink is not None and len(track_result) > 0:
                alert_sent = False
                for track in track_result:
                    self._mavlink.alert_detection(track.label, track.confidence)
                    alert_sent = True

                # Flash light bar when detections are present (throttled)
                if alert_sent and self._light_bar_enabled:
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

                # Auto-loiter on detection
                if self._mavlink.auto_loiter:
                    self._mavlink.command_loiter()

            # Geo-tracking map markers
            if self._geo_tracker is not None:
                self._geo_tracker.send(
                    track_result,
                    alert_classes=self._alert_classes,
                    locked_track_id=current_lock_id,
                )

            # TAK/ATAK CoT output
            if self._tak is not None:
                self._tak.push(track_result, self._alert_classes, current_lock_id)

            # Autonomous strike evaluation
            if self._autonomous is not None and self._mavlink is not None:
                self._autonomous.evaluate(
                    track_result, self._mavlink,
                    self._handle_target_lock, self._handle_strike_command,
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
                    self._handle_target_unlock(reason="lost")

            # Log with GPS data
            self._det_logger.log(track_result, frame, gps=gps)

            # Render overlay
            self._last_frame_time = time.monotonic()
            fps = fps_counter.tick()
            with self._state_lock:
                render_lock_id = self._locked_track_id
                render_lock_mode = self._lock_mode
                total_det = self._total_detections
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
                    render_lock_id, render_lock_mode, gps,
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
                    "camera_source": str(self._camera.source),
                    "camera_ok": not self._cam_lost,
                }
                if self._mavlink is not None:
                    telem = self._mavlink.get_telemetry()
                    stats_update["vehicle_mode"] = telem.get("vehicle_mode")
                    stats_update["armed"] = telem.get("armed", False)
                    stats_update["battery_v"] = telem.get("battery_v")
                    stats_update["battery_pct"] = telem.get("battery_pct")
                    stats_update["groundspeed"] = telem.get("groundspeed")
                    stats_update["altitude_m"] = telem.get("altitude")
                    stats_update["heading_deg"] = telem.get("heading")
                    gps_data = self._mavlink.get_gps()
                    stats_update["gps_fix"] = gps_data.get("fix", 0)
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
            self._mavlink.send_statustext(f"MODE CMD: {mode}", severity=5)
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
                f"TGT LOCK: #{track_id} {t.label} [{mode.upper()}]", severity=5
            )
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
            if reason == "lost":
                logger.warning(
                    "Locked target #%d lost from tracker — auto-unlocking.",
                    prev_id,
                )
                if self._mavlink is not None:
                    self._mavlink.send_statustext(
                        f"TGT LOST: #{prev_id} — lock released", severity=4
                    )
            else:
                logger.info("Target UNLOCKED: #%d", prev_id)
                if self._mavlink is not None:
                    self._mavlink.send_statustext("TGT LOCK RELEASED", severity=5)
                    self._mavlink.clear_roi()
        if self._servo_tracker is not None:
            self._servo_tracker.safe()

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
        approach_dist = self._cfg.getfloat("mavlink", "strike_distance_m", fallback=20.0)
        _hfov_default = 120.0 if self._camera.source_type == "analog" else 60.0
        camera_hfov = self._cfg.getfloat("camera", "hfov_deg", fallback=_hfov_default)
        target_pos = self._mavlink.estimate_target_position(
            error_x, approach_dist, camera_hfov
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
                            f"MODEL REJECTED: {model_name} not in manifest", severity=3
                        )
                    return False
                ok, reason = validate_model(entry, model_dirs)
                if not ok:
                    logger.error("Model validation failed for %s: %s", model_name, reason)
                    if self._mavlink is not None:
                        self._mavlink.send_statustext(
                            f"MODEL FAIL: {model_name} ({reason[:30]})", severity=3
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
        from .web.config_api import get_config_path
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
                capture_dir=self._cfg.get("rf_homing", "kismet_capture_dir", fallback="./output_data/kismet"),
                host=self._cfg.get("rf_homing", "kismet_host", fallback="http://localhost:2501"),
                user=self._cfg.get("rf_homing", "kismet_user", fallback=""),
                password=self._cfg.get("rf_homing", "kismet_pass", fallback=""),
                log_dir=self._cfg.get("logging", "log_dir", fallback="./output_data/logs"),
                max_capture_mb=self._cfg.getfloat("rf_homing", "kismet_max_capture_mb", fallback=100.0),
                auto_spawn=self._cfg.getboolean("rf_homing", "kismet_auto_spawn", fallback=False),
            )
            if not self._kismet_manager.start():
                logger.error("Kismet auto-start failed — RF hunt aborted")
                self._kismet_manager = None
                return False
            logger.info("Kismet auto-started for RF hunt")

        # Build a new controller from the web-submitted params
        self._rf_hunt = RFHuntController(
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
            gradient_rotation_deg=self._cfg.getfloat("rf_homing", "gradient_rotation_deg", fallback=45.0),
            rssi_window=self._cfg.getint("rf_homing", "rssi_window", fallback=10),
            poll_interval_sec=self._cfg.getfloat("rf_homing", "poll_interval_sec", fallback=0.5),
            arrival_tolerance_m=self._cfg.getfloat("rf_homing", "arrival_tolerance_m", fallback=3.0),
            kismet_manager=self._kismet_manager,
            gps_required=self._cfg.getboolean("rf_homing", "gps_required", fallback=True),
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
            self._rtsp = RTSPServer(
                port=self._rtsp_port,
                mount=self._rtsp_mount,
                bitrate=self._rtsp_bitrate,
                width=self._cfg.getint("camera", "width", fallback=640),
                height=self._cfg.getint("camera", "height", fallback=480),
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
                link_budget_bytes_sec=self._cfg.getint("mavlink_video", "link_budget_bytes_sec", fallback=8000),
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
        """Start or stop TAK CoT output at runtime."""
        if enabled and self._tak is None:
            if self._mavlink is None:
                return {"status": "error", "message": "MAVLink not connected"}
            rtsp_url = None
            tak_host = self._cfg.get("tak", "advertise_host", fallback="").strip()
            if tak_host and self._rtsp is not None and self._rtsp.running:
                rtsp_url = f"rtsp://{tak_host}:{self._rtsp_port}{self._rtsp_mount}"
            self._tak = TAKOutput(
                mavlink_io=self._mavlink,
                callsign=self._cfg.get("tak", "callsign", fallback="HYDRA-1"),
                multicast_group=self._cfg.get("tak", "multicast_group", fallback="239.2.3.1"),
                multicast_port=self._cfg.getint("tak", "multicast_port", fallback=6969),
                emit_interval=self._cfg.getfloat("tak", "emit_interval", fallback=2.0),
                sa_interval=self._cfg.getfloat("tak", "sa_interval", fallback=5.0),
                stale_detection=self._cfg.getfloat("tak", "stale_detection", fallback=60.0),
                stale_sa=self._cfg.getfloat("tak", "stale_sa", fallback=30.0),
                camera_hfov_deg=self._cfg.getfloat("camera", "hfov_deg", fallback=60.0),
                unicast_targets=self._cfg.get("tak", "unicast_targets", fallback=""),
                rtsp_url=rtsp_url,
            )
            if self._tak.start():
                return {"status": "ok", "running": True}
            self._tak = None
            return {"status": "error", "message": "Failed to start TAK output"}
        elif not enabled and self._tak is not None:
            self._tak.stop()
            self._tak = None
            return {"status": "ok", "running": False}
        return {"status": "ok", "running": self._tak is not None}

    def _get_tak_status(self) -> dict:
        """Return TAK output status for the web API."""
        if self._tak is not None:
            return self._tak.get_status()
        return {
            "enabled": self._cfg.getboolean("tak", "enabled", fallback=False),
            "running": False,
            "callsign": self._cfg.get("tak", "callsign", fallback="HYDRA-1"),
            "events_sent": 0,
        }

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
    def _shutdown(self) -> None:
        logger.info("Shutting down ...")
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
        if self._tak_input is not None:
            self._tak_input.stop()
        if self._servo_tracker is not None:
            self._servo_tracker.safe()
        self._camera.close()
        self._detector.unload()
        self._det_logger.stop()
        if self._mavlink is not None:
            self._mavlink.close()
        logger.info("=== Hydra Detect stopped ===")

    def _signal_handler(self, sig, frame) -> None:
        logger.info("Signal %s received, stopping.", sig)
        self._running = False


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
