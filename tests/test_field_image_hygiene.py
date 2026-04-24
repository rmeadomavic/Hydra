"""Field-image hygiene tests — issue #150.

Covers:
  1. Morale endpoint gating: beep returns 404 when morale_features_enabled=false (default).
  2. Morale endpoint accessible when morale_features_enabled=true.
  3. Config schema: [ui] morale_features_enabled defaults to false, accepts true/false.
  4. Snapshot: no 'DEMO VIZ' string in shipped source (excludes tests and archives).
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_morale_features
from hydra_detect.config_schema import SCHEMA, validate_config


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_morale_off():
    """Ensure morale features are disabled before and after each test."""
    configure_morale_features(False)
    yield
    configure_morale_features(False)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. Morale endpoints return 404 when disabled (default)
# ---------------------------------------------------------------------------

class TestMoraleEndpointsDisabled:
    """With morale_features_enabled = false, morale endpoints must return 404."""

    def test_beep_returns_404_when_disabled(self, client):
        resp = client.post("/api/vehicle/beep", json={"tune": "alert"})
        assert resp.status_code == 404, (
            f"Expected 404 for /api/vehicle/beep with morale disabled, got {resp.status_code}"
        )

    def test_beep_404_body_does_not_expose_feature(self, client):
        resp = client.post("/api/vehicle/beep", json={"tune": "alert"})
        assert resp.status_code == 404
        # Body should not reveal the feature exists (not a 403/disabled message)
        text = resp.text.lower()
        assert "morale" not in text
        assert "disabled" not in text


# ---------------------------------------------------------------------------
# 2. Morale endpoints accessible when enabled
# ---------------------------------------------------------------------------

class TestMoraleEndpointsEnabled:
    """With morale_features_enabled = true, morale endpoints must not return 404."""

    def test_beep_not_404_when_enabled(self, client):
        configure_morale_features(True)
        resp = client.post("/api/vehicle/beep", json={"tune": "alert"})
        # Will be 503 (MAVLink not connected in test env) or 200, but not 404
        assert resp.status_code != 404, (
            f"Expected non-404 for /api/vehicle/beep with morale enabled, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# 3. Config schema: [ui] section
# ---------------------------------------------------------------------------

class TestUiConfigSchema:
    def test_ui_section_exists_in_schema(self):
        assert "ui" in SCHEMA, "[ui] section missing from config SCHEMA"

    def test_morale_features_enabled_key_exists(self):
        assert "morale_features_enabled" in SCHEMA["ui"], (
            "morale_features_enabled key missing from [ui] schema"
        )

    def test_morale_features_enabled_defaults_false(self):
        spec = SCHEMA["ui"]["morale_features_enabled"]
        assert spec.default is False, (
            f"morale_features_enabled default must be False, got {spec.default}"
        )

    def test_morale_features_enabled_accepts_true(self):
        cfg = configparser.ConfigParser()
        cfg.add_section("ui")
        cfg.set("ui", "morale_features_enabled", "true")
        result = validate_config(cfg)
        errors = [e for e in result.errors if "morale_features_enabled" in e]
        assert not errors, f"Unexpected validation errors for true: {errors}"

    def test_morale_features_enabled_accepts_false(self):
        cfg = configparser.ConfigParser()
        cfg.add_section("ui")
        cfg.set("ui", "morale_features_enabled", "false")
        result = validate_config(cfg)
        errors = [e for e in result.errors if "morale_features_enabled" in e]
        assert not errors, f"Unexpected validation errors for false: {errors}"

    def test_morale_features_enabled_rejects_invalid(self):
        cfg = configparser.ConfigParser()
        cfg.add_section("ui")
        cfg.set("ui", "morale_features_enabled", "maybe")
        result = validate_config(cfg)
        errors = [e for e in result.errors if "morale_features_enabled" in e]
        assert errors, "Expected a validation error for 'maybe', got none"

    def test_missing_ui_section_does_not_error(self):
        """[ui] is optional — missing section should not produce errors specific to [ui]."""
        cfg = configparser.ConfigParser()
        # Don't add [ui]
        result = validate_config(cfg)
        errors = [e for e in result.errors if "[ui]" in e]
        assert not errors, f"Missing [ui] section produced [ui]-specific errors: {errors}"


# ---------------------------------------------------------------------------
# 4. Snapshot: no DEMO VIZ in shipped source
# ---------------------------------------------------------------------------

class TestNoDemoVizInSource:
    """DEMO VIZ must not appear in any shipped source file."""

    _EXCLUDED_PATTERNS = {
        "tests/",          # test files themselves
        ".archive/",       # archived old code
        "__pycache__/",
    }

    def _is_excluded(self, path: Path) -> bool:
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        return any(pat in rel for pat in self._EXCLUDED_PATTERNS)

    def test_no_demo_viz_in_source(self):
        """No DEMO VIZ, DEMO VIS, or DEMOVIZ in shipped files."""
        patterns = ["DEMO VIZ", "DEMO VIS", "DEMOVIZ"]
        violations: list[str] = []

        extensions = {".py", ".html", ".js", ".css", ".md", ".ini", ".txt"}
        for ext in extensions:
            for fpath in REPO_ROOT.rglob(f"*{ext}"):
                if self._is_excluded(fpath):
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for pat in patterns:
                    if pat in text:
                        rel = str(fpath.relative_to(REPO_ROOT))
                        violations.append(f"{rel}: contains '{pat}'")

        assert not violations, (
            "DEMO VIZ strings found in shipped source:\n" + "\n".join(violations)
        )
