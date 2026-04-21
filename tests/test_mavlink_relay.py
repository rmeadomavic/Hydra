"""Tests for MAVLinkRelayOutput — the offline-Jetson CoT relay sink."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from hydra_detect.tak.mavlink_relay import MAVLinkRelayOutput
from hydra_detect.tracker import TrackedObject, TrackingResult


def _make_track(track_id=1, label="person", confidence=0.9):
    return TrackedObject(
        track_id=track_id, x1=280, y1=200, x2=360, y2=400,
        confidence=confidence, class_id=0, label=label,
    )


def _make_tracking(*tracks):
    return TrackingResult(tracks=list(tracks), active_ids=len(tracks))


@pytest.fixture
def fake_mavlink():
    mav = MagicMock()
    mav.get_lat_lon.return_value = (47.12345, 8.54321, 25.0)
    mav.estimate_target_position.return_value = (47.12400, 8.54400)
    mav.send_raw_message.return_value = True
    mav._is_sim_gps = False
    return mav


def test_push_is_non_blocking_and_thread_safe(fake_mavlink):
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=1.0)
    # No thread started yet, but push must not raise.
    relay.push(_make_tracking(_make_track()), alert_classes=None, locked_track_id=None)
    assert relay.get_status()["enabled"] is False


def test_send_detections_emits_one_adsb_per_track(fake_mavlink):
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=1.0)
    relay.push(
        _make_tracking(_make_track(1, "person"), _make_track(2, "car")),
        alert_classes=None,
        locked_track_id=2,
    )
    # Drive the sender synchronously.
    relay._send_detections(time.monotonic())
    assert fake_mavlink.send_raw_message.call_count == 2
    assert relay._events_sent == 2


def test_throttle_blocks_rapid_resends(fake_mavlink):
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=10.0)
    tracks = _make_tracking(_make_track(1, "person"))
    relay.push(tracks, alert_classes=None, locked_track_id=None)
    relay._send_detections(time.monotonic())
    relay._send_detections(time.monotonic())  # within throttle
    assert fake_mavlink.send_raw_message.call_count == 1


def test_alert_classes_filter_skips_unlisted_labels(fake_mavlink):
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=0.1)
    relay.push(
        _make_tracking(_make_track(1, "person"), _make_track(2, "dog")),
        alert_classes={"person"},
        locked_track_id=None,
    )
    relay._send_detections(time.monotonic())
    assert fake_mavlink.send_raw_message.call_count == 1


def test_no_gps_fix_skips_emission(fake_mavlink):
    fake_mavlink.get_lat_lon.return_value = (None, None, None)
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=0.1)
    relay.push(
        _make_tracking(_make_track(1, "person")),
        alert_classes=None,
        locked_track_id=None,
    )
    relay._send_detections(time.monotonic())
    fake_mavlink.send_raw_message.assert_not_called()


def test_projection_failure_is_skipped(fake_mavlink):
    fake_mavlink.estimate_target_position.return_value = None
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=0.1)
    relay.push(
        _make_tracking(_make_track(1, "person")),
        alert_classes=None,
        locked_track_id=None,
    )
    relay._send_detections(time.monotonic())
    fake_mavlink.send_raw_message.assert_not_called()


def test_locked_flag_propagates(fake_mavlink):
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=0.1)
    relay.push(
        _make_tracking(_make_track(7, "person")),
        alert_classes=None,
        locked_track_id=7,
    )
    relay._send_detections(time.monotonic())
    assert fake_mavlink.send_raw_message.call_count == 1
    (msg,), _ = fake_mavlink.send_raw_message.call_args
    # msg is a pymavlink ADSB_VEHICLE — the locked flag bit must be set.
    from hydra_detect.tak import adsb_codec
    assert msg.flags & adsb_codec.FLAG_LOCKED


def test_get_status_reports_enabled_and_events(fake_mavlink):
    relay = MAVLinkRelayOutput(fake_mavlink, emit_interval=0.1)
    status = relay.get_status()
    assert set(status) >= {"enabled", "events_sent", "emit_interval"}
    assert status["events_sent"] == 0


def test_start_requires_mavlink():
    # Simulate a None MAVLinkIO — start must refuse cleanly.
    relay = MAVLinkRelayOutput(mavlink_io=None, emit_interval=1.0)  # type: ignore[arg-type]
    assert relay.start() is False
