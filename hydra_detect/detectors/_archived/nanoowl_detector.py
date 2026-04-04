"""NanoOWL open-vocabulary detector back-end (Jetson-optimised OWL-ViT)."""

from __future__ import annotations

import logging
import time
from typing import List

import numpy as np

from .base import BaseDetector, Detection, DetectionResult

logger = logging.getLogger(__name__)


class NanoOWLDetector(BaseDetector):
    """Open-vocabulary detector using NVIDIA NanoOWL / OWL-ViT.

    Falls back to HuggingFace OWL-ViT when NanoOWL C++ engine is unavailable.
    """

    def __init__(
        self,
        model_name: str = "google/owlvit-base-patch32",
        prompts: List[str] | None = None,
        threshold: float = 0.3,
    ):
        self._model_name = model_name
        self._prompts = prompts or ["person", "vehicle"]
        self._threshold = threshold
        self._predictor = None
        self._processor = None
        self._model = None
        self._use_nanoowl = False

    def load(self) -> None:
        # Try NanoOWL first (Jetson-optimised TensorRT engine)
        try:
            from nanoowl.owl_predictor import OwlPredictor

            logger.info("Loading NanoOWL predictor: %s", self._model_name)
            self._predictor = OwlPredictor(self._model_name)
            self._use_nanoowl = True
            logger.info("NanoOWL predictor ready.")
            return
        except ImportError:
            logger.info("NanoOWL not available, falling back to HF OWL-ViT.")

        # Fallback: HuggingFace transformers pipeline
        from transformers import OwlViTForObjectDetection, OwlViTProcessor

        logger.info("Loading OWL-ViT model: %s", self._model_name)
        self._processor = OwlViTProcessor.from_pretrained(self._model_name)
        self._model = OwlViTForObjectDetection.from_pretrained(self._model_name)
        logger.info("OWL-ViT model loaded (CPU/GPU via HuggingFace).")

    def detect(self, frame: np.ndarray) -> DetectionResult:
        if self._use_nanoowl:
            return self._detect_nanoowl(frame)
        return self._detect_hf(frame)

    # ------------------------------------------------------------------
    def _detect_nanoowl(self, frame: np.ndarray) -> DetectionResult:
        import torch  # noqa: F401
        from PIL import Image

        t0 = time.perf_counter()
        image = Image.fromarray(frame[..., ::-1])  # BGR → RGB
        output = self._predictor.predict(
            image=image,
            text=self._prompts,
            text_encodings=None,
            threshold=self._threshold,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        detections: list[Detection] = []
        boxes = output.boxes.cpu()
        scores = output.scores.cpu()
        labels = output.labels

        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes[i].tolist()
            conf = float(scores[i])
            cls = int(labels[i])
            detections.append(
                Detection(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    confidence=conf,
                    class_id=cls,
                    label=self._prompts[cls] if 0 <= cls < len(self._prompts) else str(cls),
                )
            )

        return DetectionResult(detections=detections, inference_ms=elapsed_ms)

    # ------------------------------------------------------------------
    def _detect_hf(self, frame: np.ndarray) -> DetectionResult:
        import torch
        from PIL import Image

        t0 = time.perf_counter()
        image = Image.fromarray(frame[..., ::-1])
        text_queries = [[f"a photo of a {p}" for p in self._prompts]]
        inputs = self._processor(text=text_queries, images=image, return_tensors="pt")

        with torch.no_grad():
            outputs = self._model(**inputs)

        target_sizes = torch.tensor([image.size[::-1]])
        results = self._processor.post_process_object_detection(
            outputs=outputs,
            target_sizes=target_sizes,
            threshold=self._threshold,
        )[0]
        elapsed_ms = (time.perf_counter() - t0) * 1000

        detections: list[Detection] = []
        for score, label, box in zip(
            results["scores"], results["labels"], results["boxes"]
        ):
            x1, y1, x2, y2 = box.tolist()
            cls = int(label)
            detections.append(
                Detection(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    confidence=float(score),
                    class_id=cls,
                    label=self._prompts[cls] if 0 <= cls < len(self._prompts) else str(cls),
                )
            )

        return DetectionResult(detections=detections, inference_ms=elapsed_ms)

    # -- Public setters for runtime reconfiguration ----------------------
    def set_prompts(self, prompts: list[str]) -> None:
        """Update detection prompts at runtime."""
        self._prompts = prompts

    def get_prompts(self) -> list[str]:
        return list(self._prompts)

    def set_threshold(self, threshold: float) -> None:
        """Update confidence threshold at runtime."""
        self._threshold = threshold

    def get_threshold(self) -> float:
        return self._threshold

    def unload(self) -> None:
        self._predictor = None
        self._model = None
        self._processor = None
