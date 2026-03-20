"""Tests for CAMERA_TRACKING_GEO_STATUS sender."""
from __future__ import annotations

from unittest.mock import MagicMock

from hydra_detect.geo_tracking import GeoTracker
from hydra_detect.tracker import TrackedObject, TrackingResult


def _make_track(track_id=1, label="person", confidence=0.9):
    return TrackedObject(
        track_id=track_id, x1=280, y1=200, x2=360, y2=400,
        confidence=confidence, class_id=0, label=label,
    )


def _make_tracking(*tracks):
    return TrackingResult(tracks=list(tracks), active_ids=len(tracks))


class TestGeoTrackerInit:
    def test_creates_without_error(self):
        mav = MagicMock()
        gt = GeoTracker(mav, camera_hfov_deg=60.0)
        assert gt is not None


class TestGeoTrackerSend:
    def test_no_tracks_no_send(self):
        mav = MagicMock()
        gt = GeoTracker(mav)
        gt.send(_make_tracking(), alert_classes=None, locked_track_id=None)
        mav._mav.mav.send.assert_not_called()

    def test_filters_by_alert_classes(self):
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 100.0)
        mav.get_heading_deg.return_value = 90.0
        mav.estimate_target_position.return_value = None
        gt = GeoTracker(mav)
        tracks = _make_tracking(_make_track(label="toothbrush"))
        gt.send(tracks, alert_classes={"person"}, locked_track_id=None)
        mav.estimate_target_position.assert_not_called()

    def test_throttle_default_2s(self):
        """Default min_interval=2.0 throttles rapid sends."""
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 100.0)
        mav.estimate_target_position.return_value = (34.001, -117.999)
        gt = GeoTracker(mav)
        tracks = _make_tracking(_make_track())
        gt.send(tracks, alert_classes=None, locked_track_id=1)
        gt.send(tracks, alert_classes=None, locked_track_id=1)
        assert mav.estimate_target_position.call_count == 1

    def test_configurable_interval(self):
        """Custom min_interval is respected."""
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 100.0)
        mav.estimate_target_position.return_value = (34.001, -117.999)
        gt = GeoTracker(mav, min_interval=5.0)
        assert gt._min_interval == 5.0
