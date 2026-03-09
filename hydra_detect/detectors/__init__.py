"""Detector back-ends — swappable detection engines."""

from .base import BaseDetector, Detection, DetectionResult
from .nanoowl_detector import NanoOWLDetector
from .yolo_detector import YOLODetector

__all__ = [
    "BaseDetector",
    "Detection",
    "DetectionResult",
    "NanoOWLDetector",
    "YOLODetector",
]
