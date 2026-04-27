"""Tests for operating mode system — issue #147.

Covers:
- OperatingMode enum round-trip
- current_mode() reads from config
- set_mode() persists and reads back
- ARMED transition requires confirmed_twice=True
- Event timeline entry written on transition
- Invalid mode raises
- API: GET returns current mode
- API: POST transitions mode
- API: POST to ARMED without confirm=True returns 400
- API: POST with bad mode returns 422
- Factory reset → OBSERVE
"""

from __future__ import annotations

import configparser
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.operating_mode import (
    ModeTransitionError,
    OperatingMode,
    current_mode,
    set_mode,
)
from hydra_detect.web.server import app, configure_auth, stream_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_server_state():
    configure_auth(None)
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Minimal config.ini with [system] section."""
    cfg = configparser.ConfigParser()
    cfg["system"] = {"mode": "OBSERVE"}
    cfg["web"] = {
        "host": "0.0.0.0",
        "port": "8080",
        "api_token": "",
        "web_password": "",
        "session_timeout_min": "480",
        "tls_enabled": "false",
        "tls_cert": "",
        "tls_key": "",
        "require_auth_for_control": "false",
        "mjpeg_quality": "70",
        "hud_layout": "classic",
        "theme": "lattice",
    }
    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        cfg.write(f)
    return path


@pytest.fixture
def tmp_factory(tmp_path: Path) -> Path:
    """config.ini.factory with mode=OBSERVE."""
    cfg = configparser.ConfigParser()
    cfg["system"] = {"mode": "OBSERVE"}
    cfg["web"] = {
        "host": "0.0.0.0", "port": "8080", "api_token": "",
        "web_password": "", "session_timeout_min": "480",
        "tls_enabled": "false", "tls_cert": "", "tls_key": "",
        "require_auth_for_control": "false", "mjpeg_quality": "70",
        "hud_layout": "classic", "theme": "lattice",
    }
    path = tmp_path / "config.ini"
    factory = tmp_path / "config.ini.factory"
    with open(path, "w") as f:
        cfg.write(f)
    with open(factory, "w") as f:
        cfg.write(f)
    return path


# ---------------------------------------------------------------------------
# Enum round-trip
# ---------------------------------------------------------------------------

class TestOperatingModeEnum:
    def test_all_six_values_exist(self):
        names = {m.name for m in OperatingMode}
        assert names == {"SIM", "BENCH", "OBSERVE", "FIELD", "ARMED", "MAINTENANCE"}

    def test_enum_is_str_subclass(self):
        assert isinstance(OperatingMode.OBSERVE, str)

    def test_round_trip_from_string(self):
        for mode in OperatingMode:
            assert OperatingMode(mode.value) == mode

    def test_value_equals_name(self):
        for mode in OperatingMode:
            assert mode.value == mode.name


# ---------------------------------------------------------------------------
# current_mode()
# ---------------------------------------------------------------------------

class TestCurrentMode:
    def test_reads_observe_from_config(self, tmp_config: Path):
        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        assert current_mode(cfg) == OperatingMode.OBSERVE

    def test_reads_field_from_config(self, tmp_config: Path):
        # Manually set mode to FIELD
        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        cfg.set("system", "mode", "FIELD")
        with open(tmp_config, "w") as f:
            cfg.write(f)

        cfg2 = configparser.ConfigParser()
        cfg2.read(tmp_config)
        assert current_mode(cfg2) == OperatingMode.FIELD

    def test_missing_section_defaults_to_observe(self):
        cfg = configparser.ConfigParser()
        # No [system] section
        assert current_mode(cfg) == OperatingMode.OBSERVE

    def test_invalid_value_defaults_to_observe(self):
        cfg = configparser.ConfigParser()
        cfg["system"] = {"mode": "BOGUS"}
        assert current_mode(cfg) == OperatingMode.OBSERVE


# ---------------------------------------------------------------------------
# set_mode()
# ---------------------------------------------------------------------------

class TestSetMode:
    def test_set_mode_persists_to_config(self, tmp_config: Path):
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            cfg = configparser.ConfigParser()
            cfg.read(tmp_config)
            set_mode(cfg, OperatingMode.FIELD, reason="field sortie", confirmed_twice=False)

        cfg2 = configparser.ConfigParser()
        cfg2.read(tmp_config)
        assert cfg2.get("system", "mode") == "FIELD"

    def test_set_mode_reads_back_correctly(self, tmp_config: Path):
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            cfg = configparser.ConfigParser()
            cfg.read(tmp_config)
            set_mode(cfg, OperatingMode.SIM, reason="simulation start", confirmed_twice=False)

        cfg2 = configparser.ConfigParser()
        cfg2.read(tmp_config)
        assert current_mode(cfg2) == OperatingMode.SIM

    def test_armed_requires_confirmed_twice_true(self, tmp_config: Path):
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            cfg = configparser.ConfigParser()
            cfg.read(tmp_config)
            with pytest.raises(ModeTransitionError, match="ARMED"):
                set_mode(cfg, OperatingMode.ARMED, reason="hot range", confirmed_twice=False)

    def test_armed_succeeds_with_confirmed_twice_true(self, tmp_config: Path):
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            cfg = configparser.ConfigParser()
            cfg.read(tmp_config)
            set_mode(cfg, OperatingMode.ARMED, reason="hot range", confirmed_twice=True)

        cfg2 = configparser.ConfigParser()
        cfg2.read(tmp_config)
        assert cfg2.get("system", "mode") == "ARMED"

    def test_other_modes_do_not_require_confirmed_twice(self, tmp_config: Path):
        for mode in [OperatingMode.BENCH, OperatingMode.MAINTENANCE, OperatingMode.SIM]:
            with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
                cfg = configparser.ConfigParser()
                cfg.read(tmp_config)
                # Should not raise
                set_mode(cfg, mode, reason="test", confirmed_twice=False)


# ---------------------------------------------------------------------------
# Event timeline entry on transition
# ---------------------------------------------------------------------------

class TestModeTransitionEvent:
    def test_event_written_on_transition(self, tmp_config: Path):
        mock_logger = MagicMock()
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode._get_event_logger", return_value=mock_logger):
                cfg = configparser.ConfigParser()
                cfg.read(tmp_config)
                set_mode(cfg, OperatingMode.FIELD, reason="pre-sortie", confirmed_twice=False)

        mock_logger.log_action.assert_called_once()
        call_kwargs = mock_logger.log_action.call_args
        # First positional arg is the action name
        action = call_kwargs[0][0]
        assert action == "mode.transition"
        # Details dict
        details = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get("details", {})
        assert details["to"] == "FIELD"
        assert details["reason"] == "pre-sortie"

    def test_event_includes_from_and_to(self, tmp_config: Path):
        mock_logger = MagicMock()
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode._get_event_logger", return_value=mock_logger):
                cfg = configparser.ConfigParser()
                cfg.read(tmp_config)
                set_mode(cfg, OperatingMode.BENCH, reason="bench test", confirmed_twice=False)

        call_kwargs = mock_logger.log_action.call_args
        details = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get("details", {})
        assert details["from"] == "OBSERVE"
        assert details["to"] == "BENCH"

    def test_event_includes_actor(self, tmp_config: Path):
        mock_logger = MagicMock()
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode._get_event_logger", return_value=mock_logger):
                cfg = configparser.ConfigParser()
                cfg.read(tmp_config)
                set_mode(cfg, OperatingMode.FIELD, reason="test", actor="api")

        call_kwargs = mock_logger.log_action.call_args
        details = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get("details", {})
        assert "actor" in details

    def test_no_event_when_no_logger(self, tmp_config: Path):
        """set_mode works even when no event logger is registered."""
        with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode._get_event_logger", return_value=None):
                cfg = configparser.ConfigParser()
                cfg.read(tmp_config)
                # Should not raise
                set_mode(cfg, OperatingMode.MAINTENANCE, reason="maintenance window")

        cfg2 = configparser.ConfigParser()
        cfg2.read(tmp_config)
        assert cfg2.get("system", "mode") == "MAINTENANCE"


# ---------------------------------------------------------------------------
# API — GET /api/mode
# ---------------------------------------------------------------------------

class TestModeGetEndpoint:
    def test_get_returns_current_mode(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/mode")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "OBSERVE"

    def test_get_returns_updated_mode(self, client, tmp_config: Path):
        # Write FIELD into config
        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        cfg.set("system", "mode", "FIELD")
        with open(tmp_config, "w") as f:
            cfg.write(f)

        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/mode")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "FIELD"

    def test_get_requires_no_auth(self, client, tmp_config: Path):
        """GET /api/mode is read-only — no token required."""
        configure_auth("test-token")
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/mode")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API — POST /api/mode
# ---------------------------------------------------------------------------

class TestModePostEndpoint:
    def test_post_transitions_mode(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
                resp = client.post("/api/mode", json={
                    "mode": "FIELD",
                    "reason": "pre-sortie check",
                    "confirm": True,
                })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "FIELD"

    def test_post_armed_without_confirm_returns_400(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
                resp = client.post("/api/mode", json={
                    "mode": "ARMED",
                    "reason": "hot range",
                    "confirm": False,
                })
        assert resp.status_code == 400

    def test_post_armed_without_reason_returns_400(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
                resp = client.post("/api/mode", json={
                    "mode": "ARMED",
                    "confirm": True,
                })
        assert resp.status_code == 400

    def test_post_armed_with_confirm_and_reason_succeeds(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
                resp = client.post("/api/mode", json={
                    "mode": "ARMED",
                    "reason": "confirmed hot range",
                    "confirm": True,
                })
        assert resp.status_code == 200

    def test_post_bad_mode_returns_422(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/mode", json={
                "mode": "BOGUS_MODE",
                "reason": "test",
                "confirm": True,
            })
        assert resp.status_code == 422

    def test_post_missing_body_returns_400(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/mode", content=b"not json",
                               headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_post_persists_across_get(self, client, tmp_config: Path):
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            with patch("hydra_detect.operating_mode.get_config_path", return_value=tmp_config):
                client.post("/api/mode", json={
                    "mode": "BENCH",
                    "reason": "bench test session",
                    "confirm": True,
                })
        with patch("hydra_detect.web.mode_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/mode")
        assert resp.json()["mode"] == "BENCH"


# ---------------------------------------------------------------------------
# Factory reset → OBSERVE
# ---------------------------------------------------------------------------

class TestFactoryResetMode:
    def test_factory_config_has_observe(self, tmp_factory: Path):
        """Factory file must default to OBSERVE."""
        factory = Path(str(tmp_factory) + ".factory")
        cfg = configparser.ConfigParser()
        cfg.read(factory)
        assert cfg.get("system", "mode") == "OBSERVE"

    def test_restore_factory_resets_mode_to_observe(self, tmp_factory: Path):
        """After factory restore, mode reads as OBSERVE."""
        # First set mode to FIELD
        cfg = configparser.ConfigParser()
        cfg.read(tmp_factory)
        cfg.set("system", "mode", "FIELD")
        with open(tmp_factory, "w") as f:
            cfg.write(f)

        # Restore factory
        from hydra_detect.web.config_api import restore_factory
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_factory):
            result = restore_factory()

        assert result is True
        cfg2 = configparser.ConfigParser()
        cfg2.read(tmp_factory)
        assert cfg2.get("system", "mode") == "OBSERVE"
