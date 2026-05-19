"""Tests for capability_status module — evaluators, registry, API endpoint."""

from __future__ import annotations

import sys

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
        servo_enabled=True,
        servo_locked_track_id=42,
        autonomy_mode="dryrun",
        autonomy_enabled=True,
        autonomy_geofence_present=True,
        operating_mode="ARMED",
        identity_callsign="HYDRA-01-DRONE",
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
    "Servo Tracking",
    "Follow",
    "Autonomy Dryrun",
    "Autonomy Shadow",
    "Autonomy Live",
    "Drop",
    "RF Hunt",
    "Log Export",
    "Fleet View",
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
# Disk evaluator — platform-aware pct + absolute floor gates (#226)
# ---------------------------------------------------------------------------

class TestDiskPlatformAwareGates:
    """Issue #226 — pct alone is not enough on heterogeneous storage.

    5 % of a 32 GB SD card (1.6 GB) and 5 % of a 4 TB NVMe (200 GB) both
    look identical on percent telemetry but only one of them is about to
    actually run out of room. BLOCKED requires BOTH pct AND absolute floor
    to trip; WARN trips on pct alone (dashboard banner is informational).
    """

    def _state(self, free_gb, total_gb, **kw):
        free_pct = (free_gb / total_gb) * 100.0 if total_gb else None
        return _make_state(
            disk_free_gb=free_gb,
            disk_total_gb=total_gb,
            disk_free_pct=free_pct,
            **kw,
        )

    def test_ready_at_50_pct_large_disk(self):
        # 50 % of 4 TB = 2 TB free — fully READY
        state = self._state(free_gb=2000.0, total_gb=4000.0)
        r = next(r for r in evaluate_all(state) if r.name == "Disk")
        assert r.status == CapabilityStatus.READY

    def test_warn_at_10_pct_pct_alone(self):
        # 10 % of 4 TB = 400 GB free — below warn pct (15), above block floor
        state = self._state(free_gb=400.0, total_gb=4000.0)
        r = next(r for r in evaluate_all(state) if r.name == "Disk")
        assert r.status == CapabilityStatus.WARN

    def test_4pct_large_disk_not_blocked_pct_alone(self):
        # 4 % of 4 TB = 160 GB free — below BLOCKED pct but above abs floor.
        # MUST NOT block: platform has 160 GB of operational headroom.
        state = self._state(free_gb=160.0, total_gb=4000.0)
        r = next(r for r in evaluate_all(state) if r.name == "Disk")
        assert r.status == CapabilityStatus.WARN

    def test_4pct_small_disk_blocked(self):
        # 4 % of 32 GB = 1.28 GB free — both pct AND floor tripped.
        state = self._state(free_gb=1.28, total_gb=32.0)
        r = next(r for r in evaluate_all(state) if r.name == "Disk")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("Refusing new mission bundles" in s for s in r.reasons)

    def test_recovery_ladder_blocked_to_warn_to_ready(self):
        """The full READY -> WARN -> BLOCKED -> WARN -> READY ladder.

        Pinned scenario from issue #226 acceptance:
        - 4 %, 1.28 GB free on a 32 GB SD card -> BLOCKED
        - cleanup brings the unit to 20 % (6.4 GB) -> READY (above 15 % warn)
        - the intermediate WARN window at 10 % (3.2 GB) was observed too
        """
        # Start BLOCKED
        s_blocked = self._state(free_gb=1.28, total_gb=32.0)
        assert next(
            r for r in evaluate_all(s_blocked) if r.name == "Disk"
        ).status == CapabilityStatus.BLOCKED
        # Operator runs cleanup — disk recovers into WARN window (10 %).
        s_warn = self._state(free_gb=3.2, total_gb=32.0)
        assert next(
            r for r in evaluate_all(s_warn) if r.name == "Disk"
        ).status == CapabilityStatus.WARN
        # More cleanup — disk fully recovered into READY (20 %).
        s_ready = self._state(free_gb=6.4, total_gb=32.0)
        assert next(
            r for r in evaluate_all(s_ready) if r.name == "Disk"
        ).status == CapabilityStatus.READY

    def test_custom_thresholds_via_state(self):
        # Operator sets a strict 25 % WARN — 20 % free should already WARN.
        state = self._state(
            free_gb=6.4, total_gb=32.0,
            disk_warn_pct=25.0, disk_blocked_pct=5.0,
            disk_blocked_min_free_gb=5.0,
        )
        r = next(r for r in evaluate_all(state) if r.name == "Disk")
        assert r.status == CapabilityStatus.WARN

    def test_blocked_requires_both_pct_and_floor(self):
        # Disk at 3 % pct (below blocked_pct) but free_gb of 10 GB (above
        # absolute floor of 5 GB) — must NOT block. Platform-aware gate.
        state = self._state(free_gb=10.0, total_gb=400.0)
        r = next(r for r in evaluate_all(state) if r.name == "Disk")
        assert r.status == CapabilityStatus.WARN
        # And the inverse: free_gb=2 (below floor) but pct=20 (above warn) —
        # not BLOCKED because pct did not trip.
        state = self._state(free_gb=2.0, total_gb=10.0)
        r = next(r for r in evaluate_all(state) if r.name == "Disk")
        # 20 % free, but only 2 GB absolute. Pct says READY, floor would
        # block only if pct also fell. Must be READY in the pct-only path.
        assert r.status == CapabilityStatus.READY


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

    def test_public_reset_clears_sustained_window(self):
        """Closes adversarial findings R3-2 + R3-8 from PR #183.

        Production callers (model swap, restart-command handler) call
        ``reset_fps_tracker()`` to clear the window so a legitimate
        detector pause (model load, pipeline restart) does not accumulate
        into a 30 s false WARN labelled as thermal throttling.
        """
        from hydra_detect.capability_status import (
            record_fps, sustained_fps_below_sec, reset_fps_tracker,
        )
        # Drive the tracker into a deep below-threshold state.
        record_fps(2.0, now_s=0.0)
        record_fps(2.0, now_s=45.0)
        assert sustained_fps_below_sec(now_s=45.0) == pytest.approx(45.0, abs=0.01)

        # Production reset (e.g. _handle_model_switch success path).
        reset_fps_tracker()
        assert sustained_fps_below_sec(now_s=46.0) == 0.0

        # Subsequent samples re-arm the window from the new now_s anchor —
        # no leakage of pre-reset state.
        record_fps(2.0, now_s=50.0)
        assert sustained_fps_below_sec(now_s=55.0) == pytest.approx(5.0, abs=0.01)

    def test_build_system_state_does_not_advance_tracker(self):
        """Regression test for PR #183 Codex review: build_system_state must
        be a pure reader of the FPS tracker. Re-feeding stream_state's cached
        "fps" on every readiness poll would let a stalled pipeline's last
        good value masquerade as a fresh sample and reset the sustained
        counter, defeating the very signal this evaluator is meant to catch.
        """
        from hydra_detect.capability_status import (
            record_fps, sustained_fps_below_sec, build_system_state,
        )

        # Pipeline reports a healthy FPS, then stalls (no more record_fps
        # calls). The cached fps value in stream_state.get_stats() does
        # not change.
        record_fps(10.0, now_s=0.0)

        class FakeStreamState:
            def get_stats(self):
                # Stale "healthy" reading — pipeline has actually stopped
                # producing frames but no one updated this dict.
                return {"camera_ok": True, "fps": 10.0, "last_frame_ts": 0.0}

        # Operator polls readiness many times. Each poll calls
        # build_system_state with the stale stats dict.
        for poll_t in range(1, 60):
            build_system_state(stream_state=FakeStreamState())

        # FPS has never gone below threshold, so sustained must be 0.0 —
        # but the bug would make it appear that FPS is healthy even after
        # a stall because polls keep pushing the cached 10.0 back in.
        # We verify the tracker state directly: no one has fed it a below-
        # threshold sample, so it should still be at zero. The stalled-
        # high case is symmetric — see test_stalled_high_fps_does_not_mask_real_drop.
        assert sustained_fps_below_sec(now_s=60.0) == 0.0

    def test_stalled_high_fps_does_not_mask_real_drop(self):
        """Variant of the Codex regression scenario where the pipeline drops
        below threshold and stalls. The readiness poll must NOT be able to
        reset the sustained counter by re-pushing a now-stale healthy value.
        """
        from hydra_detect.capability_status import (
            record_fps, sustained_fps_below_sec, build_system_state,
        )

        # Pipeline reports healthy, then degrades, then stalls entirely.
        record_fps(10.0, now_s=0.0)
        record_fps(2.0, now_s=10.0)  # below threshold from t=10 onward
        # Pipeline stops at t=10 — no more record_fps calls.

        class StalePostDegradationStream:
            def get_stats(self):
                # Cached value still shows the LAST below-threshold reading,
                # but operator polls keep re-firing on cached state.
                return {"fps": 2.0, "camera_ok": True, "last_frame_ts": 10.0}

        # Operator polls many times — none of these should advance _below_since
        # forward (which would shorten the apparent sustained-below window),
        # nor should any push of a stale value prior to t=10 reset to None.
        for poll_t in range(11, 50):
            build_system_state(stream_state=StalePostDegradationStream())

        # 40 s of below-threshold time elapsed since t=10. The tracker should
        # see the full window because only the pipeline's t=10 record_fps
        # call drove _below_since, and nothing has reset it since.
        assert sustained_fps_below_sec(now_s=50.0) == pytest.approx(40.0, abs=0.01)


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

    # ── R3-4 branch-coverage: reason text branches on SoC temp ────────────
    #
    # Closes adversarial finding R3-4 from PR #183. The prior unconditional
    # "Likely thermal throttling or detector overload" misdirected operators
    # on three legitimate config combinations (heavy model + 8GB Jetson,
    # marine telephoto USV, low-confidence wide-class surveillance). The
    # fix branches on whether observed SoC temp is within
    # _PERF_THERMAL_HINT_C of the thermal WARN threshold.

    def test_warn_text_cites_thermal_when_soc_hot(self):
        # CPU at 71 C is within 5 C of the 75 C thermal WARN threshold.
        state = _make_state(
            fps_below_target_sustained_sec=45.0,
            cpu_temp_c=71.0,
            gpu_temp_c=68.0,
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Performance")
        assert r.status == CapabilityStatus.WARN
        text = " ".join(r.reasons).lower()
        assert "thermal throttling" in text
        assert "config under-provisioning" not in text
        assert "active profile may be heavier" not in text

    def test_warn_text_cites_config_when_soc_benign(self):
        # CPU at 55 C and GPU at 50 C are both well below the thermal hint
        # threshold (75 - 5 == 70 C). Reason text must point operator at
        # config, not thermal.
        state = _make_state(
            fps_below_target_sustained_sec=45.0,
            cpu_temp_c=55.0,
            gpu_temp_c=50.0,
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Performance")
        assert r.status == CapabilityStatus.WARN
        text = " ".join(r.reasons).lower()
        assert "thermal cause unlikely" in text
        assert "active profile may be heavier" in text
        # The operator should NOT be told to land/shade when SoC is benign.
        assert "land" not in text
        assert "shade" not in text

    def test_warn_text_handles_missing_temps(self):
        # Dev box / SITL host with no thermal sensors. Cannot rule out
        # thermal cause, but cannot point at it confidently either.
        state = _make_state(
            fps_below_target_sustained_sec=45.0,
            cpu_temp_c=None,
            gpu_temp_c=None,
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Performance")
        assert r.status == CapabilityStatus.WARN
        text = " ".join(r.reasons).lower()
        assert "soc temperature unavailable" in text
        assert "active profile" in text


# ---------------------------------------------------------------------------
# Follow evaluator — GPS + MAVLink + servo lock
# ---------------------------------------------------------------------------

class TestFollowEvaluator:
    def test_ready_with_full_stack(self):
        state = _make_state(
            mavlink_connected=True, gps_fix=3, servo_locked_track_id=7,
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Follow")
        assert r.status == CapabilityStatus.READY

    def test_blocked_without_mavlink(self):
        state = _make_state(mavlink_connected=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Follow")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("MAVLink" in reason for reason in r.reasons)

    def test_blocked_without_gps(self):
        state = _make_state(gps_fix=1)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Follow")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("GPS" in reason or "fix" in reason for reason in r.reasons)

    def test_warn_without_lock(self):
        state = _make_state(servo_locked_track_id=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Follow")
        assert r.status == CapabilityStatus.WARN
        assert any("lock" in reason.lower() for reason in r.reasons)


# ---------------------------------------------------------------------------
# Servo Tracking evaluator
# ---------------------------------------------------------------------------

class TestServoTrackingEvaluator:
    def test_ready_when_enabled_and_locked(self):
        state = _make_state(servo_enabled=True, servo_locked_track_id=3)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Servo Tracking")
        assert r.status == CapabilityStatus.READY

    def test_blocked_when_disabled(self):
        state = _make_state(servo_enabled=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Servo Tracking")
        assert r.status == CapabilityStatus.BLOCKED
        assert len(r.reasons) > 0

    def test_warn_when_enabled_but_scanning(self):
        state = _make_state(servo_enabled=True, servo_locked_track_id=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Servo Tracking")
        assert r.status == CapabilityStatus.WARN


# ---------------------------------------------------------------------------
# Autonomy Dryrun + Shadow evaluators
# ---------------------------------------------------------------------------

class TestAutonomyDryrunEvaluator:
    def test_ready_with_mavlink_and_geofence(self):
        state = _make_state(
            mavlink_connected=True, autonomy_geofence_present=True,
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Dryrun")
        assert r.status == CapabilityStatus.READY

    def test_blocked_without_mavlink(self):
        state = _make_state(mavlink_connected=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Dryrun")
        assert r.status == CapabilityStatus.BLOCKED

    def test_blocked_without_geofence(self):
        state = _make_state(autonomy_geofence_present=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Dryrun")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("geofence" in reason.lower() for reason in r.reasons)


class TestAutonomyShadowEvaluator:
    def test_ready_with_mavlink_geofence_servo(self):
        state = _make_state(
            mavlink_connected=True,
            autonomy_geofence_present=True,
            servo_enabled=True,
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Shadow")
        assert r.status == CapabilityStatus.READY

    def test_blocked_without_geofence(self):
        state = _make_state(autonomy_geofence_present=False, servo_enabled=True)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Shadow")
        assert r.status == CapabilityStatus.BLOCKED

    def test_blocked_without_servo(self):
        """Shadow without servo is indistinguishable from Dryrun."""
        state = _make_state(
            mavlink_connected=True,
            autonomy_geofence_present=True,
            servo_enabled=False,
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Shadow")
        assert r.status == CapabilityStatus.BLOCKED
        assert r.fix_target == "Servo Tracking"


# ---------------------------------------------------------------------------
# Autonomy Live evaluator — full gating chain
# ---------------------------------------------------------------------------

class TestAutonomyLiveEvaluator:
    def test_ready_with_full_stack_and_armed(self):
        state = _make_state(
            mavlink_connected=True,
            gps_fix=3,
            autonomy_geofence_present=True,
            autonomy_enabled=True,
            operating_mode="ARMED",
        )
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Live")
        assert r.status == CapabilityStatus.READY

    def test_blocked_without_armed(self):
        state = _make_state(operating_mode="OBSERVE")
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Live")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("ARMED" in reason for reason in r.reasons)

    def test_blocked_without_autonomy_enabled(self):
        state = _make_state(autonomy_enabled=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Live")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("autonomy" in reason.lower() for reason in r.reasons)

    def test_blocked_without_geofence(self):
        state = _make_state(autonomy_geofence_present=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Live")
        assert r.status == CapabilityStatus.BLOCKED

    def test_blocked_without_gps(self):
        state = _make_state(gps_fix=2)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Live")
        assert r.status == CapabilityStatus.BLOCKED

    def test_blocked_without_mavlink(self):
        state = _make_state(mavlink_connected=False)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Autonomy Live")
        assert r.status == CapabilityStatus.BLOCKED


# ---------------------------------------------------------------------------
# Log Export evaluator — disk + output dir
# ---------------------------------------------------------------------------

class TestLogExportEvaluator:
    def test_ready_when_disk_healthy(self):
        state = _make_state(disk_free_gb=20.0)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Log Export")
        assert r.status == CapabilityStatus.READY

    def test_warn_when_disk_low(self):
        state = _make_state(disk_free_gb=1.5)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Log Export")
        assert r.status == CapabilityStatus.WARN

    def test_blocked_when_disk_critical(self):
        state = _make_state(disk_free_gb=0.2)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Log Export")
        assert r.status == CapabilityStatus.BLOCKED

    def test_blocked_when_disk_unreadable(self):
        state = _make_state(disk_free_gb=None)
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Log Export")
        assert r.status == CapabilityStatus.BLOCKED


# ---------------------------------------------------------------------------
# Fleet View evaluator — identity callsign
# ---------------------------------------------------------------------------

class TestFleetViewEvaluator:
    def test_ready_with_callsign(self):
        state = _make_state(identity_callsign="HYDRA-01-DRONE")
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Fleet View")
        assert r.status == CapabilityStatus.READY

    def test_blocked_without_callsign(self):
        state = _make_state(identity_callsign="")
        reports = evaluate_all(state)
        r = next(r for r in reports if r.name == "Fleet View")
        assert r.status == CapabilityStatus.BLOCKED
        assert any("callsign" in reason.lower() for reason in r.reasons)


# ---------------------------------------------------------------------------
# Aggregator — overall state derivation from mixed provider states
# ---------------------------------------------------------------------------

class TestAggregator:
    """Verify that evaluate_all() composes per-provider states correctly.

    The endpoint does NOT compute an "overall" rollup today — each provider
    surfaces its own state and the frontend renders them as rows. These tests
    pin the aggregator behaviour that the dashboard depends on: every
    provider always returns a CapabilityReport, never None, and a broken
    evaluator becomes a WARN row rather than crashing the page.
    """

    def test_all_providers_return_reports(self):
        state = _make_state()
        reports = evaluate_all(state)
        assert len(reports) == len(EXPECTED_CAPABILITIES)
        assert all(isinstance(r, CapabilityReport) for r in reports)

    def test_one_blocked_provider_does_not_block_others(self):
        """A BLOCKED on one provider must not affect READY siblings."""
        state = _make_state(gps_fix=0)
        reports = evaluate_all(state)
        gps = next(r for r in reports if r.name == "GPS")
        det = next(r for r in reports if r.name == "Detection")
        assert gps.status == CapabilityStatus.BLOCKED
        assert det.status == CapabilityStatus.READY

    def test_all_warn_still_returns_reports(self):
        """A mix of WARN providers still produces a full report set."""
        state = _make_state(
            disk_free_gb=1.5,        # Disk WARN
            servo_locked_track_id=None,  # Servo + Follow WARN
        )
        reports = evaluate_all(state)
        assert len(reports) == len(EXPECTED_CAPABILITIES)
        statuses = {r.name: r.status for r in reports}
        assert statuses["Disk"] == CapabilityStatus.WARN
        assert statuses["Servo Tracking"] == CapabilityStatus.WARN

    def test_broken_evaluator_returns_warn_not_crash(self):
        """Closes the never-crash-the-status-page contract."""
        # Use a stub state object that raises on attribute access for a
        # field one evaluator reads. Instead of constructing one, we patch
        # an evaluator to raise and confirm the rollup recovers.
        from hydra_detect import capability_status as cs

        original = cs._EVALUATORS
        try:
            def _boom(_state):
                raise RuntimeError("synthetic evaluator failure")
            cs._EVALUATORS = original + [("__synthetic__", _boom)]
            reports = evaluate_all(_make_state())
            r = next(r for r in reports if r.name == "__synthetic__")
            assert r.status == CapabilityStatus.WARN
            assert any("Evaluator error" in reason for reason in r.reasons)
        finally:
            cs._EVALUATORS = original


# ---------------------------------------------------------------------------
# build_system_state — wiring for new refs
# ---------------------------------------------------------------------------

class TestBuildSystemStateExtensions:
    """Verify build_system_state populates the new fields from live refs."""

    def test_servo_state_ref_populates_fields(self):
        from hydra_detect.capability_status import build_system_state

        class FakeServo:
            def get_api_status(self):
                return {"enabled": True, "locked_track_id": 11}

        state = build_system_state(servo_state_ref=FakeServo())
        assert state.servo_enabled is True
        assert state.servo_locked_track_id == 11

    def test_servo_state_ref_no_lock(self):
        from hydra_detect.capability_status import build_system_state

        class FakeServo:
            def get_api_status(self):
                return {"enabled": True, "locked_track_id": None}

        state = build_system_state(servo_state_ref=FakeServo())
        assert state.servo_enabled is True
        assert state.servo_locked_track_id is None

    def test_autonomy_ref_populates_fields(self):
        from hydra_detect.capability_status import build_system_state

        class FakeAutonomy:
            enabled = True

            def get_mode(self):
                return "shadow"

            def _has_valid_geofence(self):
                return True

        state = build_system_state(autonomy_ref=FakeAutonomy())
        assert state.autonomy_mode == "shadow"
        assert state.autonomy_enabled is True
        assert state.autonomy_geofence_present is True

    def test_operating_mode_string(self):
        from hydra_detect.capability_status import build_system_state
        state = build_system_state(operating_mode="ARMED")
        assert state.operating_mode == "ARMED"

    def test_operating_mode_normalises_case(self):
        from hydra_detect.capability_status import build_system_state
        state = build_system_state(operating_mode="armed")
        assert state.operating_mode == "ARMED"

    def test_all_none_returns_conservative_defaults(self):
        """Safety net: with no refs wired, every field is at a 'block' default."""
        from hydra_detect.capability_status import build_system_state
        state = build_system_state()
        assert state.servo_enabled is False
        assert state.servo_locked_track_id is None
        assert state.autonomy_enabled is False
        assert state.autonomy_geofence_present is False
        assert state.identity_callsign == ""


# ---------------------------------------------------------------------------
# Placeholder capabilities — Drop, RF Hunt
# ---------------------------------------------------------------------------

class TestPlaceholderCapabilities:
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


# ---------------------------------------------------------------------------
# Disk-BLOCKED gate (issue #226): mission-start refusal + listener notification
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_SKIP_SERVER, reason=_skip_server_reason)
class TestDiskBlockedGate:
    """End-to-end: when the registry reports disk BLOCKED, the mission-start
    endpoint refuses with 503 and crop-gate listeners get notified. When the
    state recovers, both flip back automatically."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        from hydra_detect.web.capability_api import (
            reset_cache,
            reset_disk_gate_for_test,
        )
        reset_cache()
        reset_disk_gate_for_test()
        yield
        reset_cache()
        reset_disk_gate_for_test()

    def test_set_disk_blocked_notifies_listeners(self):
        from hydra_detect.web import capability_api
        seen: list = []
        capability_api.register_disk_gate_listener(
            lambda b, r: seen.append((b, r))
        )
        capability_api._set_disk_blocked(True, "disk_free below 5%")
        capability_api._set_disk_blocked(False, "")
        assert seen == [(True, "disk_free below 5%"), (False, "")]

    def test_listener_no_op_when_state_does_not_flip(self):
        from hydra_detect.web import capability_api
        seen: list = []
        capability_api.register_disk_gate_listener(
            lambda b, r: seen.append((b, r))
        )
        capability_api._set_disk_blocked(False, "")
        capability_api._set_disk_blocked(False, "")
        capability_api._set_disk_blocked(False, "")
        assert seen == []

    def test_is_disk_blocked_defaults_false(self):
        from hydra_detect.web.capability_api import is_disk_blocked
        blocked, reason = is_disk_blocked()
        assert blocked is False
        assert reason == ""

    def test_mission_start_refused_when_disk_blocked(self):
        from fastapi.testclient import TestClient
        from hydra_detect.web import capability_api
        from hydra_detect.web.server import app
        capability_api._set_disk_blocked(
            True, "disk_free below 5% AND under 5GB free",
        )
        client = TestClient(app)
        resp = client.post("/api/mission/start", json={"name": "blocked-test"})
        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert "disk_free below 5%" in body.get("reason", "")

    def test_mission_start_not_blocked_in_warn_state(self):
        from fastapi.testclient import TestClient
        from hydra_detect.web import capability_api
        from hydra_detect.web.server import app
        capability_api._set_disk_blocked(False, "")
        client = TestClient(app)
        resp = client.post("/api/mission/start", json={"name": "warn-test"})
        assert resp.status_code != 503

    def test_recovery_clears_gate(self):
        from fastapi.testclient import TestClient
        from hydra_detect.web import capability_api
        from hydra_detect.web.server import app
        client = TestClient(app)
        capability_api._set_disk_blocked(True, "disk_free below 5%")
        r1 = client.post("/api/mission/start", json={"name": "low-disk"})
        assert r1.status_code == 503
        capability_api._set_disk_blocked(False, "")
        r2 = client.post("/api/mission/start", json={"name": "recovered"})
        assert r2.status_code != 503


# ---------------------------------------------------------------------------
# DetectionLogger.set_disk_blocked toggles crop emission only
# ---------------------------------------------------------------------------

class TestDetectionLoggerDiskBlocked:
    def test_toggle_persists(self):
        from hydra_detect.detection_logger import DetectionLogger
        dl = DetectionLogger(log_dir="/tmp/_t226", save_crops=True)
        # Default: not blocked.
        assert dl._disk_blocked is False
        dl.set_disk_blocked(True)
        assert dl._disk_blocked is True
        dl.set_disk_blocked(False)
        assert dl._disk_blocked is False
