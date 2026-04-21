"""Regression tests for EventLogger in-memory ring buffer.

get_recent_events must no longer re-open and read the mission JSONL
from disk on every call. Dashboard polls used to block the pipeline's
write path for the duration of a Path.read_text() on the active log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hydra_detect.event_logger import EventLogger


def test_get_recent_events_returns_logged_events(tmp_path: Path):
    el = EventLogger(log_dir=tmp_path)
    el.start_mission("unit-test")
    el.log_action("lock", {"track_id": 7})
    el.log_detection(track_id=7, label="person", confidence=0.92)
    el.log_state_change("low_light")

    events = el.get_recent_events()
    # start_mission also logs a "mission_start" event; expect at least 4
    types = [e["type"] for e in events]
    assert "mission_start" in types
    assert "action" in types
    assert "detection" in types
    assert "state" in types
    el.end_mission()


def test_get_recent_events_reads_from_memory_not_disk(tmp_path: Path):
    """After log file is closed, ring buffer should still serve reads."""
    el = EventLogger(log_dir=tmp_path)
    el.start_mission("memory-read")
    el.log_action("strike", {"track_id": 3})
    el.end_mission()
    # After end_mission the file handle is closed; the old implementation
    # returned [] here because it tried to re-read the closed file.
    events = el.get_recent_events()
    types = [e["type"] for e in events]
    assert "action" in types, f"Expected 'action' in {types}"
    assert "mission_end" in types


def test_ring_buffer_cleared_on_new_mission(tmp_path: Path):
    """Starting a new mission must wipe the previous mission's events."""
    el = EventLogger(log_dir=tmp_path)
    el.start_mission("mission-one")
    el.log_action("lock", {"track_id": 1})
    el.end_mission()

    el.start_mission("mission-two")
    events = el.get_recent_events()
    # Only "mission_start" for mission-two should be present — the lock
    # from mission-one must be gone.
    assert all(e.get("name") != "mission-one" for e in events)
    actions = [e for e in events if e.get("type") == "action"]
    assert actions == [], "Previous mission actions leaked into new mission"
    el.end_mission()


def test_max_events_truncation(tmp_path: Path):
    el = EventLogger(log_dir=tmp_path)
    el.start_mission("truncate-test")
    for i in range(50):
        el.log_action("ping", {"seq": i})

    latest_5 = el.get_recent_events(max_events=5)
    assert len(latest_5) == 5
    # The latest event should be the last ping
    assert latest_5[-1].get("seq") == 49
    el.end_mission()


def test_ring_buffer_bounded(tmp_path: Path):
    """Ring buffer size is capped — old events drop when full."""
    el = EventLogger(log_dir=tmp_path)
    el.start_mission("bound-test")

    # Push more events than the default buffer holds.
    cap = EventLogger._RECENT_DEFAULT
    for i in range(cap + 50):
        el.log_action("ping", {"seq": i})

    all_buffered = el.get_recent_events(max_events=cap + 100)
    assert len(all_buffered) <= cap + 1  # +1 for mission_start
    # Earliest "ping" retained should be seq >= 50 (the first 50 got evicted).
    pings = [e for e in all_buffered if e.get("type") == "action"]
    assert pings[0]["seq"] >= 50
    el.end_mission()
