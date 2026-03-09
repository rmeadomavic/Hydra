"""Smoke tests for detector base classes."""

import numpy as np

from hydra_detect.detectors.base import Detection, DetectionResult


def test_detection_properties():
    d = Detection(x1=10, y1=20, x2=50, y2=80, confidence=0.9, class_id=0, label="person")
    assert d.bbox == (10, 20, 50, 80)
    assert d.center == (30.0, 50.0)
    assert d.area == 40 * 60


def test_detection_result_iterable():
    dr = DetectionResult(
        detections=[
            Detection(0, 0, 10, 10, 0.5, 0),
            Detection(20, 20, 30, 30, 0.8, 1),
        ],
        inference_ms=12.3,
    )
    assert len(dr) == 2
    labels = [d.class_id for d in dr]
    assert labels == [0, 1]
