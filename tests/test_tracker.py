"""Smoke tests for tracker data classes."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

from hydra_detect.tracker import (
    ByteTracker,
    ReIDTracker,
    TrackedObject,
    TrackingResult,
    reid_dependency_available,
)
from hydra_detect.detectors.base import Detection, DetectionResult


def test_tracked_object_center():
    t = TrackedObject(track_id=1, x1=0, y1=0, x2=100, y2=100, confidence=0.9, class_id=0)
    assert t.center == (50.0, 50.0)


def test_tracking_result_empty():
    tr = TrackingResult()
    assert len(tr) == 0


# ---------------------------------------------------------------------------
# ByteTracker forward compatibility — accepts optional frame kwarg
# ---------------------------------------------------------------------------

class TestByteTrackerFrameKwarg:
    """Existing callers must keep working; new callers may pass a frame."""

    def test_update_accepts_optional_frame(self):
        bt = ByteTracker()
        # Tracker is uninitialised — falls through to passthrough mode.
        det_result = DetectionResult(detections=[
            Detection(x1=0, y1=0, x2=10, y2=10, confidence=0.9, class_id=0, label="person"),
        ])
        result_no_frame = bt.update(det_result)
        result_with_frame = bt.update(det_result, frame=np.zeros((100, 100, 3), dtype=np.uint8))
        # Both code paths produce the same shape of result.
        assert isinstance(result_no_frame, TrackingResult)
        assert isinstance(result_with_frame, TrackingResult)


# ---------------------------------------------------------------------------
# ReIDTracker — boxmot-backed re-ID layer (gated by [tracker] reid_enabled)
# ---------------------------------------------------------------------------

class TestReIDTrackerImportGate:
    """When boxmot is missing, ReIDTracker.init() must raise an actionable
    error pointing at requirements-extra.txt."""

    def test_init_raises_with_install_hint_when_boxmot_missing(self, monkeypatch):
        # Force the lazy import inside ReIDTracker.init() to fail.
        monkeypatch.setitem(sys.modules, "boxmot", None)
        rt = ReIDTracker(tracker_type="botsort")
        with pytest.raises(ImportError) as exc:
            rt.init()
        msg = str(exc.value)
        assert "boxmot" in msg
        assert "requirements-extra.txt" in msg or "pip install" in msg

    def test_dependency_probe_returns_false_when_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "boxmot", None)
        assert reid_dependency_available() is False


class TestReIDTrackerWiring:
    """With a stubbed boxmot module, ReIDTracker delegates correctly."""

    def _install_boxmot_stub(self, monkeypatch):
        """Drop a fake boxmot module into sys.modules and return the mock
        tracker instance create_tracker() will return."""
        fake_tracker = MagicMock()
        # boxmot returns Nx8 numpy array: x1,y1,x2,y2,id,conf,cls,detection_idx
        fake_tracker.update.return_value = np.array([
            [10.0, 20.0, 30.0, 40.0, 7, 0.85, 0, 0],
        ])
        fake_module = types.ModuleType("boxmot")
        fake_module.create_tracker = MagicMock(return_value=fake_tracker)
        monkeypatch.setitem(sys.modules, "boxmot", fake_module)
        return fake_tracker, fake_module

    def test_init_calls_boxmot_create_tracker(self, monkeypatch):
        _, fake_module = self._install_boxmot_stub(monkeypatch)
        rt = ReIDTracker(tracker_type="botsort")
        rt.init()
        fake_module.create_tracker.assert_called_once()
        kwargs = fake_module.create_tracker.call_args.kwargs
        # tracker_type is plumbed through verbatim.
        args = fake_module.create_tracker.call_args.args
        assert (
            "botsort" in args
            or kwargs.get("tracker_type") == "botsort"
        )

    def test_update_translates_detections_to_numpy(self, monkeypatch):
        fake_tracker, _ = self._install_boxmot_stub(monkeypatch)
        rt = ReIDTracker(tracker_type="botsort")
        rt.init()

        det_result = DetectionResult(detections=[
            Detection(x1=10, y1=20, x2=30, y2=40, confidence=0.85,
                      class_id=0, label="person"),
        ])
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        result = rt.update(det_result, frame=frame)

        # boxmot was called with (Nx6 array, frame).
        call_args = fake_tracker.update.call_args.args
        dets_arg = call_args[0]
        assert dets_arg.shape == (1, 6)
        assert dets_arg[0, 4] == pytest.approx(0.85)
        assert int(dets_arg[0, 5]) == 0

        # Result wraps the boxmot output as TrackingResult with the assigned id.
        assert isinstance(result, TrackingResult)
        assert len(result) == 1
        assert result.tracks[0].track_id == 7
        assert result.tracks[0].confidence == pytest.approx(0.85)

    def test_empty_detections_still_calls_boxmot_update(self, monkeypatch):
        """Even with zero detections, boxmot.update() must be invoked so
        the tracker ages missed tracks during dropouts/occlusions. Skipping
        the call freezes internal counters and breaks re-ID continuity."""
        fake_tracker, _ = self._install_boxmot_stub(monkeypatch)
        # Configure the stub to return an empty result for empty input.
        fake_tracker.update.return_value = np.empty((0, 7), dtype=np.float32)

        rt = ReIDTracker(tracker_type="botsort")
        rt.init()

        empty = DetectionResult(detections=[])
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rt.update(empty, frame=frame)

        # boxmot.update() WAS called — that's the bug fix.
        assert fake_tracker.update.called
        call_args = fake_tracker.update.call_args.args
        dets_arg = call_args[0]
        assert dets_arg.shape == (0, 6), "empty detections must be passed as 0x6 ndarray"
        assert call_args[1] is frame
        # Tracking result is empty (no IDs to report this frame).
        assert len(result) == 0

    def test_empty_detections_old_boxmot_falls_back_gracefully(self, monkeypatch):
        """Older boxmot versions can raise ValueError on empty input.
        The fallback path should swallow that and return empty."""
        fake_tracker, _ = self._install_boxmot_stub(monkeypatch)
        fake_tracker.update.side_effect = ValueError("empty input not supported")

        rt = ReIDTracker(tracker_type="botsort")
        rt.init()

        empty = DetectionResult(detections=[])
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rt.update(empty, frame=frame)

        assert fake_tracker.update.called
        assert len(result) == 0

    def test_update_requires_frame(self, monkeypatch):
        """Re-ID needs the actual image for appearance embedding; an
        attempt to update without a frame should raise a clear error
        rather than silently degrading to ID-swap-prone tracking."""
        self._install_boxmot_stub(monkeypatch)
        rt = ReIDTracker(tracker_type="botsort")
        rt.init()

        det_result = DetectionResult(detections=[
            Detection(x1=0, y1=0, x2=10, y2=10, confidence=0.9, class_id=0, label="person"),
        ])
        with pytest.raises(ValueError) as exc:
            rt.update(det_result, frame=None)
        assert "frame" in str(exc.value).lower()
