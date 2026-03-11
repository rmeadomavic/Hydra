"""YOLO detector back-end using ultralytics."""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import numpy as np

from .base import BaseDetector, Detection, DetectionResult

logger = logging.getLogger(__name__)


class YOLODetector(BaseDetector):
    """YOLOv8/v11 detector via the ultralytics library."""

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence: float = 0.45,
        classes: Optional[List[int]] = None,
    ):
        self._model_path = model_path
        self._confidence = confidence
        self._classes = classes
        self._model = None

    def load(self) -> None:
        from ultralytics import YOLO

        logger.info("Loading YOLO model: %s", self._model_path)
        self._model = YOLO(self._model_path)
        logger.info("YOLO model loaded.")

    def detect(self, frame: np.ndarray) -> DetectionResult:
        if self._model is None:
            raise RuntimeError("Model not loaded — call load() first.")

        t0 = time.perf_counter()
        results = self._model.predict(
            frame,
            conf=self._confidence,
            classes=self._classes,
            verbose=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        detections: list[Detection] = []
        for r in results:
            boxes = r.boxes
            if boxes is None:
                continue
            for i in range(len(boxes)):
                x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                conf = float(boxes.conf[i])
                cls = int(boxes.cls[i])
                label = r.names.get(cls, str(cls))
                detections.append(
                    Detection(
                        x1=x1, y1=y1, x2=x2, y2=y2,
                        confidence=conf, class_id=cls, label=label,
                    )
                )

        return DetectionResult(detections=detections, inference_ms=elapsed_ms)

    # -- Public setters for runtime reconfiguration ----------------------
    def set_threshold(self, threshold: float) -> None:
        """Update confidence threshold at runtime."""
        self._confidence = threshold

    def get_threshold(self) -> float:
        return self._confidence

    def unload(self) -> None:
        self._model = None
