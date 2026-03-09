"""Main detection pipeline — orchestrates camera, detector, tracker, and outputs."""

from __future__ import annotations

import configparser
import logging
import signal
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import cv2

from .camera import Camera
from .detection_logger import DetectionLogger
from .detectors.base import BaseDetector
from .detectors.nanoowl_detector import NanoOWLDetector
from .detectors.yolo_detector import YOLODetector
from .mavlink_io import MAVLinkIO
from .overlay import draw_tracks
from .tracker import ByteTracker
from .web.server import run_server, stream_state

logger = logging.getLogger(__name__)


def _build_detector(cfg: configparser.ConfigParser) -> BaseDetector:
    """Factory: create the right detector from config."""
    engine = cfg.get("detector", "engine", fallback="yolo").lower()

    if engine == "nanoowl":
        prompts = [
            p.strip()
            for p in cfg.get("detector", "nanoowl_prompts", fallback="person,vehicle").split(",")
        ]
        return NanoOWLDetector(
            model_name=cfg.get("detector", "nanoowl_model", fallback="google/owlvit-base-patch32"),
            prompts=prompts,
            threshold=cfg.getfloat("detector", "nanoowl_threshold", fallback=0.3),
        )

    # Default: YOLO
    classes_raw = cfg.get("detector", "yolo_classes", fallback="")
    classes = [int(c.strip()) for c in classes_raw.split(",") if c.strip()] or None
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
        self._total_detections = 0

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Initialise all subsystems and run the main loop."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )
        logger.info("=== Hydra Detect v2.0 starting ===")

        # Graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Init subsystems
        self._detector.load()
        engine_name = self._cfg.get("detector", "engine", fallback="yolo")
        logger.info("Detector engine: %s", engine_name)

        self._tracker.init()

        if not self._camera.open():
            logger.error("Failed to open camera — aborting.")
            sys.exit(1)

        if self._mavlink is not None:
            if not self._mavlink.connect():
                logger.warning("MAVLink connection failed — continuing without.")
                self._mavlink = None

        self._det_logger.start()

        if self._web_enabled:
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

            stream_state.runtime_config.update({
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
                get_recent_detections=self._det_logger.get_recent,
            )

            stream_state.update_stats(
                detector=engine_name,
                mavlink=self._mavlink is not None and self._mavlink.connected,
            )
            run_server(self._web_host, self._web_port)

        self._running = True
        self._run_loop()

    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Core detect -> track -> alert -> render loop."""
        fps_counter = _FPSCounter()

        while self._running:
            frame = self._camera.read()
            if frame is None:
                time.sleep(0.01)
                continue

            # Detect
            det_result = self._detector.detect(frame)

            # Track
            track_result = self._tracker.update(det_result)
            self._total_detections += len(track_result)

            # Get GPS state for logging and alerts
            gps = None
            if self._mavlink is not None:
                gps = self._mavlink.get_gps()

            # MAVLink alerts (per-label throttled)
            if self._mavlink is not None and len(track_result) > 0:
                for track in track_result:
                    self._mavlink.alert_detection(track.label, track.confidence)

                # Auto-loiter on detection
                if self._mavlink._auto_loiter:
                    self._mavlink.command_loiter()

            # Log with GPS data
            self._det_logger.log(track_result, frame, gps=gps)

            # Render overlay
            fps = fps_counter.tick()
            annotated = draw_tracks(
                frame, track_result,
                inference_ms=det_result.inference_ms,
                fps=fps,
            )

            # Push to web stream
            if self._web_enabled:
                stream_state.update_frame(annotated)
                stats_update = {
                    "fps": fps,
                    "inference_ms": det_result.inference_ms,
                    "active_tracks": len(track_result),
                    "total_detections": self._total_detections,
                    "mavlink": self._mavlink is not None and self._mavlink.connected,
                }
                if self._mavlink is not None:
                    gps_data = self._mavlink.get_gps()
                    stats_update["gps_fix"] = gps_data.get("fix", 0)
                    stats_update["position"] = self._mavlink.get_position_string()
                stream_state.update_stats(**stats_update)

        self._shutdown()

    # ------------------------------------------------------------------
    # Runtime config handlers (called from web UI)
    # ------------------------------------------------------------------
    def _handle_prompts_change(self, prompts: list[str]) -> None:
        """Update NanoOWL prompts at runtime."""
        if isinstance(self._detector, NanoOWLDetector):
            self._detector._prompts = prompts
            logger.info("NanoOWL prompts updated: %s", prompts)

    def _handle_threshold_change(self, threshold: float) -> None:
        """Update detector confidence threshold at runtime."""
        if isinstance(self._detector, NanoOWLDetector):
            self._detector._threshold = threshold
        elif isinstance(self._detector, YOLODetector):
            self._detector._confidence = threshold
        logger.info("Detection threshold updated: %.2f", threshold)

    def _handle_loiter_command(self) -> None:
        """Manual loiter command from web UI."""
        if self._mavlink is not None:
            # Force loiter regardless of auto_loiter setting
            try:
                mode_map = self._mavlink._mav.mode_mapping()
                for mode_name in ("LOITER", "HOLD"):
                    if mode_name in mode_map:
                        self._mavlink._mav.set_mode_apm(mode_map[mode_name])
                        logger.info("Manual LOITER command from web UI.")
                        return
            except Exception as exc:
                logger.warning("Manual LOITER failed: %s", exc)

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
