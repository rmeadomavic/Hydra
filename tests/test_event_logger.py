"""Tests for EventLogger — operator action and vehicle track logging."""

from __future__ import annotations

import json
import time
from pathlib import Path

from hydra_detect.event_logger import EventLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger(tmp_path: Path, **kwargs) -> EventLogger:
    """Create an EventLogger writing to tmp_path with test defaults."""
    defaults = dict(
        log_dir=str(tmp_path / "events"),
        callsign="TEST",
    )
    defaults.update(kwargs)
    return EventLogger(**defaults)


def _read_events(tmp_path: Path) -> list[dict]:
    """Read all JSONL event files from the events directory."""
    events_dir = tmp_path / "events"
    if not events_dir.exists():
        return []
    events = []
    for f in sorted(events_dir.glob("*.jsonl")):
        for line in f.read_text().strip().splitlines():
            events.append(json.loads(line))
    return events


# ---------------------------------------------------------------------------
# Tests: mission start/end
# ---------------------------------------------------------------------------

class TestMissionLifecycle:
    def test_start_end_mission_creates_file(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("patrol_alpha")
        el.end_mission()

        events = _read_events(tmp_path)
        assert len(events) == 2
        assert events[0]["type"] == "mission_start"
        assert events[0]["name"] == "patrol_alpha"
        assert events[0]["callsign"] == "TEST"
        assert events[1]["type"] == "mission_end"
        assert events[1]["name"] == "patrol_alpha"

    def test_start_mission_closes_previous(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("mission_1")
        el.start_mission("mission_2")
        el.end_mission()

        events_dir = tmp_path / "events"
        files = list(events_dir.glob("*.jsonl"))
        assert len(files) == 2

    def test_stop_ends_mission(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.stop()

        events = _read_events(tmp_path)
        types = [e["type"] for e in events]
        assert "mission_end" in types


# ---------------------------------------------------------------------------
# Tests: log_action
# ---------------------------------------------------------------------------

class TestLogAction:
    def test_log_action_writes_to_file(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.log_action("lock", {"track_id": 5, "label": "person"})
        el.end_mission()

        events = _read_events(tmp_path)
        actions = [e for e in events if e["type"] == "action"]
        assert len(actions) == 1
        assert actions[0]["action"] == "lock"
        assert actions[0]["track_id"] == 5
        assert actions[0]["label"] == "person"

    def test_log_action_with_no_details(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.log_action("abort")
        el.end_mission()

        events = _read_events(tmp_path)
        actions = [e for e in events if e["type"] == "action"]
        assert len(actions) == 1
        assert actions[0]["action"] == "abort"


# ---------------------------------------------------------------------------
# Tests: log_vehicle_track (1 Hz rate limit)
# ---------------------------------------------------------------------------

class TestVehicleTrack:
    def test_track_respects_rate_limit(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")

        # First call should write
        el.log_vehicle_track(lat=35.0, lon=-80.0, alt=100.0)
        # Immediate second call should be rate-limited
        el.log_vehicle_track(lat=35.001, lon=-80.001, alt=101.0)

        el.end_mission()

        events = _read_events(tmp_path)
        tracks = [e for e in events if e["type"] == "track"]
        assert len(tracks) == 1
        assert tracks[0]["lat"] == 35.0

    def test_track_writes_after_interval(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el._track_interval = 0.01  # Override to 10ms for fast test
        el.start_mission("test")

        el.log_vehicle_track(lat=35.0, lon=-80.0, alt=100.0)
        time.sleep(0.02)  # Wait past interval
        el.log_vehicle_track(lat=35.001, lon=-80.001, alt=101.0)

        el.end_mission()

        events = _read_events(tmp_path)
        tracks = [e for e in events if e["type"] == "track"]
        assert len(tracks) == 2

    def test_track_includes_optional_fields(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")

        el.log_vehicle_track(
            lat=35.0, lon=-80.0, alt=100.0,
            heading=270.0, speed=5.5, mode="AUTO",
        )
        el.end_mission()

        events = _read_events(tmp_path)
        tracks = [e for e in events if e["type"] == "track"]
        assert len(tracks) == 1
        assert tracks[0]["heading"] == 270.0
        assert tracks[0]["speed"] == 5.5
        assert tracks[0]["mode"] == "AUTO"

    def test_track_omits_none_optional_fields(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")

        el.log_vehicle_track(lat=35.0, lon=-80.0, alt=100.0)
        el.end_mission()

        events = _read_events(tmp_path)
        tracks = [e for e in events if e["type"] == "track"]
        assert "heading" not in tracks[0]
        assert "speed" not in tracks[0]
        assert "mode" not in tracks[0]


# ---------------------------------------------------------------------------
# Tests: log_detection
# ---------------------------------------------------------------------------

class TestLogDetection:
    def test_log_detection_writes_correct_fields(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.log_detection(
            track_id=3, label="car", confidence=0.87654,
            lat=35.1, lon=-80.2,
        )
        el.end_mission()

        events = _read_events(tmp_path)
        dets = [e for e in events if e["type"] == "detection"]
        assert len(dets) == 1
        assert dets[0]["track_id"] == 3
        assert dets[0]["label"] == "car"
        assert dets[0]["confidence"] == 0.877  # rounded to 3 decimal
        assert dets[0]["lat"] == 35.1
        assert dets[0]["lon"] == -80.2


# ---------------------------------------------------------------------------
# Tests: log_state_change
# ---------------------------------------------------------------------------

class TestLogStateChange:
    def test_log_state_change_writes(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.log_state_change("camera_lost")
        el.log_state_change("camera_restored", {"downtime_sec": 5.2})
        el.end_mission()

        events = _read_events(tmp_path)
        states = [e for e in events if e["type"] == "state"]
        assert len(states) == 2
        assert states[0]["state"] == "camera_lost"
        assert states[1]["state"] == "camera_restored"
        assert states[1]["downtime_sec"] == 5.2


# ---------------------------------------------------------------------------
# Tests: no write when no mission active
# ---------------------------------------------------------------------------

class TestNoMissionGuard:
    def test_no_write_without_mission(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        # No mission started
        el.log_action("lock", {"track_id": 1})
        el.log_vehicle_track(lat=0.0, lon=0.0, alt=0.0)
        el.log_detection(track_id=1, label="person", confidence=0.9)
        el.log_state_change("camera_lost")

        events = _read_events(tmp_path)
        assert len(events) == 0

    def test_no_write_after_mission_ended(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.end_mission()

        el.log_action("lock", {"track_id": 1})

        events = _read_events(tmp_path)
        # Only mission_start and mission_end
        assert len(events) == 2


# ---------------------------------------------------------------------------
# Tests: get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_status_no_mission(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        status = el.get_status()
        assert status["mission_active"] is False
        assert status["mission_name"] is None

    def test_status_active_mission(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("recon_bravo")
        status = el.get_status()
        assert status["mission_active"] is True
        assert status["mission_name"] == "recon_bravo"
        el.stop()

    def test_status_after_end(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.end_mission()
        status = el.get_status()
        assert status["mission_active"] is False
        assert status["mission_name"] is None


# ---------------------------------------------------------------------------
# Tests: get_recent_events
# ---------------------------------------------------------------------------

class TestGetRecentEvents:
    def test_get_recent_events_returns_events(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.log_action("lock", {"track_id": 1})
        el.log_action("unlock", {"track_id": 1})

        events = el.get_recent_events()
        assert len(events) == 3  # mission_start + 2 actions
        assert events[0]["type"] == "mission_start"
        assert events[1]["action"] == "lock"
        assert events[2]["action"] == "unlock"
        el.stop()

    def test_get_recent_events_empty_when_no_mission(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        events = el.get_recent_events()
        assert events == []

    def test_get_recent_events_respects_max(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        for i in range(10):
            el.log_action("test", {"i": i})

        events = el.get_recent_events(max_events=3)
        assert len(events) == 3
        el.stop()


# ---------------------------------------------------------------------------
# Tests: event record format
# ---------------------------------------------------------------------------

class TestEventFormat:
    def test_event_has_timestamp(self, tmp_path: Path):
        el = _make_logger(tmp_path)
        el.start_mission("test")
        el.end_mission()

        events = _read_events(tmp_path)
        for e in events:
            assert "ts" in e
            assert isinstance(e["ts"], float)

    def test_event_has_callsign(self, tmp_path: Path):
        el = _make_logger(tmp_path, callsign="HYDRA-USV")
        el.start_mission("test")
        el.end_mission()

        events = _read_events(tmp_path)
        for e in events:
            assert e["callsign"] == "HYDRA-USV"

    def test_file_naming_convention(self, tmp_path: Path):
        el = _make_logger(tmp_path, callsign="HYDRA")
        el.start_mission("patrol")
        el.stop()

        events_dir = tmp_path / "events"
        files = list(events_dir.glob("*.jsonl"))
        assert len(files) == 1
        name = files[0].name
        assert name.startswith("HYDRA_")
        assert "_patrol.jsonl" in name
