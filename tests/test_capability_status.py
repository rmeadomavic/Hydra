"""Tests for capability_status module — evaluators, registry, API endpoint."""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level imports — fail fast if the module is absent
# ---------------------------------------------------------------------------

from hydra_detect.capability_status import (
    CapabilityStatus,
    CapabilityReport,
    SystemState,
    evaluate_all,
    CAPABILITY_NAMES,
)

# server.py uses fcntl (Linux-only) — skip those tests on Windows
_SKIP_SERVER = sys.platform == "win32"
_skip_server_reason = "server.py uses fcntl (Linux only)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> SystemState:
    """Return a fully-green SystemState with selected fields overridden."""
    defaults = dict(
        camera_ok=True,
        camera_frame_age_sec=0.5,
        mavlink_connected=True,
        mavlink_last_heartbeat_age_sec=0.8,
        gps_fix=3,
        tak_output_enabled=True,
        tak_output_running=True,
        tak_allowed_callsigns_set=True,
        tak_hmac_secret_set=True,
        disk_free_gb=10.0,
        disk_output_dir="/tmp",
        time_source="RTC",
        vehicle_profile="drone",
        vehicle_profile_present=True,
        schema_version="1",
        schema_version_present=True,
        cpu_temp_c=50.0,
        gpu_temp_c=55.0,
        fps_below_target_sustained_sec=0.0,
        cfg=None,
    )
    defaults.update(overrides)
    return SystemState(**defaults)


# ---------------------------------------------------------------------------
# CapabilityStatus enum
# ---------------------------------------------------------------------------

class TestCapabilityStatus:
    def test_values_exist(self):
        assert CapabilityStatus.READY
        assert CapabilityStatus.WARN
        assert CapabilityStatus.BLOCKED
        assert CapabilityStatus.ARMED

    def test_string_representation(self):
        assert str(CapabilityStatus.READY) in ("READY", "CapabilityStatus.READY")


# ---------------------------------------------------------------------------
# CapabilityReport dataclass
# ---------------------------------------------------------------------------

class TestCapabilityReport:
    def test_construction(self):
        r = CapabilityReport(
            name="Detection",
            status=CapabilityStatus.READY,
            reasons=[],
            fix_target=None,
        )
        assert r.name == "Detection"
        assert r.status == CapabilityStatus.READY
        assert r.reasons == []
        assert r.fix_target is None

    def test_with_reasons_and_fix(self):
        r = CapabilityReport(
            name="GPS",
            status=CapabilityStatus.BLOCKED,
            reasons=["GPS fix missing. Required: 3D+ fix."],
            fix_target="#155",
        )
        assert len(r.reasons) == 1
        assert r.fix_target == "#155"


# ---------------------------------------------------------------------------
# Registry — all expected capabilities present
# ---------------------------------------------------------------------------

EXPECTED_CAPABILITIES = [
    "Detection",
    "MAVLink",
    "GPS",
    "TAK Output",
    "TAK Commands",
    "Disk",
    "Time Source",
    "Vehicle Profile",
    "Schema Version",
    "Thermal",
    "Performance",
    "Autonomy Live",
    "Drop",
    "RF Hunt",
]


class TestRegistry:
    def test_all_expected_capabilities_present(self):
        assert set(EXPECTED_CAPABILITIES).issubset(set(CAPABILITY_NAMES))

    def test_evaluate_all_returns_all_capabilities(self):
        state = _make_state()
        reports = evaluate_all(state)
        names = [r.name for r in reports]
        for cap in EXPECTED_CAPABILITIES:
            assert cap in names, f"Missing capability: {cap}"

    def test_evaluate_all_returns_capability_report_instances(self):
        state = _make_state()
        reports = evaluate_all(state)
        for r in reports:
            assert isinstance(r, CapabilityReport)

    def test_no_duplicate_names(self):
        state = _make_state()
        reports = evaluate_all(state)
        names = [r.name for r in reports]
        assert len(names) == len(set(names)), "Duplicate capability names found"


# ---------------------------------------------------------------------------
# Evaluator precedence: WARN overrides READY, BLOCKED overrides WARN
# ---------------------------------------------------------------------------

