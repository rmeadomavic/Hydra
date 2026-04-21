"""Tests that AutonomousController.evaluate() wires gate + decision recorders.

The recorders themselves (_record_gate_evaluation, _record_decision) have
unit tests in test_autonomy_api.py. These tests exercise the full evaluate()
path and assert the dashboard snapshot reflects what the gate + decision
logic actually decided — not stale values from a prior cycle.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from hydra_detect.autonomous import AutonomousController
from hydra_detect.tracker import TrackedObject, TrackingResult


def _make_mavlink(
    *,
    lat: float = 35.0527,
    lon: float = -79.4927,
    alt: float = 10.0,
    mode: str = "AUTO",
    stale_gps: bool = False,
):
    """Build a mock MAVLinkIO with configurable state.

    GPS last_update uses side_effect so every call returns a fresh monotonic
    timestamp — otherwise the mock's captured timestamp can go stale between
    multiple evaluate() calls in the same test.
    """
    mav = MagicMock()
    mav.get_lat_lon.return_value = (lat, lon, alt)
    mav.get_vehicle_mode.return_value = mode
    mav.get_position_string.return_value = f"{lat:.5f},{lon:.5f}"
    mav.gps_fix_ok = True
    if stale_gps:
        mav.get_gps.side_effect = lambda: {
            "last_update": time.monotonic() - 5.0, "fix": 4,
        }
    else:
        mav.get_gps.side_effect = lambda: {
            "last_update": time.monotonic() - 0.1, "fix": 4,
        }
    return mav


def _make_tracks(*specs) -> TrackingResult:
    tracks = [
        TrackedObject(
            track_id=tid, x1=100, y1=100, x2=200, y2=200,
            confidence=conf, class_id=0, label=label,
        )
        for tid, label, conf in specs
    ]
    return TrackingResult(tracks=tracks, active_ids=len(tracks))


def _make_controller(**overrides) -> AutonomousController:
    defaults = dict(
        enabled=True,
        geofence_lat=35.0527,
        geofence_lon=-79.4927,
        geofence_radius_m=100.0,
        min_confidence=0.80,
        min_track_frames=1,
        allowed_classes=["mine", "buoy", "kayak"],
        strike_cooldown_sec=30.0,
        allowed_vehicle_modes=["AUTO"],
        gps_max_stale_sec=2.0,
        require_operator_lock=False,
    )
    defaults.update(overrides)
    return AutonomousController(**defaults)


def _gates(ctrl: AutonomousController) -> dict:
    """Return gate dict keyed by gate_id from the current snapshot."""
    snap = ctrl.get_dashboard_snapshot(callsign="t")
    return {g["id"]: g for g in snap["gates"]}


# ---------------------------------------------------------------------------
# (a) Inside geofence → geofence=PASS
# ---------------------------------------------------------------------------

class TestGeofencePass:
    def test_inside_geofence_records_pass(self):
        ctrl = _make_controller(require_operator_lock=True)
        mav = _make_mavlink(lat=35.0527, lon=-79.4927)  # at fence center
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.9)), mav,
            MagicMock(return_value=True), MagicMock(return_value=True),
        )
        g = _gates(ctrl)
        assert g["geofence"]["state"] == "PASS"
        # Detail format: "<dist>m of <radius>m"
        assert "of 100m" in g["geofence"]["detail"]


# ---------------------------------------------------------------------------
# (b) Outside geofence → geofence=FAIL with distance detail
# ---------------------------------------------------------------------------

class TestGeofenceFail:
    def test_outside_geofence_records_fail_with_distance(self):
        ctrl = _make_controller(geofence_radius_m=50.0)
        mav = _make_mavlink(lat=36.0, lon=-79.4927)  # ~105 km from center
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.9)), mav,
            MagicMock(), MagicMock(),
        )
        g = _gates(ctrl)
        assert g["geofence"]["state"] == "FAIL"
        assert "of 50m" in g["geofence"]["detail"]
        # All prior gates PASSed; operator_lock was unreachable → N/A
        assert g["operator_lock"]["state"] == "N/A"
        assert g["operator_lock"]["detail"] == "geofence failed"


# ---------------------------------------------------------------------------
# (c) Vehicle mode mismatch → vehicle_mode=FAIL with current mode in detail
# ---------------------------------------------------------------------------

class TestVehicleModeFail:
    def test_wrong_mode_records_fail_with_current_mode(self):
        ctrl = _make_controller(allowed_vehicle_modes=["AUTO"])
        mav = _make_mavlink(mode="LOITER")
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.9)), mav,
            MagicMock(), MagicMock(),
        )
        g = _gates(ctrl)
        assert g["vehicle_mode"]["state"] == "FAIL"
        # Detail must contain current mode name
        assert "LOITER" in g["vehicle_mode"]["detail"]
        # And signal what was needed
        assert "AUTO" in g["vehicle_mode"]["detail"]
        # Gates below vehicle_mode became unreachable → N/A
        assert g["gps_fresh"]["state"] == "N/A"
        assert g["geofence"]["state"] == "N/A"
        assert g["operator_lock"]["state"] == "N/A"


# ---------------------------------------------------------------------------
# (d) Missing operator soft-lock → operator_lock=FAIL
# ---------------------------------------------------------------------------

class TestOperatorLockFail:
    def test_no_soft_lock_records_fail(self):
        ctrl = _make_controller(require_operator_lock=True)
        mav = _make_mavlink(lat=35.0527, lon=-79.4927)
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.9)), mav,
            MagicMock(), MagicMock(),
        )
        g = _gates(ctrl)
        assert g["operator_lock"]["state"] == "FAIL"
        assert g["operator_lock"]["detail"] == "no soft-lock"

    def test_soft_lock_present_records_pass(self):
        ctrl = _make_controller(require_operator_lock=True)
        ctrl._operator_locked_track = 7
        mav = _make_mavlink(lat=35.0527, lon=-79.4927)
        ctrl.evaluate(
            _make_tracks((7, "mine", 0.9)), mav,
            MagicMock(return_value=True), MagicMock(return_value=True),
        )
        g = _gates(ctrl)
        assert g["operator_lock"]["state"] == "PASS"
        assert "track 7" in g["operator_lock"]["detail"]


# ---------------------------------------------------------------------------
# (e) Stale GPS → gps_fresh=FAIL with age detail
# ---------------------------------------------------------------------------

class TestGpsFreshFail:
    def test_stale_gps_records_fail_with_age(self):
        ctrl = _make_controller()
        mav = _make_mavlink(stale_gps=True)
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.9)), mav,
            MagicMock(), MagicMock(),
        )
        g = _gates(ctrl)
        assert g["gps_fresh"]["state"] == "FAIL"
        assert "fix age" in g["gps_fresh"]["detail"]
        assert "s" in g["gps_fresh"]["detail"]
        # Downstream gates unreachable
        assert g["geofence"]["state"] == "N/A"
        assert g["operator_lock"]["state"] == "N/A"


# ---------------------------------------------------------------------------
# (f) Cooldown active → cooldown=FAIL with remaining seconds
# ---------------------------------------------------------------------------

class TestCooldownFail:
    def test_cooldown_active_records_fail_with_remaining(self):
        ctrl = _make_controller(strike_cooldown_sec=100.0)
        mav = _make_mavlink(lat=35.0527, lon=-79.4927)
        strike_cb = MagicMock(return_value=True)
        # First evaluate: all gates pass and strike fires → sets _last_strike_time
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.95)), mav,
            MagicMock(return_value=True), strike_cb,
        )
        assert strike_cb.call_count == 1

        # Second evaluate: within cooldown window → cooldown FAIL
        ctrl.evaluate(
            _make_tracks((2, "buoy", 0.9)), mav,
            MagicMock(), strike_cb,
        )
        g = _gates(ctrl)
        assert g["cooldown"]["state"] == "FAIL"
        assert "remaining" in g["cooldown"]["detail"]
        # strike_cb not called a second time
        assert strike_cb.call_count == 1
        # Gates after cooldown unreachable → N/A
        assert g["vehicle_mode"]["state"] == "N/A"
        assert g["gps_fresh"]["state"] == "N/A"
        assert g["geofence"]["state"] == "N/A"
        assert g["operator_lock"]["state"] == "N/A"


# ---------------------------------------------------------------------------
# (g) Terminal decision recording — engage / reject / defer
# ---------------------------------------------------------------------------

class TestTerminalDecisionLog:
    def test_successful_strike_records_engage(self):
        ctrl = _make_controller()
        mav = _make_mavlink(lat=35.0527, lon=-79.4927)
        strike_cb = MagicMock(return_value=True)
        ctrl.evaluate(
            _make_tracks((7, "mine", 0.95)), mav,
            MagicMock(return_value=True), strike_cb,
        )
        snap = ctrl.get_dashboard_snapshot(callsign="t")
        assert len(snap["log"]) >= 1
        latest = snap["log"][0]
        assert latest["action"] == "engage"
        assert latest["track_id"] == 7
        assert latest["label"] == "mine"
        assert "conf=" in latest["reason"]

    def test_geofence_fail_records_reject(self):
        ctrl = _make_controller(geofence_radius_m=50.0)
        mav = _make_mavlink(lat=36.0, lon=-79.4927)  # outside
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.9)), mav,
            MagicMock(), MagicMock(),
        )
        snap = ctrl.get_dashboard_snapshot(callsign="t")
        assert len(snap["log"]) >= 1
        latest = snap["log"][0]
        assert latest["action"] == "reject"
        assert latest["track_id"] is None
        assert "geofence" in latest["reason"].lower() or "outside" in latest["reason"].lower()

    def test_all_gates_pass_no_track_records_defer(self):
        # Confidence too low to qualify — gates all pass, track rejected
        ctrl = _make_controller(min_confidence=0.99)
        mav = _make_mavlink(lat=35.0527, lon=-79.4927)
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.5)), mav,
            MagicMock(), MagicMock(),
        )
        snap = ctrl.get_dashboard_snapshot(callsign="t")
        assert len(snap["log"]) >= 1
        latest = snap["log"][0]
        assert latest["action"] == "defer"
        assert latest["reason"] == "no qualifying track"
        # All 5 gates should be PASS/N-A/ready — none FAIL
        g = {gate["id"]: gate for gate in snap["gates"]}
        assert g["vehicle_mode"]["state"] == "PASS"
        assert g["geofence"]["state"] == "PASS"


# ---------------------------------------------------------------------------
# Dashboard snapshot shape integration — all-pass engage case
# ---------------------------------------------------------------------------

class TestSnapshotReflectsEvaluation:
    def test_engage_snapshot_shows_all_pass_gates(self):
        ctrl = _make_controller()
        mav = _make_mavlink(lat=35.0527, lon=-79.4927)
        ctrl.evaluate(
            _make_tracks((1, "mine", 0.95)), mav,
            MagicMock(return_value=True), MagicMock(return_value=True),
        )
        snap = ctrl.get_dashboard_snapshot(callsign="HYDRA-1")
        # self_position populated from evaluate
        assert snap["self_position"] is not None
        assert snap["self_position"]["lat"] == pytest.approx(35.0527)
        # engage logged
        assert snap["log"][0]["action"] == "engage"
        # no gate is FAIL
        states = {g["id"]: g["state"] for g in snap["gates"]}
        assert "FAIL" not in states.values()
