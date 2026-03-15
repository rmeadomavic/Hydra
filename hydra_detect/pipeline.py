"""Main detection pipeline — orchestrates camera, detector, tracker, and outputs."""

from __future__ import annotations

import configparser
import logging
import signal
import sys
import threading
import time
from typing import Optional

from .camera import Camera
from .detection_logger import DetectionLogger
from .detectors.base import BaseDetector
from .detectors.nanoowl_detector import NanoOWLDetector
from .detectors.yolo_detector import YOLODetector
from .mavlink_io import MAVLinkIO
from .osd import FpvOsd, build_osd_state
from .overlay import draw_tracks
from .tracker import ByteTracker
from .web.server import configure_auth, run_server, stream_state

logger = logging.getLogger(__name__)


def _build_detector(cfg: configparser.ConfigParser) -> BaseDetector:
    """Factory: create the right detector from config."""
    engine = cfg.get("detector", "engine", fallback="yolo").lower()

    if engine == "nanoowl":
        prompts = [
            p.strip()
            for p in cfg.get("detector", "nanoowl_prompts", fallback="person,vehicle").split(",")
            if p.strip()
        ]
        if not prompts:
            logger.warning("No valid NanoOWL prompts configured, using defaults.")
            prompts = ["person", "vehicle"]
        return NanoOWLDetector(
            model_name=cfg.get("detector", "nanoowl_model", fallback="google/owlvit-base-patch32"),
            prompts=prompts,
            threshold=cfg.getfloat("detector", "nanoowl_threshold", fallback=0.3),
        )

    # Default: YOLO
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
    return YOLODetector(
        model_path=cfg.get("detector", "yolo_model", fallback="yolov8n.pt"),
        confidence=cfg.getfloat("detector", "yolo_confidence", fallback=0.45),
        classes=classes,
    )


