"""Detector back-ends — swappable detection engines."""

from .base import BaseDetector, Detection, DetectionResult
from .yolo_detector import YOLODetector

__all__ = [
    "BaseDetector",
    "Detection",
    "DetectionResult",
    "YOLODetector",
]