class TestEvaluatorPrecedence:
    def test_blocked_overrides_warn(self):
        """A capability that has both a warn and blocked condition returns BLOCKED."""
        # GPS with fix=1 (warn) and no mavlink (can't verify — but we test GPS evaluator
        # by using fix=1 which should be BLOCKED as < 3D)
        state = _make_state(gps_fix=1)
        reports = evaluate_all(state)
        gps = next(r for r in reports if r.name == "GPS")
        assert gps.status == CapabilityStatus.BLOCKED

    def test_warn_overrides_ready(self):
        """A capability with a warn-level signal returns WARN, not READY."""
        # Disk at 1.5 GB — WARN threshold, not BLOCKED
        state = _make_state(disk_free_gb=1.5)
        reports = evaluate_all(state)
        disk = next(r for r in reports if r.name == "Disk")
        assert disk.status == CapabilityStatus.WARN


# ---------------------------------------------------------------------------
# Detection evaluator
# ---------------------------------------------------------------------------

class TestDetectionEvaluator:
    def test_ready_when_camera_ok_and_recent_frame(self):
        state = _make_state(camera_ok=True, camera_frame_age_sec=0.5)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Detection")
        assert r.status == CapabilityStatus.READY
        assert r.reasons == []

    def test_blocked_when_camera_not_ok(self):
        state = _make_state(camera_ok=False, camera_frame_age_sec=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Detection")
        assert r.status == CapabilityStatus.BLOCKED
        assert len(r.reasons) > 0

    def test_warn_when_frame_stale(self):
        # Camera reports ok but last frame is old
        state = _make_state(camera_ok=True, camera_frame_age_sec=8.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Detection")
        assert r.status in (CapabilityStatus.WARN, CapabilityStatus.BLOCKED)


# ---------------------------------------------------------------------------
# MAVLink evaluator
# ---------------------------------------------------------------------------

class TestMAVLinkEvaluator:
    def test_ready_when_connected_and_recent_heartbeat(self):
        state = _make_state(mavlink_connected=True, mavlink_last_heartbeat_age_sec=0.8)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "MAVLink")
        assert r.status == CapabilityStatus.READY
        assert r.reasons == []

    def test_blocked_when_not_connected(self):
        state = _make_state(mavlink_connected=False, mavlink_last_heartbeat_age_sec=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "MAVLink")
        assert r.status == CapabilityStatus.BLOCKED
        assert len(r.reasons) > 0

    def test_warn_when_heartbeat_stale(self):
        state = _make_state(mavlink_connected=True, mavlink_last_heartbeat_age_sec=7.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "MAVLink")
        assert r.status in (CapabilityStatus.WARN, CapabilityStatus.BLOCKED)


# ---------------------------------------------------------------------------
# GPS evaluator
# ---------------------------------------------------------------------------

class TestGPSEvaluator:
    def test_ready_when_3d_fix(self):
        state = _make_state(gps_fix=3)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "GPS")
        assert r.status == CapabilityStatus.READY

    def test_blocked_when_no_fix(self):
        state = _make_state(gps_fix=0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "GPS")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("fix" in reason.lower() for reason in r.reasons)

    def test_warn_when_2d_fix(self):
        state = _make_state(gps_fix=2)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "GPS")
        assert r.status == CapabilityStatus.WARN

    def test_blocked_when_no_mavlink(self):
        state = _make_state(mavlink_connected=False, gps_fix=0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "GPS")
        assert r.status == CapabilityStatus.BLOCKED


# ---------------------------------------------------------------------------
# TAK Output evaluator
# ---------------------------------------------------------------------------

class TestTAKOutputEvaluator:
    def test_ready_when_enabled_and_running(self):
        state = _make_state(tak_output_enabled=True, tak_output_running=True)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "TAK Output")
        assert r.status == CapabilityStatus.READY

    def test_blocked_when_disabled(self):
        state = _make_state(tak_output_enabled=False, tak_output_running=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "TAK Output")
        assert r.status == CapabilityStatus.BLOCKED
        assert len(r.reasons) > 0

    def test_warn_when_enabled_but_not_running(self):
        state = _make_state(tak_output_enabled=True, tak_output_running=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "TAK Output")
        assert r.status in (CapabilityStatus.WARN, CapabilityStatus.BLOCKED)


# ---------------------------------------------------------------------------
# TAK Commands evaluator
# ---------------------------------------------------------------------------

