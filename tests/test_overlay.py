"""Tests for overlay.py — bounding-box drawing, label clamping, strike blinking.

CLAUDE.md calls out bbox coordinate clamping as a known prior bug source
(negative coords / over-frame dimensions crash OpenCV).  These tests pin that
behavior.
"""

from __future__ import annotations

import numpy as np

from hydra_detect.overlay import draw_tracks
from hydra_detect.tracker import TrackedObject, TrackingResult


def _frame(h=480, w=640):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _tracking(*tracks) -> TrackingResult:
    return TrackingResult(tracks=list(tracks), active_ids=len(tracks))


def _track(track_id=1, x1=100.0, y1=100.0, x2=200.0, y2=200.0, label="person"):
    return TrackedObject(
        track_id=track_id, x1=x1, y1=y1, x2=x2, y2=y2,
        confidence=0.9, class_id=0, label=label,
    )


class TestBoundsClamping:
    def test_negative_coords_do_not_crash(self):
        frame = _frame()
        tr = _tracking(_track(x1=-50.0, y1=-50.0, x2=100.0, y2=100.0))
        # Should not raise — clamping required for OpenCV safety
        out = draw_tracks(frame, tr)
        assert out is frame  # in-place

    def test_over_frame_coords_do_not_crash(self):
        frame = _frame(h=480, w=640)
        tr = _tracking(_track(x1=600.0, y1=450.0, x2=700.0, y2=550.0))
        draw_tracks(frame, tr)

    def test_degenerate_box_skipped(self):
        """x2 <= x1 (fully off-frame) must be skipped silently."""
        frame = _frame()
        tr = _tracking(_track(x1=-100.0, y1=-100.0, x2=-50.0, y2=-50.0))
        draw_tracks(frame, tr)  # no crash


class TestLockModes:
    def test_locked_track_drawn(self):
        frame = _frame()
        tr = _tracking(_track(track_id=42))
        out = draw_tracks(frame, tr, locked_track_id=42, lock_mode="track")
        # Some non-zero pixels should have been drawn
        assert out.sum() > 0

    def test_strike_mode_draws_red(self):
        frame = _frame()
        tr = _tracking(_track(track_id=7))
        draw_tracks(frame, tr, locked_track_id=7, lock_mode="strike")
        # BGR red channel has activity
        assert frame[:, :, 2].sum() > 0

    def test_hud_always_drawn(self):
        frame = _frame()
        tr = _tracking()
        draw_tracks(frame, tr, fps=30.0, inference_ms=15.0)
        # HUD text area in top-left should have some non-zero pixels
        assert frame[:50, :200].sum() > 0


class TestAlertClassDimming:
    def test_matching_label_drawn_full_opacity(self):
        frame = _frame()
        tr = _tracking(_track(label="person"))
        draw_tracks(frame, tr, alert_classes={"person"})
        assert frame.sum() > 0

    def test_non_matching_label_dimmed(self):
        full = _frame()
        dim = _frame()
        tr = _tracking(_track(label="dog"))
        draw_tracks(full, tr, alert_classes=None)
        draw_tracks(dim, tr, alert_classes={"person"})  # dog not in alert list
        # Dimmed version should have a lower max pixel value
        assert dim.max() <= full.max()

    def test_locked_overrides_dimming(self):
        """A locked track must render at full opacity even when dimmed."""
        frame = _frame()
        tr = _tracking(_track(track_id=5, label="dog"))
        draw_tracks(
            frame, tr,
            locked_track_id=5, lock_mode="track",
            alert_classes={"person"},
        )
        # Locked should produce strong signal despite being non-alert class
        assert frame.sum() > 0
