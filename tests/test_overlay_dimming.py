"""Tests for overlay dimming of non-alert-class tracks."""
from __future__ import annotations

import numpy as np

from hydra_detect.overlay import draw_tracks
from hydra_detect.tracker import TrackedObject, TrackingResult


def _make_track(track_id=1, label="person", class_id=0):
    return TrackedObject(
        track_id=track_id, x1=100, y1=100, x2=200, y2=200,
        confidence=0.9, class_id=class_id, label=label,
    )


def _make_tracking(*tracks):
    return TrackingResult(tracks=list(tracks), active_ids=len(tracks))


class TestOverlayDimming:
    def test_accepts_alert_classes_param(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        tracking = _make_tracking(_make_track())
        result = draw_tracks(frame, tracking, alert_classes={"person"})
        assert result.shape == (480, 640, 3)

    def test_none_alert_classes_draws_normally(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        tracking = _make_tracking(_make_track(label="toothbrush", class_id=79))
        result = draw_tracks(frame, tracking, alert_classes=None)
        assert result.sum() > 0

    def test_dimmed_track_has_lower_intensity(self):
        tracking = _make_tracking(_make_track(label="person", class_id=0))
        frame_alert = np.zeros((480, 640, 3), dtype=np.uint8)
        draw_tracks(frame_alert, tracking, alert_classes={"person"})
        frame_dimmed = np.zeros((480, 640, 3), dtype=np.uint8)
        draw_tracks(frame_dimmed, tracking, alert_classes={"car"})
        assert frame_dimmed.sum() < frame_alert.sum()