class TestTAKCommandsEvaluator:
    def test_ready_when_callsigns_and_hmac_set(self):
        state = _make_state(tak_allowed_callsigns_set=True, tak_hmac_secret_set=True)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "TAK Commands")
        assert r.status == CapabilityStatus.READY

    def test_blocked_when_no_callsigns(self):
        state = _make_state(tak_allowed_callsigns_set=False, tak_hmac_secret_set=True)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "TAK Commands")
        assert r.status == CapabilityStatus.BLOCKED

    def test_warn_when_callsigns_set_but_no_hmac(self):
        state = _make_state(tak_allowed_callsigns_set=True, tak_hmac_secret_set=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "TAK Commands")
        # No HMAC means commands are spoofable — at least WARN
        assert r.status in (CapabilityStatus.WARN, CapabilityStatus.BLOCKED)


# ---------------------------------------------------------------------------
# Disk evaluator
# ---------------------------------------------------------------------------

class TestDiskEvaluator:
    def test_ready_when_plenty_of_space(self):
        state = _make_state(disk_free_gb=10.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Disk")
        assert r.status == CapabilityStatus.READY
        assert r.reasons == []

    def test_warn_when_low_space(self):
        state = _make_state(disk_free_gb=1.5)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Disk")
        assert r.status == CapabilityStatus.WARN
        assert len(r.reasons) > 0

    def test_blocked_when_critically_low(self):
        state = _make_state(disk_free_gb=0.3)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Disk")
        assert r.status == CapabilityStatus.BLOCKED

    def test_missing_signal(self):
        state = _make_state(disk_free_gb=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Disk")
        assert r.status in (CapabilityStatus.WARN, CapabilityStatus.BLOCKED)


# ---------------------------------------------------------------------------
# Time Source evaluator (stub — #155 will wire real NTP/PPS)
# ---------------------------------------------------------------------------

class TestTimeSourceEvaluator:
    def test_returns_report(self):
        state = _make_state(time_source="RTC")
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Time Source")
        assert isinstance(r, CapabilityReport)

    def test_ready_with_rtc(self):
        state = _make_state(time_source="RTC")
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Time Source")
        # RTC stub is at minimum WARN (no GPS-discipline) but returns a valid report
        assert r.status in (CapabilityStatus.READY, CapabilityStatus.WARN)

    def test_fix_target_points_to_issue(self):
        state = _make_state(time_source="RTC")
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Time Source")
        # Stub should reference the wiring issue
        assert r.fix_target is not None


# ---------------------------------------------------------------------------
# Vehicle Profile evaluator
# ---------------------------------------------------------------------------

class TestVehicleProfileEvaluator:
    def test_ready_when_profile_present(self):
        state = _make_state(vehicle_profile="drone", vehicle_profile_present=True)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Vehicle Profile")
        assert r.status == CapabilityStatus.READY

    def test_blocked_when_no_profile(self):
        state = _make_state(vehicle_profile="", vehicle_profile_present=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Vehicle Profile")
        assert r.status == CapabilityStatus.BLOCKED
        assert len(r.reasons) > 0

    def test_fix_target_references_issue(self):
        state = _make_state(vehicle_profile="", vehicle_profile_present=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Vehicle Profile")
        assert r.fix_target is not None


# ---------------------------------------------------------------------------
# Schema Version evaluator
# ---------------------------------------------------------------------------

class TestSchemaVersionEvaluator:
    def test_ready_when_version_present(self):
        state = _make_state(schema_version="1", schema_version_present=True)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Schema Version")
        assert r.status == CapabilityStatus.READY

    def test_blocked_when_no_version(self):
        state = _make_state(schema_version=None, schema_version_present=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Schema Version")
        assert r.status == CapabilityStatus.BLOCKED
        assert len(r.reasons) > 0


# ---------------------------------------------------------------------------
# Thermal evaluator (SoC temp from /sys/devices/virtual/thermal)
# ---------------------------------------------------------------------------

class TestThermalEvaluator:
    """Operator-visible WARN/BLOCKED gating on Jetson SoC temperature.

    Threshold rationale: Orin Nano begins thermal throttling around 85-92 °C
    depending on the rail. WARN at 75 °C gives the operator margin to land
    or shade the unit before performance degrades; BLOCKED at 90 °C is the
    'stop using this unit' line.
    """

    def test_ready_when_temps_unavailable(self):
        """No Jetson hardware (dev box, sim) — return READY rather than warn."""
        state = _make_state(cpu_temp_c=None, gpu_temp_c=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.READY

    def test_ready_when_cool(self):
        state = _make_state(cpu_temp_c=45.0, gpu_temp_c=50.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.READY
        assert r.reasons == []

    def test_warn_when_cpu_above_threshold(self):
        state = _make_state(cpu_temp_c=80.0, gpu_temp_c=50.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.WARN
        assert any("80" in reason or "75" in reason for reason in r.reasons)

    def test_warn_when_gpu_above_threshold(self):
        state = _make_state(cpu_temp_c=60.0, gpu_temp_c=82.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.WARN

    def test_warn_uses_hottest_zone(self):
        """If only one zone is hot, the report still warns (max wins)."""
        state = _make_state(cpu_temp_c=30.0, gpu_temp_c=78.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.WARN

    def test_blocked_when_at_throttle_limit(self):
        state = _make_state(cpu_temp_c=92.0, gpu_temp_c=88.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.BLOCKED

    def test_partial_data_one_zone_only(self):
        """One zone reporting None should not crash the evaluator."""
        state = _make_state(cpu_temp_c=80.0, gpu_temp_c=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.WARN

    def test_at_warn_boundary_is_warn(self):
        """75.0 °C exactly is the warn threshold — strictly-above triggers."""
        # Slightly above to avoid floating-point ambiguity.
        state = _make_state(cpu_temp_c=75.1, gpu_temp_c=50.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Thermal")
        assert r.status == CapabilityStatus.WARN


# ---------------------------------------------------------------------------
# Performance evaluator + sustained-FPS tracker
# ---------------------------------------------------------------------------

class TestSustainedFpsTracker:
    """Singleton tracker that records how long FPS has been below the
    Hydra-documented 5 FPS minimum on Jetson."""

    def setup_method(self):
        from hydra_detect.capability_status import reset_fps_tracker_for_test
        reset_fps_tracker_for_test()

    def test_zero_when_fps_above_threshold(self):
        from hydra_detect.capability_status import record_fps, sustained_fps_below_sec
        record_fps(10.0, now_s=0.0)
        record_fps(10.0, now_s=5.0)
        assert sustained_fps_below_sec(now_s=10.0) == 0.0

    def test_zero_when_fps_unknown(self):
        from hydra_detect.capability_status import record_fps, sustained_fps_below_sec
        record_fps(None, now_s=0.0)
        assert sustained_fps_below_sec(now_s=10.0) == 0.0

    def test_counts_from_first_below_sample(self):
        from hydra_detect.capability_status import record_fps, sustained_fps_below_sec
        record_fps(10.0, now_s=0.0)
        record_fps(3.0, now_s=10.0)
        record_fps(2.5, now_s=20.0)
        assert sustained_fps_below_sec(now_s=40.0) == pytest.approx(30.0, abs=0.01)

    def test_resets_when_fps_recovers(self):
        from hydra_detect.capability_status import record_fps, sustained_fps_below_sec
        record_fps(3.0, now_s=0.0)
        record_fps(3.0, now_s=20.0)
        assert sustained_fps_below_sec(now_s=20.0) == pytest.approx(20.0, abs=0.01)
        record_fps(10.0, now_s=21.0)  # recovered
        assert sustained_fps_below_sec(now_s=22.0) == 0.0

    def test_unknown_fps_does_not_reset_sustained(self):
        """A None FPS sample (e.g. stats not yet populated on a poll) must
        not zero out the sustained-below counter — that would mask a real
        thermal throttling event."""
        from hydra_detect.capability_status import record_fps, sustained_fps_below_sec
        record_fps(3.0, now_s=0.0)
        record_fps(None, now_s=10.0)  # pipeline transient
        assert sustained_fps_below_sec(now_s=15.0) == pytest.approx(15.0, abs=0.01)


class TestPerformanceEvaluator:
    """READY when FPS healthy, WARN when sustained-below window exceeded."""

    def test_ready_when_no_sustained_below(self):
        state = _make_state(fps_below_target_sustained_sec=0.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Performance")
        assert r.status == CapabilityStatus.READY
        assert r.reasons == []

    def test_ready_when_below_window(self):
        # 20 s below target — under the 30 s window, still READY.
        state = _make_state(fps_below_target_sustained_sec=20.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Performance")
        assert r.status == CapabilityStatus.READY

    def test_warn_when_at_window(self):
        state = _make_state(fps_below_target_sustained_sec=30.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Performance")
        assert r.status == CapabilityStatus.WARN
        assert any("30" in reason or "thermal" in reason.lower()
                   for reason in r.reasons)

    def test_warn_when_well_past_window(self):
        state = _make_state(fps_below_target_sustained_sec=120.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Performance")
        assert r.status == CapabilityStatus.WARN


# ---------------------------------------------------------------------------
# Placeholder capabilities — Autonomy Live, Drop, RF Hunt
# ---------------------------------------------------------------------------

class TestPlaceholderCapabilities:
    def test_autonomy_live_is_blocked_placeholder(self):
        state = _make_state()
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Live")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("#147" in reason for reason in r.reasons)

    def test_drop_is_blocked_placeholder(self):
        state = _make_state()
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Drop")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("#147" in reason for reason in r.reasons)

    def test_rf_hunt_is_blocked_placeholder(self):
        state = _make_state()
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "RF Hunt")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("#147" in reason for reason in r.reasons)


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_SKIP_SERVER, reason=_skip_server_reason)
class TestCapabilityAPI:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from hydra_detect.web.server import app
        return TestClient(app)

    def test_endpoint_returns_200(self, client):
        resp = client.get("/api/capabilities")
        assert resp.status_code == 200

    def test_response_has_expected_keys(self, client):
        resp = client.get("/api/capabilities")
        data = resp.json()
        assert "generated_at" in data
        assert "capabilities" in data

    def test_capabilities_list_has_items(self, client):
        resp = client.get("/api/capabilities")
        data = resp.json()
        assert len(data["capabilities"]) > 0

    def test_each_capability_has_required_fields(self, client):
        resp = client.get("/api/capabilities")
        data = resp.json()
        for cap in data["capabilities"]:
            assert "name" in cap
            assert "status" in cap
            assert "reasons" in cap
            assert "fix_target" in cap

    def test_status_values_are_valid(self, client):
        valid = {"READY", "WARN", "BLOCKED", "ARMED"}
        resp = client.get("/api/capabilities")
        data = resp.json()
        for cap in data["capabilities"]:
            assert cap["status"] in valid, f"Invalid status: {cap['status']}"

    def test_generated_at_is_iso8601(self, client):
        import datetime
        resp = client.get("/api/capabilities")
        data = resp.json()
        # Should parse without raising
        datetime.datetime.fromisoformat(data["generated_at"])

    def test_ttl_caching(self, client):
        """Two rapid requests return the same generated_at timestamp (cached)."""
        r1 = client.get("/api/capabilities").json()
        r2 = client.get("/api/capabilities").json()
        assert r1["generated_at"] == r2["generated_at"]

    def test_capabilities_page_route_returns_html(self, client):
        resp = client.get("/capabilities")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Standalone capability_api router tests (no fcntl dependency)
# ---------------------------------------------------------------------------

class TestCapabilityAPIRouter:
    """Test the capability_api router directly without importing full server.py."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from hydra_detect.web.capability_api import router, reset_cache

        app = FastAPI()
        app.include_router(router)
        reset_cache()
        return TestClient(app)

    def test_endpoint_returns_200(self, client):
        resp = client.get("/api/capabilities")
        assert resp.status_code == 200

    def test_response_has_expected_keys(self, client):
        resp = client.get("/api/capabilities")
        data = resp.json()
        assert "generated_at" in data
        assert "capabilities" in data

    def test_capabilities_list_has_items(self, client):
        resp = client.get("/api/capabilities")
        data = resp.json()
        assert len(data["capabilities"]) > 0

    def test_each_capability_has_required_fields(self, client):
        resp = client.get("/api/capabilities")
        data = resp.json()
        for cap in data["capabilities"]:
            assert "name" in cap
            assert "status" in cap
            assert "reasons" in cap
            assert "fix_target" in cap

    def test_status_values_are_valid(self, client):
        valid = {"READY", "WARN", "BLOCKED", "ARMED"}
        resp = client.get("/api/capabilities")
        data = resp.json()
        for cap in data["capabilities"]:
            assert cap["status"] in valid, f"Invalid status: {cap['status']}"

    def test_generated_at_is_iso8601(self, client):
        import datetime
        resp = client.get("/api/capabilities")
        data = resp.json()
        datetime.datetime.fromisoformat(data["generated_at"])

    def test_ttl_caching(self, client):
        """Two rapid requests return the same generated_at timestamp (cached)."""
        r1 = client.get("/api/capabilities").json()
        r2 = client.get("/api/capabilities").json()
        assert r1["generated_at"] == r2["generated_at"]
