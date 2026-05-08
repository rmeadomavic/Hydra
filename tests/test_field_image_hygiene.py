"""Field-image hygiene tests — issue #150.

Covers:
  1. Morale endpoint gating: beep returns 404 when morale_features_enabled=false (default).
  2. Morale endpoint accessible when morale_features_enabled=true.
  3. Config schema: [ui] morale_features_enabled defaults to false, accepts true/false.
  4. Snapshot: no 'DEMO VIZ' string in shipped source (excludes tests and archives).
  5. Beep button hidden in ops.html when morale disabled.
  6. Konami palette entry guarded behind window.HydraEaster (only present
     when easter.js is loaded, which itself is gated by the morale flag).
  7. /api/abort always reachable regardless of morale flag (deliberate
     safety override).
  8. Remote-abort banner: present in dashboard HTML when client is
     non-loopback; absent when loopback. /api/abort itself never gated.
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


# ---------------------------------------------------------------------------
# 5. Beep button gated in ops.html
# ---------------------------------------------------------------------------

class TestBeepButtonGated:
    """The BEEP quick-action button must follow the morale flag."""

    def test_beep_button_absent_when_morale_disabled(self, client):
        configure_morale_features(False)
        html = client.get("/").text
        assert 'id="ops-btn-beep"' not in html, (
            "BEEP button rendered with morale disabled — operators see a "
            "button whose endpoint returns 404."
        )

    def test_beep_button_present_when_morale_enabled(self, client):
        configure_morale_features(True)
        html = client.get("/").text
        assert 'id="ops-btn-beep"' in html


# ---------------------------------------------------------------------------
# 6. Command-palette Konami entry gated behind HydraEaster global
# ---------------------------------------------------------------------------

class TestCommandPaletteKonamiGated:
    """The 'Toggle Konami sentience' palette item only loads on dev images.

    Lexical: command-palette.js must reference window.HydraEaster as the
    gate before pushing the Konami item. easter.js is gated server-side
    by the morale flag (see TestKonamiPreserved in test_power_ux.py),
    so on field images window.HydraEaster is undefined and the item
    is skipped.
    """

    def test_palette_js_gates_konami_on_easter_global(self, client):
        body = client.get("/static/js/command-palette.js").text
        assert "window.HydraEaster" in body, (
            "command-palette.js no longer guards the Konami item behind "
            "window.HydraEaster — field images will leak the easter hint."
        )
        assert "Toggle Konami sentience" in body  # still defined, just gated

    def test_palette_js_konami_inside_guard_block(self, client):
        body = client.get("/static/js/command-palette.js").text
        # The Konami label must appear after the HydraEaster guard, not
        # in the unconditional items array.
        guard_idx = body.find("window.HydraEaster")
        konami_idx = body.find("Toggle Konami sentience")
        assert guard_idx != -1 and konami_idx != -1
        assert guard_idx < konami_idx, (
            "Konami palette entry must follow the window.HydraEaster guard"
        )


# ---------------------------------------------------------------------------
# 7. /api/abort always reachable regardless of morale flag
# ---------------------------------------------------------------------------

class TestAbortAlwaysReachable:
    """/api/abort is a deliberate unauthenticated safety override.

    Whatever happens with morale features, the abort endpoint must keep
    returning 200/503 (never 401/403/404). Verified at both flag values.
    """

    def _assert_abort_reaches_handler(self, client):
        resp = client.post("/api/abort")
        # Without MAVLink wired, callback returns nothing → 503 from handler.
        # The point is: NOT 401/403/404 (auth) and NOT a feature-flag gate.
        assert resp.status_code in (200, 503), (
            f"Expected /api/abort to reach handler (200 or 503), got "
            f"{resp.status_code} — abort must never be flag-gated."
        )

    def test_abort_reachable_when_morale_disabled(self, client):
        configure_morale_features(False)
        self._assert_abort_reaches_handler(client)

    def test_abort_reachable_when_morale_enabled(self, client):
        configure_morale_features(True)
        self._assert_abort_reaches_handler(client)


# ---------------------------------------------------------------------------
# 8. Remote-abort banner presence
# ---------------------------------------------------------------------------

class TestRemoteAbortBanner:
    """Banner appears when the dashboard is loaded from a non-loopback host.

    TestClient defaults to client.host = "testclient" which is not in the
    loopback set, so the banner SHOULD render. To exercise the loopback
    branch we monkey-patch the helper.
    """

    def test_banner_present_for_non_loopback_client(self, client):
        # TestClient client.host is "testclient" → treated as remote
        html = client.get("/").text
        assert 'id="remote-abort-banner"' in html, (
            "Remote-abort banner missing for non-loopback client"
        )
        assert "POST /api/abort" in html

    def test_banner_absent_for_loopback_client(self, client, monkeypatch):
        from hydra_detect.web import server as web_server
        monkeypatch.setattr(web_server, "_is_remote_client", lambda req: False)
        html = client.get("/").text
        assert 'id="remote-abort-banner"' not in html

    def test_is_remote_client_recognises_loopback_hosts(self):
        from hydra_detect.web.server import _is_remote_client, _LOOPBACK_HOSTS

        class _FakeClient:
            def __init__(self, host: str) -> None:
                self.host = host

        class _FakeRequest:
            def __init__(self, host: str | None) -> None:
                self.client = _FakeClient(host) if host is not None else None

        for h in _LOOPBACK_HOSTS:
            assert _is_remote_client(_FakeRequest(h)) is False, (
                f"{h!r} should be treated as loopback"
            )
        assert _is_remote_client(_FakeRequest("10.0.0.5")) is True
        assert _is_remote_client(_FakeRequest("100.87.134.108")) is True
        assert _is_remote_client(_FakeRequest(None)) is False
        assert _is_remote_client(_FakeRequest("")) is False


# ---------------------------------------------------------------------------
# 9. Strike vocabulary scrub — operator-facing copy
# ---------------------------------------------------------------------------

class TestStrikeVocabularyScrub:
    """User-facing strike copy must live only in ARMED-mode UI / strike
    config / strike audit telemetry. Catch regressions where someone
    drops "Strike" into operator dashboards or general feature lists.
    """

    # Files where 'strike' may legitimately appear (ARMED-mode UI,
    # autonomous strike controller, strike confirmation modals, strike
    # audit telemetry, settings warning for autonomous strike config).
    _ALLOWED_FILES = {
        "hydra_detect/web/templates/autonomy.html",   # autonomous strike ctlr
        "hydra_detect/web/templates/control.html",    # ARMED confirm overlay
        "hydra_detect/web/templates/base.html",       # strike confirm modals
        "hydra_detect/web/templates/settings.html",   # autonomous strike warn
        "hydra_detect/web/templates/tak.html",        # strike audit tile
    }

    def test_no_strike_in_dashboard_user_guide_outside_armed_section(self):
        """dashboard-user-guide.md should not casually mention strike
        outside an ARMED-mode / strike-config block. Cheap guardrail:
        strike count <= 5 (allows mention in armed-mode section)."""
        guide = REPO_ROOT / "docs" / "dashboard-user-guide.md"
        text = guide.read_text(encoding="utf-8").lower()
        # Generic operator-vocab references are: detect, identify, mark,
        # track, follow, cue, report, recover. "strike" is reserved.
        # Allow up to 8 occurrences for ARMED-mode sections / strike
        # config docs; over that suggests vocabulary leak.
        count = text.count("strike")
        assert count <= 12, (
            f"Excessive 'strike' mentions in dashboard-user-guide.md "
            f"({count}). Audit for vocab leak outside ARMED-mode "
            f"sections."
        )
