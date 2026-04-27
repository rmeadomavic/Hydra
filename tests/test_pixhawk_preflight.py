"""Tests for scripts/pixhawk_preflight.py — Pixhawk prerequisite validation."""

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers to import the script under test without a MAVLink connection
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "pixhawk_preflight.py"
MANIFEST_DIR = Path(__file__).parent.parent / "hydra_detect" / "profiles"


def _load_preflight_module():
    """Import pixhawk_preflight.py as a module (not __main__).

    Registers under 'pixhawk_preflight' in sys.modules so any module-level
    machinery that does module lookups finds it correctly.
    """
    module_name = "pixhawk_preflight"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Manifest loader tests
# ---------------------------------------------------------------------------

class TestManifestLoader:
    """Tests for load_manifest()."""

    def test_loads_ugv_manifest(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("ugv")
        assert manifest["profile"] == "ugv"
        assert manifest["firmware"] == "ArduRover"
        assert "required" in manifest
        assert "recommended" in manifest
        assert "stream_rates" in manifest

    def test_loads_usv_manifest(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("usv")
        assert manifest["profile"] == "usv"
        assert manifest["firmware"] == "ArduRover"

    def test_loads_drone_10in_manifest(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("drone_10in")
        assert manifest["profile"] == "drone_10in"
        assert manifest["firmware"] == "ArduCopter"

    def test_missing_profile_raises(self):
        mod = _load_preflight_module()
        with pytest.raises(FileNotFoundError):
            mod.load_manifest("nonexistent_profile")

    def test_manifest_required_entries_have_name_expected_reason(self):
        mod = _load_preflight_module()
        for profile in ("ugv", "usv", "drone_10in"):
            manifest = mod.load_manifest(profile)
            for entry in manifest.get("required", []):
                assert "name" in entry, f"{profile}: required entry missing 'name'"
                assert "expected" in entry, f"{profile}: required entry missing 'expected'"
                assert "reason" in entry, f"{profile}: required entry missing 'reason'"

    def test_manifest_recommended_entries_have_name_expected_reason(self):
        mod = _load_preflight_module()
        for profile in ("ugv", "usv", "drone_10in"):
            manifest = mod.load_manifest(profile)
            for entry in manifest.get("recommended", []):
                assert "name" in entry
                assert "expected" in entry
                assert "reason" in entry

    def test_invalid_yaml_raises(self, tmp_path):
        mod = _load_preflight_module()
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("{ not: valid: yaml: [}")
        with pytest.raises(Exception):
            mod._parse_manifest_yaml(bad_yaml)

    def test_manifest_schema_missing_profile_key_raises(self, tmp_path):
        mod = _load_preflight_module()
        data = {"firmware": "ArduRover", "required": [], "recommended": [], "stream_rates": {}}
        yaml_file = tmp_path / "manifest.yaml"
        yaml_file.write_text(yaml.dump(data))
        with pytest.raises(ValueError, match="profile"):
            mod._validate_manifest_schema(data, yaml_file)

    def test_manifest_schema_missing_firmware_key_raises(self, tmp_path):
        mod = _load_preflight_module()
        data = {"profile": "ugv", "required": [], "recommended": [], "stream_rates": {}}
        yaml_file = tmp_path / "manifest.yaml"
        yaml_file.write_text(yaml.dump(data))
        with pytest.raises(ValueError, match="firmware"):
            mod._validate_manifest_schema(data, yaml_file)


# ---------------------------------------------------------------------------
# Validator tests — synthetic param dicts
# ---------------------------------------------------------------------------

class TestValidateParams:
    """Tests for validate_params(manifest, live_params) -> list[Result]."""

    def _make_manifest(self, required=None, recommended=None, stream_rates=None):
        return {
            "profile": "ugv",
            "firmware": "ArduRover",
            "required": required or [],
            "recommended": recommended or [],
            "stream_rates": stream_rates or {},
        }

    def test_all_pass_required(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            required=[{"name": "FENCE_ENABLE", "expected": 1, "reason": "Geofence required"}]
        )
        results = mod.validate_params(manifest, {"FENCE_ENABLE": 1.0})
        assert len(results) == 1
        assert results[0].status == "PASS"
        assert results[0].name == "FENCE_ENABLE"

    def test_fail_wrong_required_value(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            required=[{"name": "FENCE_ENABLE", "expected": 1, "reason": "Geofence required"}]
        )
        results = mod.validate_params(manifest, {"FENCE_ENABLE": 0.0})
        assert results[0].status == "FAIL"

    def test_fail_missing_required_param(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            required=[{"name": "FENCE_ENABLE", "expected": 1, "reason": "Geofence required"}]
        )
        results = mod.validate_params(manifest, {})
        assert results[0].status == "FAIL"
        assert "missing" in results[0].message.lower() or results[0].actual is None

    def test_warn_wrong_recommended_value(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            recommended=[{"name": "BATT_FS_LOW_ACT", "expected": 2, "reason": "RTL on low battery"}]
        )
        results = mod.validate_params(manifest, {"BATT_FS_LOW_ACT": 0.0})
        assert results[0].status == "WARN"

    def test_pass_correct_recommended_value(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            recommended=[{"name": "BATT_FS_LOW_ACT", "expected": 2, "reason": "RTL on low battery"}]
        )
        results = mod.validate_params(manifest, {"BATT_FS_LOW_ACT": 2.0})
        assert results[0].status == "PASS"

    def test_stream_rate_min_check_pass(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(stream_rates={"SR1_POSITION": 5})
        results = mod.validate_params(manifest, {"SR1_POSITION": 5.0})
        assert results[0].status == "PASS"

    def test_stream_rate_min_check_fail_below_minimum(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(stream_rates={"SR1_POSITION": 5})
        results = mod.validate_params(manifest, {"SR1_POSITION": 2.0})
        assert results[0].status == "FAIL"
        assert "≥" in results[0].message or ">=" in results[0].message

    def test_stream_rate_missing_is_fail(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(stream_rates={"SR1_POSITION": 5})
        results = mod.validate_params(manifest, {})
        assert results[0].status == "FAIL"

    def test_stream_rate_above_minimum_passes(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(stream_rates={"SR1_POSITION": 5})
        results = mod.validate_params(manifest, {"SR1_POSITION": 10.0})
        assert results[0].status == "PASS"

    def test_multiple_results_summary_counts(self):
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            required=[
                {"name": "FENCE_ENABLE", "expected": 1, "reason": ""},
                {"name": "MISSING_PARAM", "expected": 1, "reason": ""},
            ],
            recommended=[
                {"name": "BATT_FS_LOW_ACT", "expected": 2, "reason": ""},
            ],
            stream_rates={"SR1_POSITION": 5},
        )
        live_params = {"FENCE_ENABLE": 1.0, "BATT_FS_LOW_ACT": 0.0, "SR1_POSITION": 5.0}
        results = mod.validate_params(manifest, live_params)
        statuses = [r.status for r in results]
        assert statuses.count("PASS") >= 2
        assert statuses.count("FAIL") == 1
        assert statuses.count("WARN") == 1


# ---------------------------------------------------------------------------
# Report formatting tests
# ---------------------------------------------------------------------------

class TestFormatReport:
    """Tests for format_report(profile, firmware, results) -> str."""

    def test_report_header_contains_profile_and_firmware(self):
        mod = _load_preflight_module()
        results = []
        report = mod.format_report("ugv", "ArduRover", results)
        assert "ugv" in report
        assert "ArduRover" in report

    def test_report_contains_pass_fail_warn_lines(self):
        mod = _load_preflight_module()
        Result = mod.PreflightResult
        results = [
            Result(name="FENCE_ENABLE", status="PASS", actual=1.0, expected=1, message="FENCE_ENABLE = 1"),
            Result(name="SR1_POSITION", status="FAIL", actual=2.0, expected=5, message="SR1_POSITION = 2 (expected ≥ 5)"),
            Result(name="BATT_FS_LOW_ACT", status="WARN", actual=0.0, expected=2, message="BATT_FS_LOW_ACT = 0 (recommended 2)"),
        ]
        report = mod.format_report("ugv", "ArduRover", results)
        assert "[PASS]" in report
        assert "[FAIL]" in report
        assert "[WARN]" in report

    def test_report_summary_line_correct(self):
        mod = _load_preflight_module()
        Result = mod.PreflightResult
        results = [
            Result(name="A", status="PASS", actual=1.0, expected=1, message="A = 1"),
            Result(name="B", status="FAIL", actual=0.0, expected=1, message="B = 0"),
            Result(name="C", status="WARN", actual=0.0, expected=2, message="C = 0"),
        ]
        report = mod.format_report("ugv", "ArduRover", results)
        assert "1 PASS" in report
        assert "1 FAIL" in report
        assert "1 WARN" in report

    def test_empty_results_shows_zero_summary(self):
        mod = _load_preflight_module()
        report = mod.format_report("usv", "ArduRover", [])
        assert "0 PASS" in report or "Summary" in report


# ---------------------------------------------------------------------------
# Exit code tests (mocking the MAVLink connection)
# ---------------------------------------------------------------------------

class TestCLIExitCodes:
    """Tests for CLI exit codes via compute_exit_code()."""

    def _make_manifest(self, required=None, recommended=None, stream_rates=None):
        return {
            "profile": "ugv",
            "firmware": "ArduRover",
            "required": required or [],
            "recommended": recommended or [],
            "stream_rates": stream_rates or {},
        }

    def test_exit_0_all_pass(self):
        """Exit 0 when all required params pass (warnings allowed)."""
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            required=[{"name": "FENCE_ENABLE", "expected": 1, "reason": ""}],
            recommended=[{"name": "BATT_FS_LOW_ACT", "expected": 2, "reason": ""}],
        )
        live_params = {"FENCE_ENABLE": 1.0, "BATT_FS_LOW_ACT": 0.0}  # WARN allowed
        code = mod.compute_exit_code(mod.validate_params(manifest, live_params))
        assert code == 0

    def test_exit_1_any_fail(self):
        """Exit 1 when any required param fails."""
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            required=[{"name": "FENCE_ENABLE", "expected": 1, "reason": ""}]
        )
        live_params = {"FENCE_ENABLE": 0.0}
        code = mod.compute_exit_code(mod.validate_params(manifest, live_params))
        assert code == 1

    def test_exit_1_stream_rate_fail(self):
        """Exit 1 when stream rate is below minimum."""
        mod = _load_preflight_module()
        manifest = self._make_manifest(stream_rates={"SR1_POSITION": 5})
        live_params = {"SR1_POSITION": 2.0}
        code = mod.compute_exit_code(mod.validate_params(manifest, live_params))
        assert code == 1

    def test_exit_0_warns_only(self):
        """Exit 0 when only warnings (no failures)."""
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            recommended=[{"name": "BATT_FS_LOW_ACT", "expected": 2, "reason": ""}]
        )
        live_params = {"BATT_FS_LOW_ACT": 0.0}
        code = mod.compute_exit_code(mod.validate_params(manifest, live_params))
        assert code == 0

    def test_exit_0_all_pass_no_warns(self):
        """Exit 0 with all required + recommended passing."""
        mod = _load_preflight_module()
        manifest = self._make_manifest(
            required=[{"name": "FENCE_ENABLE", "expected": 1, "reason": ""}],
            recommended=[{"name": "BATT_FS_LOW_ACT", "expected": 2, "reason": ""}],
        )
        live_params = {"FENCE_ENABLE": 1.0, "BATT_FS_LOW_ACT": 2.0}
        code = mod.compute_exit_code(mod.validate_params(manifest, live_params))
        assert code == 0


# ---------------------------------------------------------------------------
# Per-profile all-pass / one-fail / one-warn / missing-required scenarios
# ---------------------------------------------------------------------------

class TestProfileScenarios:
    """End-to-end validation scenarios using real manifests."""

    @pytest.mark.parametrize("profile", ["ugv", "usv", "drone_10in"])
    def test_all_pass_scenario(self, profile):
        mod = _load_preflight_module()
        manifest = mod.load_manifest(profile)
        # Build live_params that satisfy all required + recommended + stream_rates
        live_params = {}
        for entry in manifest.get("required", []):
            live_params[entry["name"]] = float(entry["expected"])
        for entry in manifest.get("recommended", []):
            live_params[entry["name"]] = float(entry["expected"])
        for name, val in manifest.get("stream_rates", {}).items():
            live_params[name] = float(val)
        results = mod.validate_params(manifest, live_params)
        statuses = {r.status for r in results}
        assert "FAIL" not in statuses
        assert "WARN" not in statuses
        assert mod.compute_exit_code(results) == 0

    @pytest.mark.parametrize("profile", ["ugv", "usv", "drone_10in"])
    def test_one_fail_scenario(self, profile):
        mod = _load_preflight_module()
        manifest = mod.load_manifest(profile)
        if not manifest.get("required"):
            pytest.skip(f"No required params in {profile} manifest")
        entry = manifest["required"][0]
        live_params = {entry["name"]: float(entry["expected"]) + 99.0}  # wrong value
        results = mod.validate_params(manifest, live_params)
        fail_results = [r for r in results if r.status == "FAIL"]
        assert len(fail_results) >= 1
        assert mod.compute_exit_code(results) == 1

    @pytest.mark.parametrize("profile", ["ugv", "usv", "drone_10in"])
    def test_one_warn_scenario(self, profile):
        mod = _load_preflight_module()
        manifest = mod.load_manifest(profile)
        if not manifest.get("recommended"):
            pytest.skip(f"No recommended params in {profile} manifest")
        # Satisfy all required and stream_rates, satisfy all recommended first,
        # then break only the first recommended entry
        live_params = {}
        for entry in manifest.get("required", []):
            live_params[entry["name"]] = float(entry["expected"])
        for name, val in manifest.get("stream_rates", {}).items():
            live_params[name] = float(val)
        for entry in manifest.get("recommended", []):
            live_params[entry["name"]] = float(entry["expected"])
        bad_entry = manifest["recommended"][0]
        live_params[bad_entry["name"]] = float(bad_entry["expected"]) + 99.0
        results = mod.validate_params(manifest, live_params)
        warn_results = [r for r in results if r.status == "WARN"]
        assert len(warn_results) >= 1
        assert mod.compute_exit_code(results) == 0  # WARN doesn't fail

    @pytest.mark.parametrize("profile", ["ugv", "usv", "drone_10in"])
    def test_missing_required_is_fail(self, profile):
        mod = _load_preflight_module()
        manifest = mod.load_manifest(profile)
        if not manifest.get("required"):
            pytest.skip(f"No required params in {profile} manifest")
        # Provide nothing — all required and stream_rates should fail
        results = mod.validate_params(manifest, {})
        fail_results = [r for r in results if r.status == "FAIL"]
        assert len(fail_results) >= 1
        assert mod.compute_exit_code(results) == 1


# ---------------------------------------------------------------------------
# collect_params mock tests (MAVLink connection is mocked)
# ---------------------------------------------------------------------------

class TestCollectParams:
    """Tests for collect_params() with a mocked MAVLink connection."""

    def test_returns_dict_of_params(self):
        mod = _load_preflight_module()

        mock_conn = MagicMock()
        mock_conn.target_system = 1
        mock_conn.target_component = 1

        param1 = MagicMock()
        param1.param_id = "FENCE_ENABLE"
        param1.param_value = 1.0
        param1.param_count = 2
        param1.param_index = 0

        param2 = MagicMock()
        param2.param_id = "SR1_POSITION"
        param2.param_value = 5.0
        param2.param_count = 2
        param2.param_index = 1

        # After draining the list, return None indefinitely (triggers quiescent exit)
        call_returns = [param1, param2]

        def _recv(**kwargs):
            return call_returns.pop(0) if call_returns else None

        mock_conn.recv_match.side_effect = _recv

        params = mod.collect_params(mock_conn, timeout=5, quiescent=0.1)
        assert "FENCE_ENABLE" in params
        assert params["FENCE_ENABLE"] == pytest.approx(1.0)
        assert "SR1_POSITION" in params
        assert params["SR1_POSITION"] == pytest.approx(5.0)

    def test_empty_params_on_immediate_timeout(self):
        mod = _load_preflight_module()
        mock_conn = MagicMock()
        mock_conn.target_system = 1
        mock_conn.target_component = 1
        mock_conn.recv_match.return_value = None

        # With a very short timeout, collect_params should return empty or few params
        params = mod.collect_params(mock_conn, timeout=0.05, quiescent=0.05)
        assert isinstance(params, dict)

    def test_param_id_whitespace_stripped(self):
        mod = _load_preflight_module()
        mock_conn = MagicMock()
        mock_conn.target_system = 1
        mock_conn.target_component = 1

        param1 = MagicMock()
        param1.param_id = "FENCE_ENABLE\x00\x00"  # null-padded as MAVLink sends
        param1.param_value = 1.0
        param1.param_count = 1
        param1.param_index = 0

        call_returns = [param1]

        def _recv(**kwargs):
            return call_returns.pop(0) if call_returns else None

        mock_conn.recv_match.side_effect = _recv
        params = mod.collect_params(mock_conn, timeout=5, quiescent=0.1)
        # Key must be clean string, not null-padded
        assert "FENCE_ENABLE" in params


# ---------------------------------------------------------------------------
# Manifest content spot-checks for parameter correctness
# ---------------------------------------------------------------------------

class TestManifestContent:
    """Spot-check key parameters exist in manifests with correct values."""

    def test_ugv_requires_fence_enable(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("ugv")
        required_names = [e["name"] for e in manifest["required"]]
        assert "FENCE_ENABLE" in required_names
        entry = next(e for e in manifest["required"] if e["name"] == "FENCE_ENABLE")
        assert entry["expected"] == 1

    def test_ugv_has_stream_rate_sr1_position(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("ugv")
        assert "SR1_POSITION" in manifest["stream_rates"]
        assert manifest["stream_rates"]["SR1_POSITION"] >= 5

    def test_usv_requires_fence_enable(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("usv")
        required_names = [e["name"] for e in manifest["required"]]
        assert "FENCE_ENABLE" in required_names

    def test_usv_requires_frame_class(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("usv")
        required_names = [e["name"] for e in manifest["required"]]
        assert "FRAME_CLASS" in required_names
        entry = next(e for e in manifest["required"] if e["name"] == "FRAME_CLASS")
        assert entry["expected"] == 2  # Boat frame class in ArduRover

    def test_drone_requires_fence_enable(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("drone_10in")
        required_names = [e["name"] for e in manifest["required"]]
        assert "FENCE_ENABLE" in required_names

    def test_drone_has_stream_rate_sr1_position(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("drone_10in")
        assert "SR1_POSITION" in manifest["stream_rates"]
        assert manifest["stream_rates"]["SR1_POSITION"] >= 5

    def test_ugv_recommends_battery_failsafe(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("ugv")
        rec_names = [e["name"] for e in manifest["recommended"]]
        assert "BATT_FS_LOW_ACT" in rec_names
        entry = next(e for e in manifest["recommended"] if e["name"] == "BATT_FS_LOW_ACT")
        assert entry["expected"] == 2  # RTL

    def test_drone_recommends_gcs_failsafe(self):
        mod = _load_preflight_module()
        manifest = mod.load_manifest("drone_10in")
        rec_names = [e["name"] for e in manifest["recommended"]]
        assert "FS_GCS_ENABLE" in rec_names
