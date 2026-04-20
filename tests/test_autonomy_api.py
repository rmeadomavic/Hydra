"""Integration tests for /api/autonomy/status and /api/autonomy/mode."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.autonomous import (
    AUTONOMY_LOG_MAXLEN,
    AutonomousController,
)
from hydra_detect.web.server import (
    _auth_failures,
    _response_cache,
    app,
    configure_auth,
    configure_web_password,
    set_autonomous_controller,
    stream_state,
)


@pytest.fixture(autouse=True)
def _reset_state():
    configure_auth(None)
    configure_web_password(None)
    _auth_failures.clear()
    _response_cache.clear()
    stream_state._callbacks.clear()
    stream_state.stats = {
        "fps": 0.0,
        "inference_ms": 0.0,
        "active_tracks": 0,
        "total_detections": 0,
        "detector": "n/a",
        "mavlink": False,
        "gps_fix": 0,
        "position": None,
    }
    set_autonomous_controller(None)
    yield
    set_autonomous_controller(None)
    configure_auth(None)


@pytest.fixture
def client():
    return TestClient(app)


def _make_controller(**overrides) -> AutonomousController:
    defaults = dict(
        enabled=True,
        geofence_lat=35.0527,
        geofence_lon=-79.4927,
        geofence_radius_m=100.0,
        min_confidence=0.85,
        min_track_frames=5,
        allowed_classes=["mine", "buoy", "kayak"],
        strike_cooldown_sec=30.0,
        allowed_vehicle_modes=["AUTO"],
        gps_max_stale_sec=2.0,
        require_operator_lock=True,
    )
    defaults.update(overrides)
    return AutonomousController(**defaults)


# ---------------------------------------------------------------------------
# GET /api/autonomy/status
# ---------------------------------------------------------------------------

class TestAutonomyStatusUnregistered:
    """No controller registered — endpoint must still serve the idle shape."""

    def test_returns_200_with_default_shape(self, client):
        resp = client.get("/api/autonomy/status")
        assert resp.status_code == 200
        data = resp.json()
        # Required top-level keys per impl_autonomy.md
        for key in ("mode", "enabled", "callsign", "geofence",
                    "self_position", "criteria", "gates", "log"):
            assert key in data
        assert data["mode"] == "dryrun"
        assert data["enabled"] is False
        assert data["log"] == []
        # All five gates must be present, all N/A on boot
        gate_ids = {g["id"] for g in data["gates"]}
        assert gate_ids == {"geofence", "vehicle_mode", "operator_lock",
                            "gps_fresh", "cooldown"}
        for gate in data["gates"]:
            assert gate["state"] == "N/A"

    def test_callsign_pulled_from_stats(self, client):
        stream_state.update_stats(callsign="HYDRA-5")
        resp = client.get("/api/autonomy/status")
        assert resp.status_code == 200
        assert resp.json()["callsign"] == "HYDRA-5"

    def test_no_auth_required(self, client):
        configure_auth("secret-token")
        # No Authorization header — must still be served
        resp = client.get("/api/autonomy/status")
        assert resp.status_code == 200


class TestAutonomyStatusRegistered:
    """Controller registered — endpoint returns the full snapshot shape."""

    def test_returns_full_shape(self, client):
        ctrl = _make_controller()
        set_autonomous_controller(ctrl)
        resp = client.get("/api/autonomy/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "dryrun"
        assert data["enabled"] is True
        assert data["geofence"]["shape"] == "CIRCLE"
        assert data["geofence"]["radius_m"] == 100.0
        assert data["geofence"]["center_lat"] == pytest.approx(35.0527)
        assert data["criteria"]["min_confidence"] == pytest.approx(0.85)
        assert data["criteria"]["allowed_vehicle_modes"] == "AUTO"
        assert data["criteria"]["allowed_classes"] == ["mine", "buoy", "kayak"]

    def test_polygon_geofence_shape(self, client):
        polygon = [(35.0, -79.0), (35.1, -79.0), (35.05, -79.1)]
        ctrl = _make_controller(geofence_polygon=polygon)
        set_autonomous_controller(ctrl)
        resp = client.get("/api/autonomy/status")
        data = resp.json()
        assert data["geofence"]["shape"] == "POLYGON"
        assert "35.0,-79.0" in data["geofence"]["polygon"]
        assert data["geofence"]["polygon"].count(";") == 2

    def test_gates_reflect_recorded_state(self, client):
        ctrl = _make_controller()
        ctrl._record_gate_evaluation("geofence", "PASS", "84m of 100m")
        ctrl._record_gate_evaluation("operator_lock", "FAIL", "no soft-lock")
        set_autonomous_controller(ctrl)
        resp = client.get("/api/autonomy/status")
        gates = {g["id"]: g for g in resp.json()["gates"]}
        assert gates["geofence"]["state"] == "PASS"
        assert gates["geofence"]["detail"] == "84m of 100m"
        assert gates["operator_lock"]["state"] == "FAIL"
        assert gates["operator_lock"]["detail"] == "no soft-lock"

    def test_log_newest_first(self, client):
        ctrl = _make_controller()
        ctrl._record_decision(1, "kayak", "reject", "first")
        ctrl._record_decision(2, "kayak", "engage", "second")
        set_autonomous_controller(ctrl)
        resp = client.get("/api/autonomy/status")
        log = resp.json()["log"]
        assert log[0]["reason"] == "second"
        assert log[1]["reason"] == "first"
        assert log[0]["action"] == "engage"

    def test_snapshot_is_deep_copy(self, client):
        """Mutating the response must not leak into the controller."""
        ctrl = _make_controller()
        ctrl._record_decision(1, "kayak", "reject", "test")
        set_autonomous_controller(ctrl)
        snap1 = ctrl.get_dashboard_snapshot(callsign="HYDRA-1")
        snap1["log"].clear()
        snap1["gates"].clear()
        # Second call must still include the entry — first was a deep copy
        snap2 = ctrl.get_dashboard_snapshot(callsign="HYDRA-1")
        assert len(snap2["log"]) == 1
        assert len(snap2["gates"]) == 5


# ---------------------------------------------------------------------------
# Bounded log
# ---------------------------------------------------------------------------

class TestAutonomyLogBound:
    def test_log_caps_at_200(self, client):
        ctrl = _make_controller()
        for i in range(300):
            ctrl._record_decision(i, "kayak", "reject", f"entry {i}")
        set_autonomous_controller(ctrl)
        resp = client.get("/api/autonomy/status")
        log = resp.json()["log"]
        assert len(log) == AUTONOMY_LOG_MAXLEN == 200
        # Newest-first: entry 299 comes before entry 100
        assert log[0]["reason"] == "entry 299"
        assert log[-1]["reason"] == "entry 100"


# ---------------------------------------------------------------------------
# POST /api/autonomy/mode
# ---------------------------------------------------------------------------

class TestAutonomyModePost:
    def test_valid_mode_transitions(self, client):
        configure_auth("secret-token")
        ctrl = _make_controller()
        set_autonomous_controller(ctrl)
        headers = {"Authorization": "Bearer secret-token"}
        for mode in ("dryrun", "shadow", "live"):
            resp = client.post("/api/autonomy/mode", json={"mode": mode}, headers=headers)
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"status": "ok", "mode": mode}
            assert ctrl.get_mode() == mode

    def test_status_reflects_posted_mode(self, client):
        configure_auth("secret-token")
        ctrl = _make_controller()
        set_autonomous_controller(ctrl)
        headers = {"Authorization": "Bearer secret-token"}
        client.post("/api/autonomy/mode", json={"mode": "live"}, headers=headers)
        resp = client.get("/api/autonomy/status")
        assert resp.json()["mode"] == "live"

    def test_rejects_invalid_mode(self, client):
        configure_auth("secret-token")
        ctrl = _make_controller()
        set_autonomous_controller(ctrl)
        headers = {"Authorization": "Bearer secret-token"}
        for bad in ("weapons_free", "", "DRYRUN", "off", None, 42):
            resp = client.post("/api/autonomy/mode", json={"mode": bad}, headers=headers)
            assert resp.status_code == 400
            assert "mode must be one of" in resp.json()["error"]
        assert ctrl.get_mode() == "dryrun"

    def test_rejects_missing_body(self, client):
        configure_auth("secret-token")
        set_autonomous_controller(_make_controller())
        headers = {
            "Authorization": "Bearer secret-token",
            "Content-Type": "application/json",
        }
        resp = client.post("/api/autonomy/mode", content="not json", headers=headers)
        assert resp.status_code == 400

    def test_rejects_without_auth(self, client):
        configure_auth("secret-token")
        set_autonomous_controller(_make_controller())
        resp = client.post("/api/autonomy/mode", json={"mode": "live"})
        assert resp.status_code == 401

    def test_rejects_wrong_token(self, client):
        configure_auth("secret-token")
        set_autonomous_controller(_make_controller())
        headers = {"Authorization": "Bearer wrong"}
        resp = client.post("/api/autonomy/mode", json={"mode": "live"}, headers=headers)
        assert resp.status_code == 403

    def test_returns_503_when_controller_unregistered(self, client):
        configure_auth("secret-token")
        # No controller registered
        headers = {"Authorization": "Bearer secret-token"}
        resp = client.post("/api/autonomy/mode", json={"mode": "live"}, headers=headers)
        assert resp.status_code == 503
        assert "not available" in resp.json()["error"]


# ---------------------------------------------------------------------------
# _record_decision / _record_gate_evaluation validation
# ---------------------------------------------------------------------------

class TestRecorderValidation:
    def test_invalid_gate_id_raises(self):
        ctrl = _make_controller()
        with pytest.raises(ValueError):
            ctrl._record_gate_evaluation("not_a_gate", "PASS", "")

    def test_invalid_gate_state_raises(self):
        ctrl = _make_controller()
        with pytest.raises(ValueError):
            ctrl._record_gate_evaluation("geofence", "MAYBE", "")

    def test_invalid_action_raises(self):
        ctrl = _make_controller()
        with pytest.raises(ValueError):
            ctrl._record_decision(1, "kayak", "nuke", "because")

    def test_decision_accepts_sha(self):
        ctrl = _make_controller()
        ctrl._record_decision(7, "kayak", "reject", "op lock required", sha256="7f2a")
        snap = ctrl.get_dashboard_snapshot(callsign="HYDRA-1")
        assert snap["log"][0]["sha256"] == "7f2a"
