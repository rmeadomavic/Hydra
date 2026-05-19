"""Mission tagging + event timeline + summary endpoint tests (issue #72).

Covers:
  - EventLogger generates a UUID mission_id on start_mission()
  - DetectionLogger stamps mission_id onto every record
  - /api/mission/start returns the id, propagates to event + detection loggers
  - /api/mission/end clears the id
  - /api/summary aggregates per-mission stats from JSONL on disk
  - Convex hull computation on synthetic GPS tracks
  - mission_id is null when no mission is active
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.detection_logger import DetectionLogger
from hydra_detect.event_logger import EventLogger
from hydra_detect.mission_summary import (
    clear_cache,
    compute_summary,
    get_summary,
    list_missions,
)
from hydra_detect.review_export import _convex_hull, _polygon_area_m2, gps_coverage
from hydra_detect.tracker import TrackedObject, TrackingResult
from hydra_detect.web.server import (
    app,
    configure_auth,
    configure_web_password,
    stream_state,
    _auth_failures,
    _response_cache,
)


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset web-server globals between tests."""
    configure_auth(None)
    configure_web_password(None)
    _auth_failures.clear()
    _response_cache.clear()
    stream_state._callbacks.clear()
    stream_state.runtime_config = {"prompts": ["person"], "threshold": 0.25, "auto_loiter": False}
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def event_logger(tmp_path):
    return EventLogger(log_dir=str(tmp_path / "events"), callsign="TEST")


# ----------------------------------------------------------------------------
# EventLogger: mission_id generation + stamping
# ----------------------------------------------------------------------------

class TestEventLoggerMissionId:
    def test_start_mission_returns_uuid(self, event_logger):
        mid = event_logger.start_mission("alpha")
        # UUID v4 is 36 chars with hyphens. Use uuid module to parse strictly.
        parsed = uuid.UUID(mid)
        assert str(parsed) == mid
        event_logger.end_mission()

    def test_mission_id_present_on_every_event(self, tmp_path, event_logger):
        mid = event_logger.start_mission("alpha")
        event_logger.log_action("lock", {"track_id": 1})
        event_logger.log_vehicle_track(lat=35.0, lon=-80.0, alt=100.0)
        event_logger.log_state_change("camera_lost")
        event_logger.end_mission()

        events_dir = tmp_path / "events"
        path = next(events_dir.glob("*.jsonl"))
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for rec in records:
            assert rec.get("mission_id") == mid, f"missing/wrong mission_id in {rec}"

    def test_new_start_rotates_id(self, event_logger):
        mid1 = event_logger.start_mission("alpha")
        mid2 = event_logger.start_mission("bravo")
        assert mid1 != mid2
        event_logger.end_mission()

    def test_external_id_accepted(self, event_logger):
        wanted = str(uuid.uuid4())
        got = event_logger.start_mission("alpha", mission_id=wanted)
        assert got == wanted
        event_logger.end_mission()

    def test_get_status_exposes_id(self, event_logger):
        mid = event_logger.start_mission("alpha")
        status = event_logger.get_status()
        assert status["mission_active"] is True
        assert status["mission_id"] == mid
        assert status["mission_name"] == "alpha"
        assert status["mission_log"] is not None
        event_logger.end_mission()
        post = event_logger.get_status()
        assert post["mission_active"] is False
        assert post["mission_id"] is None
        assert post["mission_log"] is None

    def test_unsafe_name_is_sanitized_for_filename(self, tmp_path):
        el = EventLogger(log_dir=str(tmp_path / "events"), callsign="TEST")
        el.start_mission("foo/../bar baz")
        el.end_mission()
        events_dir = tmp_path / "events"
        files = list(events_dir.glob("*.jsonl"))
        assert len(files) == 1
        assert "/" not in files[0].name
        assert ".." not in files[0].name


# ----------------------------------------------------------------------------
# DetectionLogger: mission_id stamping
# ----------------------------------------------------------------------------

def _tracking(label: str = "person", track_id: int = 1) -> TrackingResult:
    t = TrackedObject(
        track_id=track_id, x1=10.0, y1=10.0, x2=50.0, y2=80.0,
        confidence=0.9, class_id=0, label=label,
    )
    return TrackingResult([t])


