"""Abstract base detector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

import numpy as np


@dataclass
class Detection:
    """Single detection result."""

    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    label: str = ""

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def area(self) -> float:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)


@dataclass
class DetectionResult:
    """Container for a frame's detections."""

    detections: List[Detection] = field(default_factory=list)
    inference_ms: float = 0.0

    def __len__(self) -> int:
        return len(self.detections)

    def __iter__(self):
        return iter(self.detections)


class BaseDetector(ABC):
    """Interface that all detector back-ends must implement."""

    @abstractmethod
    def load(self) -> None:
        """Load model weights / initialise engine."""

    @abstractmethod
    def detect(self, frame: np.ndarray) -> DetectionResult:
        """Run inference on a single BGR frame."""

    def unload(self) -> None:
        """Optional cleanup."""
