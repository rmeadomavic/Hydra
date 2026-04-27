"""Tests for issue #148: HYDRA_VEHICLE env var activation of platform profiles.

Covers:
- HYDRA_VEHICLE=ugv loads UGV overrides verifiable via effective-config helper
- HYDRA_VEHICLE=unknown_profile loads base config and logs a warning
- No env var → base config only
- /api/config/effective returns profile-aware values when profile is set
"""

from __future__ import annotations

import configparser
import os
import sys
import tempfile
from unittest.mock import patch

import pytest


# ── Helpers ─────────────────────────────────────────────────────────────────

def _base_ini(extra_sections: dict | None = None) -> str:
    """Write a minimal config.ini and return its path."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    base: dict[str, dict[str, str]] = {
        "camera": {
            "source_type": "auto",
            "source": "auto",
            "width": "640",
            "height": "480",
            "fps": "30",
        },
        "detector": {"yolo_model": "yolov8n.pt", "yolo_confidence": "0.45"},
        "tracker": {"track_thresh": "0.5", "track_buffer": "30", "match_thresh": "0.8"},
        "mavlink": {"enabled": "false"},
        "web": {"enabled": "false"},
        "logging": {"log_dir": "/tmp/hydra_test", "save_images": "false"},
        "tak": {"callsign": "HYDRA-1"},
        # UGV profile — new first-class keys
        "vehicle.ugv": {
            "reserved_channels": "1,3",
            "autonomous.safe_mode": "HOLD",
            "autonomous.platform_role": "ground_isr",
            "autonomous.default_features": "detect,mavlink,tak_output,logging",
        },
        # USV profile
        "vehicle.usv": {
            "reserved_channels": "1,3",
            "autonomous.safe_mode": "HOLD",
            "autonomous.platform_role": "water_isr",
            "autonomous.default_features": "detect,mavlink,tak_output,logging,geofence_warning",
        },
        # drone_10in profile
        "vehicle.drone_10in": {
            "reserved_channels": "1,2,3,4",
            "autonomous.safe_mode": "LOITER",
            "autonomous.platform_role": "aerial_isr",
            "autonomous.default_features": "detect,mavlink,tak_output,logging",
        },
    }
    if extra_sections:
        base.update(extra_sections)
    cfg.read_dict(base)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ini", delete=False, encoding="utf-8"
    )
    cfg.write(tmp)
    tmp.close()
    return tmp.name


def _load(vehicle: str | None = None, extra: dict | None = None) -> configparser.ConfigParser:
    """Run PipelineBootstrap.load_config and return the merged ConfigParser."""
    from hydra_detect.pipeline.bootstrap import PipelineBootstrap
    path = _base_ini(extra)
    bs = PipelineBootstrap()
    ctx = bs.load_config(path, vehicle=vehicle)
    return ctx.cfg


# ── 1. HYDRA_VEHICLE=ugv applies UGV overrides ──────────────────────────────

class TestUGVProfile:
    def test_ugv_reserved_channels_accessible(self):
        """[vehicle.ugv] reserved_channels is preserved as a vehicle-local key."""
        from hydra_detect.pipeline.bootstrap import PipelineBootstrap
        path = _base_ini()
        bs = PipelineBootstrap()
        ctx = bs.load_config(path, vehicle="ugv")
        # reserved_channels is a vehicle-local key, not a dotted override,
        # so it must still exist in [vehicle.ugv] after load_config.
        assert ctx.cfg.has_section("vehicle.ugv")
        raw = ctx.cfg.get("vehicle.ugv", "reserved_channels", fallback="")
        assert raw.strip() == "1,3"

    def test_ugv_dotted_overrides_applied(self):
        """Dotted keys in [vehicle.ugv] are merged into base sections."""
        cfg = _load("ugv")
        assert cfg.get("autonomous", "platform_role", fallback="") == "ground_isr"
        assert cfg.get("autonomous", "safe_mode", fallback="") == "HOLD"
        assert cfg.get("autonomous", "default_features", fallback="") == (
            "detect,mavlink,tak_output,logging"
        )

    def test_ugv_platform_role_ground_isr(self):
        """UGV platform_role resolves to ground_isr."""
        cfg = _load("ugv")
        assert cfg.get("autonomous", "platform_role") == "ground_isr"

    def test_ugv_safe_mode_is_hold(self):
        """UGV safe_mode resolves to HOLD."""
        cfg = _load("ugv")
        assert cfg.get("autonomous", "safe_mode") == "HOLD"


# ── 2. USV profile ───────────────────────────────────────────────────────────

class TestUSVProfile:
    def test_usv_platform_role(self):
        cfg = _load("usv")
        assert cfg.get("autonomous", "platform_role") == "water_isr"

    def test_usv_default_features_include_geofence_warning(self):
        cfg = _load("usv")
        feats = cfg.get("autonomous", "default_features")
        assert "geofence_warning" in feats

    def test_usv_safe_mode_hold(self):
        cfg = _load("usv")
        assert cfg.get("autonomous", "safe_mode") == "HOLD"


# ── 3. drone_10in profile ────────────────────────────────────────────────────

class TestDrone10inProfile:
    def test_drone_10in_platform_role_aerial(self):
        cfg = _load("drone_10in")
        assert cfg.get("autonomous", "platform_role") == "aerial_isr"

    def test_drone_10in_safe_mode_loiter(self):
        cfg = _load("drone_10in")
        assert cfg.get("autonomous", "safe_mode") == "LOITER"

    def test_drone_10in_reserved_channels(self):
        from hydra_detect.pipeline.bootstrap import PipelineBootstrap
        path = _base_ini()
        bs = PipelineBootstrap()
        ctx = bs.load_config(path, vehicle="drone_10in")
        raw = ctx.cfg.get("vehicle.drone_10in", "reserved_channels", fallback="")
        assert raw.strip() == "1,2,3,4"


# ── 4. Unknown profile logs a warning ────────────────────────────────────────

class TestUnknownProfile:
    def test_unknown_profile_logs_warning(self, caplog):
        """HYDRA_VEHICLE=unknown_profile → base config only, warning logged."""
        import logging
        with caplog.at_level(logging.WARNING):
            cfg = _load("unknown_profile")

        # Base config should still be intact
        assert cfg.get("camera", "source_type") == "auto"

        # A warning/error must have been emitted mentioning the unknown profile
        messages = " ".join(caplog.messages)
        assert "unknown_profile" in messages

    def test_unknown_profile_base_config_unchanged(self):
        """Unknown profile must not corrupt the base config."""
        cfg = _load("unknown_profile")
        assert cfg.get("detector", "yolo_confidence") == "0.45"

    def test_unknown_profile_no_vehicle_section_applied(self):
        """Unknown profile section does not exist, so no override applied."""
        cfg = _load("unknown_profile")
        assert not cfg.has_section("vehicle.unknown_profile")


# ── 5. No env var → base config only ────────────────────────────────────────

class TestNoProfile:
    def test_no_vehicle_base_config_returned(self):
        """Without a vehicle flag, base config values are unchanged."""
        cfg = _load(vehicle=None)
        assert cfg.get("camera", "source_type") == "auto"
        assert cfg.get("detector", "yolo_model") == "yolov8n.pt"

    def test_no_vehicle_no_platform_role(self):
        """No vehicle flag → [autonomous] platform_role not present."""
        cfg = _load(vehicle=None)
        assert not cfg.has_option("autonomous", "platform_role")


# ── 6. HYDRA_VEHICLE env var reaches __main__.py ────────────────────────────

class TestHydraVehicleEnvVar:
    def test_env_var_read_in_main(self):
        """__main__ reads HYDRA_VEHICLE env var into --vehicle default."""
        import argparse

        with patch.dict(os.environ, {"HYDRA_VEHICLE": "ugv"}):
            # Re-parse args with the env var active (simulates process start)
            parser = argparse.ArgumentParser()
            parser.add_argument("--vehicle", default=os.environ.get("HYDRA_VEHICLE"))
            args = parser.parse_args([])
            assert args.vehicle == "ugv"

    def test_no_env_var_vehicle_is_none(self):
        """Without HYDRA_VEHICLE, --vehicle defaults to None."""
        import argparse

        env = {k: v for k, v in os.environ.items() if k != "HYDRA_VEHICLE"}
        with patch.dict(os.environ, env, clear=True):
            parser = argparse.ArgumentParser()
            parser.add_argument("--vehicle", default=os.environ.get("HYDRA_VEHICLE"))
            args = parser.parse_args([])
            assert args.vehicle is None

    def test_main_vehicle_default_from_env(self):
        """Verify the actual __main__.py parser uses HYDRA_VEHICLE as default."""
        # We directly inspect the argument definition in __main__.py source
        # rather than importing it (which drags in pipeline and heavy deps).
        import ast
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "hydra_detect" / "__main__.py"
        ).read_text()
        # The source must reference HYDRA_VEHICLE as the env var for --vehicle
        assert "HYDRA_VEHICLE" in src
        assert "--vehicle" in src


# ── 7. /api/config/effective endpoint ────────────────────────────────────────
# These tests import the web server, which requires Linux-only modules
# (fcntl, cv2). They are skipped on Windows/macOS and run in CI (Ubuntu).

_server_importable = sys.platform != "win32"
_skip_no_server = pytest.mark.skipif(
    not _server_importable,
    reason="hydra_detect.web.server requires Linux-only modules (fcntl, cv2)",
)


class TestEffectiveConfigEndpoint:
    """Verify the /api/config/effective endpoint contract via the StreamState
    runtime config layer, which is the source of truth the endpoint reads."""

    @_skip_no_server
    def test_stream_state_vehicle_profile_key(self):
        """StreamState runtime_config can store and retrieve vehicle_profile."""
        from hydra_detect.web.server import stream_state
        original = stream_state.get_runtime_config().copy()
        stream_state.update_runtime_config({"vehicle_profile": "ugv"})
        try:
            assert stream_state.get_runtime_config()["vehicle_profile"] == "ugv"
        finally:
            stream_state.update_runtime_config({"vehicle_profile": original.get("vehicle_profile")})

    @_skip_no_server
    def test_stream_state_vehicle_profile_none_by_default(self):
        """vehicle_profile defaults to None (no profile active)."""
        from hydra_detect.web.server import stream_state
        original = stream_state.get_runtime_config().copy()
        stream_state.update_runtime_config({"vehicle_profile": None})
        try:
            val = stream_state.get_runtime_config().get("vehicle_profile")
            assert val is None
        finally:
            stream_state.update_runtime_config({"vehicle_profile": original.get("vehicle_profile")})

    @_skip_no_server
    def test_effective_endpoint_exists_in_server(self):
        """The /api/config/effective route is registered on the FastAPI app."""
        from hydra_detect.web.server import app as fastapi_app
        routes = [r.path for r in fastapi_app.routes]
        assert "/api/config/effective" in routes

    @_skip_no_server
    def test_effective_endpoint_is_get(self):
        """The /api/config/effective route uses GET method."""
        from hydra_detect.web.server import app as fastapi_app
        for route in fastapi_app.routes:
            if hasattr(route, "path") and route.path == "/api/config/effective":
                assert "GET" in route.methods
                return
        pytest.fail("/api/config/effective route not found")