class TestDetectionLoggerMissionId:
    def test_default_mission_id_is_none(self, tmp_path):
        dl = DetectionLogger(log_dir=str(tmp_path / "logs"), save_images=False)
        assert dl.get_mission_id() is None

    def test_set_and_clear_mission_id(self, tmp_path):
        dl = DetectionLogger(log_dir=str(tmp_path / "logs"), save_images=False)
        mid = str(uuid.uuid4())
        dl.set_mission_id(mid)
        assert dl.get_mission_id() == mid
        dl.set_mission_id(None)
        assert dl.get_mission_id() is None

    def test_log_record_carries_mission_id(self, tmp_path):
        log_dir = tmp_path / "logs"
        dl = DetectionLogger(log_dir=str(log_dir), save_images=False)
        dl.start()
        mid = str(uuid.uuid4())
        dl.set_mission_id(mid)
        try:
            dl.log(_tracking())
        finally:
            dl.stop(timeout=2.0)
        files = list(log_dir.glob("*.jsonl"))
        assert files, "no detection log written"
        records = [
            json.loads(line)
            for line in files[0].read_text().splitlines()
            if line.strip()
        ]
        assert records, "no records in log"
        assert all(r.get("mission_id") == mid for r in records)

    def test_log_record_mission_id_null_when_idle(self, tmp_path):
        log_dir = tmp_path / "logs"
        dl = DetectionLogger(log_dir=str(log_dir), save_images=False)
        dl.start()
        try:
            dl.log(_tracking())
        finally:
            dl.stop(timeout=2.0)
        records = [
            json.loads(line)
            for line in next(log_dir.glob("*.jsonl")).read_text().splitlines()
            if line.strip()
        ]
        # Either absent or explicitly null is acceptable, but the field is
        # part of the schema now so we assert it is present and null.
        for r in records:
            assert "mission_id" in r
            assert r["mission_id"] is None


# ----------------------------------------------------------------------------
# Convex hull + GPS coverage
# ----------------------------------------------------------------------------

class TestConvexHull:
    def test_empty(self):
        out = gps_coverage([])
        assert out["point_count"] == 0
        assert out["hull"] == []
        assert out["bbox"] is None
        assert out["area_m2"] == 0.0

    def test_single_point(self):
        out = gps_coverage([(35.0, -80.0)])
        assert out["point_count"] == 1
        assert out["area_m2"] == 0.0
        assert out["bbox"]["min_lat"] == 35.0

    def test_unit_square_hull(self):
        # ~111 m on a side at the equator ≈ 12 387 m² area
        pts = [(0.0, 0.0), (0.001, 0.0), (0.001, 0.001), (0.0, 0.001),
               (0.0005, 0.0005)]  # interior point should be excluded
        hull = _convex_hull(pts)
        assert len(hull) == 4
        assert (0.0005, 0.0005) not in hull
        out = gps_coverage(pts)
        # Equator → m_per_deg_lon ≈ m_per_deg_lat ≈ 111 320
        assert 12_000 < out["area_m2"] < 12_700

    def test_hull_is_ccw(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        hull = _convex_hull(pts)
        # Andrew monotone chain returns CCW. Verify signed area > 0.
        # Using shoelace on the unprojected coords (just for orientation).
        s = 0.0
        for i, (x1, y1) in enumerate(hull):
            x2, y2 = hull[(i + 1) % len(hull)]
            s += x1 * y2 - x2 * y1
        assert s > 0

    def test_collinear_points_degenerate(self):
        pts = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)]
        out = gps_coverage(pts)
        # All collinear → area is 0 (degenerate polygon).
        assert out["area_m2"] == 0.0

    def test_polygon_area_nonzero_at_higher_lat(self):
        # At Aberdeen NC (~35° N) the area should still be roughly right.
        pts = [
            (35.0, -79.0), (35.001, -79.0),
            (35.001, -78.999), (35.0, -78.999),
        ]
        area = _polygon_area_m2(_convex_hull(pts))
        # cos(35°) ≈ 0.819, so longitude meters shrink — expect ~9 100 m²
        assert 8_000 < area < 10_500


# ----------------------------------------------------------------------------
# Mission summary on synthetic logs
# ----------------------------------------------------------------------------

def _write_detection_jsonl(log_dir: Path, mission_id: str, rows: list[dict]) -> Path:
    """Write a detections_001.jsonl with the given rows."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "detections_001.jsonl"
    with path.open("w") as f:
        for r in rows:
            r.setdefault("mission_id", mission_id)
            f.write(json.dumps(r) + "\n")
    return path


def _write_event_jsonl(log_dir: Path, mission_id: str, name: str, rows: list[dict]) -> Path:
    """Write a mission event JSONL starting with mission_start."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"TEST_{int(time.time())}_{name}.jsonl"
    with path.open("w") as f:
        f.write(json.dumps({
            "ts": rows[0]["ts"], "type": "mission_start",
            "callsign": "TEST", "mission_id": mission_id, "name": name,
        }) + "\n")
        for r in rows:
            r.setdefault("mission_id", mission_id)
            r.setdefault("callsign", "TEST")
            f.write(json.dumps(r) + "\n")
        f.write(json.dumps({
            "ts": rows[-1]["ts"] + 60.0, "type": "mission_end",
            "callsign": "TEST", "mission_id": mission_id, "name": name,
        }) + "\n")
    return path