class Pipeline:
    """Top-level orchestrator that ties all modules together."""

    def __init__(self, config_path: str = "config.ini"):
        self._cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        self._cfg.read(config_path)

        # Camera
        self._camera = Camera(
            source=self._cfg.get("camera", "source", fallback="0"),
            width=self._cfg.getint("camera", "width", fallback=640),
            height=self._cfg.getint("camera", "height", fallback=480),
            fps=self._cfg.getint("camera", "fps", fallback=30),
        )

        # Detector
        self._detector = _build_detector(self._cfg)

        # Tracker
        self._tracker = ByteTracker(
            track_thresh=self._cfg.getfloat("tracker", "track_thresh", fallback=0.5),
            track_buffer=self._cfg.getint("tracker", "track_buffer", fallback=30),
            match_thresh=self._cfg.getfloat("tracker", "match_thresh", fallback=0.8),
        )

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
            )

        # Logger
        self._det_logger = DetectionLogger(
            log_dir=self._cfg.get("logging", "log_dir", fallback="/data/logs"),
            log_format=self._cfg.get("logging", "log_format", fallback="jsonl"),
            save_images=self._cfg.getboolean("logging", "save_images", fallback=True),
            image_dir=self._cfg.get("logging", "image_dir", fallback="/data/images"),
            image_quality=self._cfg.getint("logging", "image_quality", fallback=90),
            save_crops=self._cfg.getboolean("logging", "save_crops", fallback=False),
            crop_dir=self._cfg.get("logging", "crop_dir", fallback="/data/crops"),
        )

        # Web UI
        self._web_enabled = self._cfg.getboolean("web", "enabled", fallback=True)
        self._web_host = self._cfg.get("web", "host", fallback="0.0.0.0")
        self._web_port = self._cfg.getint("web", "port", fallback=8080)

        self._running = False
        self._paused = False
        self._total_detections = 0
        self._init_target_state()

    def _init_target_state(self) -> None:
        """Initialise target-lock state. Safe to call from tests."""
        self._state_lock = threading.Lock()
        self._locked_track_id: Optional[int] = None
        self._lock_mode: Optional[str] = None  # "track" or "strike"
        self._last_track_result = None  # Most recent TrackingResult for web API

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Initialise all subsystems and run the main loop."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )
        logger.info("=== Hydra Detect v2.0 starting ===")

        # Init subsystems — clean up on partial failure
        try:
            self._detector.load()
        except Exception as exc:
            logger.error("Detector failed to load: %s", exc)
            sys.exit(1)
        engine_name = self._cfg.get("detector", "engine", fallback="yolo")
        logger.info("Detector engine: %s", engine_name)

        self._tracker.init()

        if not self._camera.open():
            logger.error("Failed to open camera — aborting.")
            self._detector.unload()
            sys.exit(1)

        if self._mavlink is not None:
            if not self._mavlink.connect():
                logger.warning("MAVLink connection failed — continuing without.")
                self._mavlink = None
                self._osd = None

        if self._osd is not None:
            logger.info("FPV OSD enabled (mode=%s, interval=%.2fs)",
                        self._osd.mode, self._cfg.getfloat(
                            "osd", "update_interval", fallback=0.2))

        self._det_logger.start()

        if self._web_enabled:
            # Configure API auth
            api_token = self._cfg.get("web", "api_token", fallback="").strip()
            configure_auth(api_token or None)

            # Set initial runtime config for web UI
            if engine_name == "nanoowl":
                prompts = [
                    p.strip()
                    for p in self._cfg.get(
                        "detector", "nanoowl_prompts", fallback="person"
                    ).split(",")
                ]
                threshold = self._cfg.getfloat("detector", "nanoowl_threshold", fallback=0.3)
            else:
                prompts = []
                threshold = self._cfg.getfloat("detector", "yolo_confidence", fallback=0.45)

            stream_state.update_runtime_config({
                "prompts": prompts,
                "threshold": threshold,
                "auto_loiter": self._cfg.getboolean(
                    "mavlink", "auto_loiter_on_detect", fallback=False
                ),
            })

            # Wire runtime config callbacks
            stream_state.set_callbacks(
                on_prompts_change=self._handle_prompts_change,
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
            )

            stream_state.update_stats(
                detector=engine_name,
                mavlink=self._mavlink is not None and self._mavlink.connected,
            )
            run_server(self._web_host, self._web_port)

        # Register signal handlers after init is complete
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self._running = True
        self._run_loop()

    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Core detect -> track -> alert -> render loop."""
        fps_counter = _FPSCounter()

        while self._running:
            if self._paused:
                time.sleep(0.1)
                continue

            frame = self._camera.read()
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
                for track in track_result:
                    self._mavlink.alert_detection(track.label, track.confidence)

                # Auto-loiter on detection
                if self._mavlink.auto_loiter:
                    self._mavlink.command_loiter()

            if current_lock_id is not None and self._mavlink is not None:
                locked_track = None
                for t in track_result:
                    if t.track_id == current_lock_id:
                        locked_track = t
                        break

                if locked_track is not None:
                    # Compute normalised horizontal error from frame center
                    frame_w = frame.shape[1]
                    cx = (locked_track.x1 + locked_track.x2) / 2.0
                    error_x = (cx - frame_w / 2.0) / (frame_w / 2.0)  # -1..+1

                    if current_lock_mode == "track":
                        self._mavlink.adjust_yaw(error_x)
                    # Strike mode: yaw + continue GUIDED approach
                    elif current_lock_mode == "strike":
                        self._mavlink.adjust_yaw(error_x, yaw_rate_max=15.0)
                else:
                    # Target lost — auto-unlock and notify operator.
                    # NOTE: The lock is released automatically so the vehicle
                    # stops yaw corrections toward a stale position. The
                    # operator is notified via MAVLink STATUSTEXT and can
                    # re-acquire manually if the target reappears.
                    logger.warning(
                        "Locked target #%d lost from tracker — auto-unlocking.",
                        current_lock_id,
                    )
                    with self._state_lock:
                        self._locked_track_id = None
                        self._lock_mode = None
                    if self._mavlink is not None:
                        self._mavlink.send_statustext(
                            f"TGT LOST: #{current_lock_id} — lock released"
                        )

            # Log with GPS data
            self._det_logger.log(track_result, frame, gps=gps)

            # Render overlay
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
            )

            # FPV OSD update (sends to FC onboard OSD chip via MAVLink)
            if self._osd is not None:
                osd_state = build_osd_state(
                    track_result, fps, det_result.inference_ms,
                    render_lock_id, render_lock_mode, gps,
                )
                self._osd.update(osd_state)

            # Push to web stream
            if self._web_enabled:
                stream_state.update_frame(annotated)
                stats_update = {
                    "fps": fps,
                    "inference_ms": det_result.inference_ms,
                    "active_tracks": len(track_result),
                    "total_detections": total_det,
                    "mavlink": self._mavlink is not None and self._mavlink.connected,
                }
                if self._mavlink is not None:
                    gps_data = self._mavlink.get_gps()
                    stats_update["gps_fix"] = gps_data.get("fix", 0)
                    stats_update["position"] = self._mavlink.get_position_string()
                stream_state.update_stats(**stats_update)

                # Update target lock state for web UI
                # (reuse render_lock_id/render_lock_mode from above)
                if render_lock_id is not None:
                    locked_label = None
                    for t in track_result:
                        if t.track_id == render_lock_id:
                            locked_label = t.label
                            break
                    stream_state.set_target_lock({
                        "locked": True,
                        "track_id": render_lock_id,
                        "mode": render_lock_mode,
                        "label": locked_label,
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
    def _handle_prompts_change(self, prompts: list[str]) -> None:
        """Update NanoOWL prompts at runtime."""
        if isinstance(self._detector, NanoOWLDetector):
            self._detector.set_prompts(prompts)
            logger.info("NanoOWL prompts updated: %s", prompts)

    def _handle_threshold_change(self, threshold: float) -> None:
        """Update detector confidence threshold at runtime."""
        self._detector.set_threshold(threshold)
        logger.info("Detection threshold updated: %.2f", threshold)

    def _handle_loiter_command(self) -> None:
        """Manual loiter command from web UI."""
        if self._mavlink is not None:
            for mode_name in ("LOITER", "HOLD"):
                if self._mavlink.set_mode(mode_name):
                    logger.info("Manual LOITER command from web UI.")
                    return

    def _handle_target_lock(self, track_id: int, mode: str = "track") -> bool:
        """Lock onto a tracked object for keep-in-frame or strike."""
        with self._state_lock:
            if self._last_track_result is None:
                return False
            # Verify track_id exists in current tracks
            for t in self._last_track_result:
                if t.track_id == track_id:
                    self._locked_track_id = track_id
                    self._lock_mode = mode
                    logger.info(
                        "Target LOCKED: #%d (%s) mode=%s", track_id, t.label, mode
                    )
                    if self._mavlink is not None:
                        self._mavlink.send_statustext(
                            f"TGT LOCK: #{track_id} {t.label} [{mode.upper()}]"
                        )
                    return True
        logger.warning("Target lock failed: track #%d not found.", track_id)
        return False

    def _handle_target_unlock(self) -> None:
        """Release target lock."""
        with self._state_lock:
            prev_id = self._locked_track_id
            self._locked_track_id = None
            self._lock_mode = None
        if prev_id is not None:
            logger.info("Target UNLOCKED: #%d", prev_id)
            if self._mavlink is not None:
                self._mavlink.send_statustext("TGT LOCK RELEASED")
                self._mavlink.clear_roi()

    def _handle_strike_command(self, track_id: int) -> bool:
        """Command vehicle to navigate toward a tracked target.

        Estimates target GPS from camera offset, switches to GUIDED,
        and sets lock_mode to 'strike' for continued yaw tracking.
        """
        if self._mavlink is None:
            return False

        with self._state_lock:
            if self._last_track_result is None:
                return False
            # Find the track under lock (snapshot)
            target_track = None
            for t in self._last_track_result:
                if t.track_id == track_id:
                    target_track = t
                    break
        if target_track is None:
            logger.warning("Strike failed: track #%d not found.", track_id)
            return False

        # Compute target bearing from frame position
        if self._camera.has_frame:
            frame_w = self._camera.width
        else:
            frame_w = 640
        cx = (target_track.x1 + target_track.x2) / 2.0
        error_x = (cx - frame_w / 2.0) / (frame_w / 2.0)

        # Estimate target GPS position
        approach_dist = self._cfg.getfloat("mavlink", "strike_distance_m", fallback=20.0)
        camera_hfov = self._cfg.getfloat("camera", "hfov_deg", fallback=60.0)
        target_pos = self._mavlink.estimate_target_position(
            error_x, approach_dist, camera_hfov
        )
        if target_pos is None:
            logger.warning("Strike failed: no GPS fix or heading.")
            return False

        target_lat, target_lon = target_pos

        # Command GUIDED to estimated position
        success = self._mavlink.command_guided_to(target_lat, target_lon)
        if success:
            # Only lock target after GUIDED is confirmed
            with self._state_lock:
                self._locked_track_id = track_id
                self._lock_mode = "strike"
            logger.info(
                "STRIKE initiated: #%d (%s) -> %.6f, %.6f",
                track_id, target_track.label, target_lat, target_lon,
            )
        else:
            logger.error(
                "STRIKE failed: GUIDED command rejected for #%d (%s)",
                track_id, target_track.label,
            )
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
        self._window = window
        self._times: list[float] = []

    def tick(self) -> float:
        now = time.perf_counter()
        self._times.append(now)
        if len(self._times) > self._window:
            self._times.pop(0)
        if len(self._times) < 2:
            return 0.0
        return (len(self._times) - 1) / (self._times[-1] - self._times[0])
