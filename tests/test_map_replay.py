"""Tests for after-action review map replay (event timeline API endpoints)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, _auth_failures, stream_state


SAMPLE_EVENT_TIMELINE = [
    {"type": "mission_start", "ts": 1711720000, "name": "patrol-alpha"},
    {"type": "track", "ts": 1711720001, "lat": 34.050, "lon": -118.250, "alt": 10.0, "hdg": 90},
    {"type": "track", "ts": 1711720002, "lat": 34.051, "lon": -118.251, "alt": 10.0, "hdg": 92},
    {"type": "action", "ts": 1711720002.5, "action": "target_lock", "track_id": 3},
    {"type": "track", "ts": 1711720003, "lat": 34.052, "lon": -118.252, "alt": 10.0, "hdg": 95},
    {"type": "state", "ts": 1711720004, "state": "loiter"},
    {"type": "track", "ts": 1711720005, "lat": 34.053, "lon": -118.253, "alt": 10.0, "hdg": 100},
    {"type": "mission_end", "ts": 1711720006},
]


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset stream_state and auth between tests."""
    configure_auth(None)
    _auth_failures.clear()
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def event_log_dir(tmp_path):
    """Create a temp log dir with an event timeline JSONL file."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Write event timeline file
    event_file = log_dir / "HYDRA-1_20260329_143207_patrol.jsonl"
    with open(event_file, "w") as f:
        for evt in SAMPLE_EVENT_TIMELINE:
            f.write(json.dumps(evt) + "\n")
    # Write a detection log (not an event timeline) to verify it's excluded
    det_file = log_dir / "detections_20260329.jsonl"
    with open(det_file, "w") as f:
        f.write(json.dumps({"timestamp": "2026-03-29T14:00:00Z", "frame": 1,
                            "track_id": 1, "label": "person", "confidence": 0.9,
                            "chain_hash": "abc123"}) + "\n")
    # Set the callback so server finds this dir
    stream_state.set_callbacks(get_log_dir=lambda: str(log_dir))
    return log_dir


# ---------------------------------------------------------------------------
# GET /api/review/events/{filename}
# ---------------------------------------------------------------------------

class TestReviewEvents:
    def test_returns_events(self, client, event_log_dir):
        resp = client.get("/api/review/events/HYDRA-1_20260329_143207_patrol.jsonl")
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "HYDRA-1_20260329_143207_patrol.jsonl"
        assert len(data["events"]) == len(SAMPLE_EVENT_TIMELINE)
        # Check first event
        assert data["events"][0]["type"] == "mission_start"
        assert data["events"][0]["name"] == "patrol-alpha"
        # Check track events have lat/lon
        track_events = [e for e in data["events"] if e["type"] == "track"]
        assert len(track_events) == 4
        assert track_events[0]["lat"] == 34.050

    def test_invalid_filename_dotdot(self, client, event_log_dir):
        resp = client.get("/api/review/events/..secret.jsonl")
        assert resp.status_code == 400
        assert "invalid filename" in resp.json()["error"]

    def test_invalid_filename_traversal(self, client, event_log_dir):
        """Path traversal with encoded slashes should be blocked."""
        resp = client.get("/api/review/events/foo%2F..%2Fbar.jsonl")
        # Starlette may normalize or reject — either 400 or 404 is acceptable
        assert resp.status_code in (400, 404)

    def test_invalid_filename_backslash(self, client, event_log_dir):
        resp = client.get("/api/review/events/foo%5Cbar.jsonl")
        assert resp.status_code in (400, 404)

    def test_missing_file_404(self, client, event_log_dir):
        resp = client.get("/api/review/events/nonexistent.jsonl")
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]

    def test_non_jsonl_404(self, client, event_log_dir):
        """Non-.jsonl files should return 404 even if they exist."""
        # Create a .csv file
        (event_log_dir / "test.csv").write_text("a,b,c\n1,2,3\n")
        resp = client.get("/api/review/events/test.csv")
        assert resp.status_code == 404

    def test_malformed_lines_skipped(self, client, event_log_dir):
        """Malformed JSON lines should be skipped without error."""
        bad_file = event_log_dir / "bad_events.jsonl"
        with open(bad_file, "w") as f:
            f.write(json.dumps({"type": "mission_start", "ts": 1000}) + "\n")
            f.write("this is not valid json\n")
            f.write("\n")  # empty line
            f.write(json.dumps({"type": "track", "ts": 1001, "lat": 34.0, "lon": -118.0}) + "\n")
        resp = client.get("/api/review/events/bad_events.jsonl")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 2
        assert data["events"][0]["type"] == "mission_start"
        assert data["events"][1]["type"] == "track"


# ---------------------------------------------------------------------------
# GET /api/review/logs — event_logs field
# ---------------------------------------------------------------------------

class TestReviewLogsEventLogs:
    def test_includes_event_logs_field(self, client, event_log_dir):
        resp = client.get("/api/review/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "event_logs" in data

    def test_event_timeline_listed(self, client, event_log_dir):
        resp = client.get("/api/review/logs")
        data = resp.json()
        event_filenames = [e["filename"] for e in data["event_logs"]]
        assert "HYDRA-1_20260329_143207_patrol.jsonl" in event_filenames

    def test_detection_log_not_in_event_logs(self, client, event_log_dir):
        """Detection logs (chain_hash) should not appear in event_logs."""
        resp = client.get("/api/review/logs")
        data = resp.json()
        event_filenames = [e["filename"] for e in data["event_logs"]]
        assert "detections_20260329.jsonl" not in event_filenames

    def test_event_log_has_size(self, client, event_log_dir):
        resp = client.get("/api/review/logs")
        data = resp.json()
        for el in data["event_logs"]:
            assert "size_kb" in el
            assert el["size_kb"] >= 0