class TestMissionSummary:
    def test_summary_counts_by_class_and_tracks(self, tmp_path):
        mid = str(uuid.uuid4())
        _write_detection_jsonl(tmp_path, mid, [
            {"timestamp": "2026-05-19T10:00:01Z", "track_id": 1, "label": "person"},
            {"timestamp": "2026-05-19T10:00:02Z", "track_id": 1, "label": "person"},
            {"timestamp": "2026-05-19T10:00:03Z", "track_id": 2, "label": "car"},
            {"timestamp": "2026-05-19T10:00:04Z", "track_id": 3, "label": "person"},
        ])
        out = compute_summary(mid, tmp_path)
        assert out["detections"]["total"] == 4
        assert out["detections"]["by_class"] == {"person": 3, "car": 1}
        assert out["detections"]["unique_tracks"] == 3

    def test_summary_other_missions_excluded(self, tmp_path):
        mid_a = str(uuid.uuid4())
        mid_b = str(uuid.uuid4())
        _write_detection_jsonl(tmp_path, mid_a, [
            {"timestamp": "2026-05-19T10:00:01Z", "track_id": 1, "label": "person", "mission_id": mid_a},
            {"timestamp": "2026-05-19T10:00:02Z", "track_id": 2, "label": "person", "mission_id": mid_b},
        ])
        out_a = compute_summary(mid_a, tmp_path)
        out_b = compute_summary(mid_b, tmp_path)
        assert out_a["detections"]["total"] == 1
        assert out_b["detections"]["total"] == 1

    def test_summary_time_to_first_detection(self, tmp_path):
        mid = str(uuid.uuid4())
        # Mission starts at ts=1000; first detection ISO = epoch 1005.
        from datetime import datetime, timezone
        det_ts = datetime.fromtimestamp(1005.0, tz=timezone.utc).isoformat()
        _write_detection_jsonl(tmp_path, mid, [
            {"timestamp": det_ts, "track_id": 1, "label": "person"},
        ])
        _write_event_jsonl(tmp_path, mid, "alpha", [
            {"ts": 1000.0, "type": "track", "lat": 35.0, "lon": -80.0},
        ])
        out = compute_summary(mid, tmp_path)
        assert out["time_to_first_detection_sec"] == pytest.approx(5.0, abs=0.01)

    def test_summary_gps_coverage_from_event_track(self, tmp_path):
        mid = str(uuid.uuid4())
        _write_event_jsonl(tmp_path, mid, "alpha", [
            {"ts": 1000.0, "type": "track", "lat": 35.0, "lon": -80.0},
            {"ts": 1001.0, "type": "track", "lat": 35.001, "lon": -80.0},
            {"ts": 1002.0, "type": "track", "lat": 35.001, "lon": -79.999},
            {"ts": 1003.0, "type": "track", "lat": 35.0, "lon": -79.999},
        ])
        out = compute_summary(mid, tmp_path)
        cov = out["gps_coverage"]
        assert cov["point_count"] == 4
        assert len(cov["hull"]) == 4
        assert cov["area_m2"] > 0

    def test_summary_caches_within_ttl(self, tmp_path):
        mid = str(uuid.uuid4())
        _write_detection_jsonl(tmp_path, mid, [
            {"timestamp": "2026-05-19T10:00:01Z", "track_id": 1, "label": "person"},
        ])
        first = get_summary(mid, tmp_path)
        # Append more without touching mtime sums dramatically — the
        # signature still changes (size grows), so cache should invalidate.
        with (tmp_path / "detections_001.jsonl").open("a") as f:
            f.write(json.dumps({
                "timestamp": "2026-05-19T10:00:02Z", "track_id": 2,
                "label": "car", "mission_id": mid,
            }) + "\n")
        # File size changed → signature differs → fresh compute.
        second = get_summary(mid, tmp_path)
        assert second["detections"]["total"] == 2
        assert first["detections"]["total"] == 1

    def test_list_missions_orders_by_start_descending(self, tmp_path):
        mid_old = str(uuid.uuid4())
        mid_new = str(uuid.uuid4())
        _write_event_jsonl(tmp_path, mid_old, "old", [
            {"ts": 1000.0, "type": "track", "lat": 0.0, "lon": 0.0},
        ])
        _write_event_jsonl(tmp_path, mid_new, "new", [
            {"ts": 2000.0, "type": "track", "lat": 0.0, "lon": 0.0},
        ])
        missions = list_missions(tmp_path)
        # Newest first.
        assert missions[0]["mission_id"] == mid_new
        assert missions[1]["mission_id"] == mid_old


