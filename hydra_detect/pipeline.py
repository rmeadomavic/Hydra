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
        self._cfg = configparser.ConfigParser()
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
                    "mavlink", "connection_string", fallback="udpin:0.0.0.0:14550"
                ),
                alert_statustext=self._cfg.getboolean(
                    "mavlink", "alert_statustext", fallback=True
                ),
                alert_interval_sec=self._cfg.getfloat(
                    "mavlink", "alert_interval_sec", fallback=5.0
                ),
                auto_loiter=self._cfg.getboolean(
                    "mavlink", "auto_loiter_on_detect", fallback=False
                ),
                guided_roi=self._cfg.getboolean(
                    "mavlink", "guided_roi_on_detect", fallback=False
                ),
            )

        # Logger
        self._det_logger = DetectionLogger(
            log_dir=self._cfg.get("logging", "log_dir", fallback="logs"),
            log_format=self._cfg.get("logging", "log_format", fallback="csv"),
            save_crops=self._cfg.getboolean("logging", "save_crops", fallback=False),
            crop_dir=self._cfg.get("logging", "crop_dir", fallback="crops"),
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
            stream_state.update_stats(
                detector=engine_name,
                mavlink=self._mavlink is not None and self._mavlink.connected,
            )
            run_server(self._web_host, self._web_port)

        self._running = True
        self._run_loop()

    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Core detect → track → alert → render loop."""
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

            # MAVLink alerts
            if self._mavlink is not None and len(track_result) > 0:
                labels = Counter(t.label for t in track_result)
                most_common_label, count = labels.most_common(1)[0]
                self._mavlink.alert_detection(most_common_label, count)

            # Log
            self._det_logger.log(track_result, frame)

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
                stream_state.update_stats(
                    fps=fps,
                    inference_ms=det_result.inference_ms,
                    active_tracks=len(track_result),
                    total_detections=self._total_detections,
                    mavlink=self._mavlink is not None and self._mavlink.connected,
                )

        self._shutdown()

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
