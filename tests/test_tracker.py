"""Smoke tests for tracker data classes."""

from hydra_detect.tracker import TrackedObject, TrackingResult


def test_tracked_object_center():
    t = TrackedObject(track_id=1, x1=0, y1=0, x2=100, y2=100, confidence=0.9, class_id=0)
    assert t.center == (50.0, 50.0)


def test_tracking_result_empty():
    tr = TrackingResult()
    assert len(tr) == 0