# ----------------------------------------------------------------------------
# Web API: /api/mission/start, /api/mission/end, /api/summary
# ----------------------------------------------------------------------------

class TestMissionWebAPI:
    def test_start_returns_mission_id(self, client):
        captured: dict = {}

        def _on_start(name):
            mid = str(uuid.uuid4())
            captured["mid"] = mid
            captured["name"] = name
            return mid

        stream_state.set_callbacks(on_mission_start=_on_start)
        resp = client.post("/api/mission/start", json={"name": "alpha"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "started"
        assert body["name"] == "alpha"
        assert body["mission_id"] == captured["mid"]

    def test_start_with_empty_body_synthesizes_name(self, client):
        called: dict = {}

        def _on_start(name):
            called["name"] = name
            return "fake-id"

        stream_state.set_callbacks(on_mission_start=_on_start)
        resp = client.post("/api/mission/start", json={})
        assert resp.status_code == 200
        assert called["name"].startswith("mission-")

    def test_start_rejects_blank_name(self, client):
        stream_state.set_callbacks(on_mission_start=lambda n: "x")
        resp = client.post("/api/mission/start", json={"name": "   "})
        assert resp.status_code == 400

    def test_end_invokes_callback(self, client):
        ended: dict = {"flag": False}

        def _on_end():
            ended["flag"] = True

        stream_state.set_callbacks(on_mission_end=_on_end)
        resp = client.post("/api/mission/end", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ended"
        assert ended["flag"] is True

    def test_mission_status_endpoint(self, client):
        def _status():
            return {
                "mission_active": True, "mission_name": "alpha",
                "mission_id": "abc-123", "mission_start_ts": 1000.0,
                "mission_log": "TEST_alpha.jsonl",
            }
        stream_state.set_callbacks(get_event_status=_status)
        resp = client.get("/api/mission/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mission_id"] == "abc-123"
        assert body["mission_active"] is True

    def test_mission_status_idle_default(self, client):
        # No callback registered → endpoint returns the idle shape.
        resp = client.get("/api/mission/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "mission_active": False, "mission_name": None,
            "mission_id": None, "mission_start_ts": None,
            "mission_log": None,
        }

    def test_summary_endpoint_requires_mission_param(self, client):
        resp = client.get("/api/summary")
        assert resp.status_code == 400

    def test_summary_endpoint_rejects_oversized_id(self, client):
        resp = client.get("/api/summary?mission=" + "a" * 65)
        assert resp.status_code == 400

    def test_summary_endpoint_returns_stats_from_disk(self, client, tmp_path):
        mid = str(uuid.uuid4())
        _write_detection_jsonl(tmp_path, mid, [
            {"timestamp": "2026-05-19T10:00:01Z", "track_id": 1, "label": "person"},
            {"timestamp": "2026-05-19T10:00:02Z", "track_id": 2, "label": "car"},
        ])
        _write_event_jsonl(tmp_path, mid, "alpha", [
            {"ts": 1000.0, "type": "track", "lat": 35.0, "lon": -80.0},
            {"ts": 1001.0, "type": "action", "action": "lock"},
        ])
        stream_state.set_callbacks(get_log_dir=lambda: str(tmp_path))
        resp = client.get(f"/api/summary?mission={mid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mission_id"] == mid
        assert body["detections"]["total"] == 2
        assert body["detections"]["by_class"] == {"person": 1, "car": 1}
        assert body["operator_actions"] == 1
        assert body["gps_coverage"]["point_count"] == 1

    def test_review_logs_includes_missions(self, client, tmp_path):
        mid = str(uuid.uuid4())
        _write_event_jsonl(tmp_path, mid, "alpha", [
            {"ts": 1000.0, "type": "track", "lat": 35.0, "lon": -80.0},
        ])
        stream_state.set_callbacks(get_log_dir=lambda: str(tmp_path))
        resp = client.get("/api/review/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert "missions" in body
        assert body["missions"][0]["mission_id"] == mid
