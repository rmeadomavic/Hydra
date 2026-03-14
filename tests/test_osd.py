"""Tests for FPV OSD overlay module."""

from __future__ import annotations

from unittest.mock import MagicMock

from hydra_detect.osd import FpvOsd, OSDState, build_osd_state
from hydra_detect.tracker import TrackedObject, TrackingResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink_mock() -> MagicMock:
    """Create a mock MAVLinkIO with inner _mav and _lock."""
    mav = MagicMock()
    mav._mav = MagicMock()
    mav._lock = MagicMock()
    # Make the lock usable as a context manager
    mav._lock.__enter__ = MagicMock(return_value=None)
    mav._lock.__exit__ = MagicMock(return_value=False)
    return mav


def _make_tracking_result(tracks: list[dict] | None = None) -> TrackingResult:
    """Build a TrackingResult from simple dicts."""
    if tracks is None:
        tracks = []
    objs = [
        TrackedObject(
            track_id=t.get("track_id", 1),
            x1=t.get("x1", 100.0),
            y1=t.get("y1", 100.0),
            x2=t.get("x2", 200.0),
            y2=t.get("y2", 200.0),
            confidence=t.get("confidence", 0.9),
            class_id=t.get("class_id", 0),
            label=t.get("label", "person"),
        )
        for t in tracks
    ]
    return TrackingResult(tracks=objs, active_ids=len(objs))


# ---------------------------------------------------------------------------
# build_osd_state
# ---------------------------------------------------------------------------

class TestBuildOsdState:
    def test_empty_tracking(self):
        state = build_osd_state(
            _make_tracking_result(), fps=10.0, inference_ms=50.0,
            locked_track_id=None, lock_mode=None, gps=None,
        )
        assert state.active_tracks == 0
        assert state.fps == 10.0
        assert state.inference_ms == 50.0
        assert state.locked_track_id is None
        assert state.gps_fix == 0

    def test_with_tracks_and_lock(self):
        tracks = [
            {"track_id": 5, "label": "person"},
            {"track_id": 8, "label": "vehicle"},
        ]
        state = build_osd_state(
            _make_tracking_result(tracks), fps=12.0, inference_ms=30.0,
            locked_track_id=5, lock_mode="track",
            gps={"fix": 3, "lat": 340000000, "lon": -1180000000},
        )
        assert state.active_tracks == 2
        assert state.locked_track_id == 5
        assert state.lock_mode == "track"
        assert state.locked_label == "person"
        assert state.gps_fix == 3

    def test_lock_on_missing_track(self):
        """Locked track ID not in results — label stays empty."""
        state = build_osd_state(
            _make_tracking_result([{"track_id": 1}]),
            fps=10.0, inference_ms=20.0,
            locked_track_id=99, lock_mode="strike", gps=None,
        )
        assert state.locked_track_id == 99
        assert state.locked_label == ""


# ---------------------------------------------------------------------------
# FpvOsd — statustext mode
# ---------------------------------------------------------------------------

class TestFpvOsdStatustext:
    def test_sends_statustext(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(fps=15.0, inference_ms=40.0, active_tracks=3)

        osd.update(state)

        mav.send_statustext.assert_called_once()
        text = mav.send_statustext.call_args[0][0]
        assert "T:3" in text
        assert "15fps" in text
        assert "40ms" in text

    def test_locked_target_in_statustext(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=25.0, active_tracks=1,
            locked_track_id=7, lock_mode="strike", locked_label="person",
        )

        osd.update(state)

        text = mav.send_statustext.call_args[0][0]
        assert "LK#7S:person" in text

    def test_rate_limiting(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=1.0)
        state = OSDState(fps=10.0, inference_ms=20.0, active_tracks=0)

        osd.update(state)
        osd.update(state)  # Should be throttled

        assert mav.send_statustext.call_count == 1

    def test_statustext_truncated_to_50_chars(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=25.0, active_tracks=1,
            locked_track_id=7, lock_mode="track",
            locked_label="very_long_label_name",
        )

        osd.update(state)

        text = mav.send_statustext.call_args[0][0]
        assert len(text) <= 50


# ---------------------------------------------------------------------------
# FpvOsd — named_value mode
# ---------------------------------------------------------------------------

class TestFpvOsdNamedValue:
    def test_sends_named_values(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="named_value", update_interval=0.0)
        state = OSDState(
            fps=12.0, inference_ms=35.0, active_tracks=2,
            locked_track_id=None, lock_mode=None, gps_fix=3,
        )

        osd.update(state)

        inner = mav._mav.mav
        # Should have sent fps, infms, trks, lkid(-1), gfix
        assert inner.named_value_float_send.call_count == 2  # fps, infms
        assert inner.named_value_int_send.call_count == 3    # trks, lkid, gfix

    def test_sends_lock_data_when_locked(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="named_value", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=20.0, active_tracks=1,
            locked_track_id=5, lock_mode="strike", gps_fix=0,
        )

        osd.update(state)

        inner = mav._mav.mav
        # trks, lkid, lkmod, gfix = 4 int sends
        assert inner.named_value_int_send.call_count == 4

    def test_no_send_when_mav_disconnected(self):
        mav = _make_mavlink_mock()
        mav._mav = None  # Disconnected
        osd = FpvOsd(mav, mode="named_value", update_interval=0.0)
        state = OSDState(fps=10.0, inference_ms=20.0, active_tracks=0)

        # Should not raise
        osd.update(state)


# ---------------------------------------------------------------------------
# OSDState defaults
# ---------------------------------------------------------------------------

class TestOsdState:
    def test_defaults(self):
        state = OSDState()
        assert state.fps == 0.0
        assert state.inference_ms == 0.0
        assert state.active_tracks == 0
        assert state.locked_track_id is None
        assert state.lock_mode is None
        assert state.locked_label == ""
        assert state.gps_fix == 0
