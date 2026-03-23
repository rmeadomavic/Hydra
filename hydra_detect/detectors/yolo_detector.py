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
        imgsz: int | None = None,
    ):
        self._model_path = model_path
        self._confidence = confidence
        self._classes = classes
        self._imgsz = imgsz
        self._model = None

    def load(self) -> None:
        from ultralytics import YOLO

        logger.info("Loading YOLO model: %s", self._model_path)
        self._model = YOLO(self._model_path)
        # Force GPU inference on Jetson — CPU gives 1-2 FPS, GPU gives 5-10+
        try:
            import torch
            if torch.cuda.is_available():
                self._model.to("cuda:0")
                logger.info("YOLO model loaded on GPU (CUDA).")
            else:
                logger.warning("CUDA not available — running YOLO on CPU (expect slow inference).")
        except ImportError:
            logger.warning("torch not available for device check — YOLO using default device.")
        logger.info("YOLO model loaded.")

    def detect(self, frame: np.ndarray) -> DetectionResult:
        if self._model is None:
            raise RuntimeError("Model not loaded — call load() first.")

        t0 = time.perf_counter()
        predict_kwargs: dict = dict(
            conf=self._confidence,
            classes=self._classes,
            verbose=False,
        )
        if self._imgsz is not None:
            predict_kwargs["imgsz"] = self._imgsz
        results = self._model.predict(frame, **predict_kwargs)
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

    @property
    def model_path(self) -> str:
        return self._model_path

    def get_class_names(self) -> list[str]:
        """Return class label names from the loaded model."""
        if self._model is None:
            return []
        return list(self._model.names.values())

    def switch_model(self, model_path: str) -> bool:
        """Switch to a different YOLO model at runtime.

        Returns True on success, False if the new model can't be loaded
        (old model stays active).
        """
        from ultralytics import YOLO

        old_path = self._model_path
        logger.info("Switching YOLO model: %s -> %s", old_path, model_path)
        try:
            new_model = YOLO(model_path)
            try:
                import torch
                if torch.cuda.is_available():
                    new_model.to("cuda:0")
            except ImportError:
                pass
            self._model = new_model
            self._model_path = model_path
            logger.info("YOLO model switched to: %s", model_path)
            return True
        except Exception as exc:
            logger.error("Failed to load model %s: %s — keeping %s",
                         model_path, exc, old_path)
            return False

    def unload(self) -> None:
        self._model = None
