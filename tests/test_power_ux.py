"""Smoke tests for the power-user UX surface.

Covers:
  (a) /static/js/keybinds.js is served and contains the required markers.
  (b) /static/js/command-palette.js is served and references every source.
  (c) base.html includes both new <script src="…"> tags.
  (d) base.html contains both new overlay divs.
  (e) base.css has styling blocks for both overlays.
  (f) Easter-egg regression — easter.js tag + #sentience-overlay preserved
      when morale_features_enabled = true.

Lexical / HTTP-level only. No headless JS runner.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_morale_features


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "base.html"
BASE_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "base.css"


@pytest.fixture
def client():
    return TestClient(app)


# ── (a) keybinds.js served + markers ────────────────────────────────────────

class TestKeybindsJsServed:
    def test_keybinds_js_200(self, client):
        resp = client.get("/static/js/keybinds.js")
        assert resp.status_code == 200

    def test_keybinds_js_exposes_global(self, client):
        body = client.get("/static/js/keybinds.js").text
        assert "HydraKeybinds" in body
        assert "attached: true" in body

    def test_keybinds_js_references_switch_view(self, client):
        body = client.get("/static/js/keybinds.js").text
        assert "switchView" in body

    def test_keybinds_js_skips_input_focus(self, client):
        body = client.get("/static/js/keybinds.js").text
        assert "INPUT" in body and "TEXTAREA" in body and "SELECT" in body

    def test_keybinds_js_lists_top_level_views(self, client):
        """After 6→4 streamline, only the surviving top-level tabs are
        in the keybinds VIEW_ORDER. autonomy/systems were folded into
        config/settings respectively."""
        body = client.get("/static/js/keybinds.js").text
        for v in ("ops", "tak", "config", "settings"):
            assert f"'{v}'" in body
        for gone in ("'systems'", "'autonomy'"):
            assert gone not in body, f"{gone} should be dropped from keybinds VIEW_ORDER"

    def test_keybinds_js_does_not_swallow_arrow_keys(self, client):
        body = client.get("/static/js/keybinds.js").text
        assert "ArrowUp" not in body
        assert "ArrowDown" not in body


# ── (b) command-palette.js served + markers ─────────────────────────────────

class TestCommandPaletteJsServed:
    def test_palette_js_200(self, client):
        resp = client.get("/static/js/command-palette.js")
        assert resp.status_code == 200

    def test_palette_js_exposes_global(self, client):
        body = client.get("/static/js/command-palette.js").text
        assert "HydraCommandPalette" in body
        assert "attached: true" in body

    def test_palette_js_mentions_ctrl_k(self, client):
        body = client.get("/static/js/command-palette.js").text
        assert "Ctrl+K" in body or "Ctrl" in body
        assert "metaKey" in body and "ctrlKey" in body

    def test_palette_js_sources_tracks_and_peers(self, client):
        body = client.get("/static/js/command-palette.js").text
        assert "tracks" in body
        assert "peers" in body

    def test_palette_js_has_fuzzy_substring_filter(self, client):
        body = client.get("/static/js/command-palette.js").text
        assert "includes(" in body


# ── (c) base.html includes both script tags ─────────────────────────────────

class TestBaseHtmlScriptsIncluded:
    def test_keybinds_script_tag_present(self, client):
        html = client.get("/").text
        assert '/static/js/keybinds.js' in html

    def test_palette_script_tag_present(self, client):
        html = client.get("/").text
        assert '/static/js/command-palette.js' in html

    def test_load_order_easter_then_keybinds_then_palette_then_main(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        i_easter = html.find('/static/js/easter.js')
        i_keybinds = html.find('/static/js/keybinds.js')
        i_palette = html.find('/static/js/command-palette.js')
        i_main = html.find('/static/js/main.js')
        assert i_easter != -1 and i_keybinds != -1 and i_palette != -1 and i_main != -1
        assert i_easter < i_keybinds < i_palette < i_main


# ── (d) base.html has both overlay divs ─────────────────────────────────────

class TestBaseHtmlOverlaysPresent:
    def test_keybinds_help_overlay(self, client):
        html = client.get("/").text
        assert 'id="keybinds-help"' in html

    def test_command_palette_overlay(self, client):
        html = client.get("/").text
        assert 'id="hydra-command-palette"' in html
        assert 'id="hydra-command-palette-input"' in html
        assert 'id="hydra-command-palette-list"' in html

    def test_overlays_start_hidden(self, client):
        html = client.get("/").text
        snippet_help = html.split('id="keybinds-help"')[1].split('>')[0]
        snippet_cmd = html.split('id="hydra-command-palette"')[1].split('>')[0]
        assert 'display:none' in snippet_help
        assert 'display:none' in snippet_cmd


# ── (e) base.css has styling for both overlays ──────────────────────────────

class TestBaseCssBlocks:
    def test_keybinds_help_styled(self):
        css = BASE_CSS.read_text(encoding="utf-8")
        assert '#keybinds-help' in css
        assert '.keybinds-card' in css
        assert '.keybinds-kbd' in css

    def test_command_palette_styled(self):
        css = BASE_CSS.read_text(encoding="utf-8")
        assert '#hydra-command-palette' in css
        assert '.cmd-palette-card' in css
        assert '.cmd-palette-input' in css
        assert '.cmd-palette-row' in css

    def test_animated_fade_in(self):
        css = BASE_CSS.read_text(encoding="utf-8")
        assert '80ms' in css


# ── (f) Konami / easter regression ──────────────────────────────────────────

class TestKonamiPreserved:
    def test_easter_script_tag_present_when_morale_enabled(self, client):
        configure_morale_features(True)
        html = client.get("/").text
        configure_morale_features(False)
        assert '/static/js/easter.js' in html

    def test_easter_script_tag_absent_when_morale_disabled(self, client):
        configure_morale_features(False)
        html = client.get("/").text
        assert '/static/js/easter.js' not in html

    def test_sentience_overlay_div_present_when_morale_enabled(self, client):
        configure_morale_features(True)
        html = client.get("/").text
        configure_morale_features(False)
        assert 'id="sentience-overlay"' in html
        assert 'id="sentience-terminal"' in html

    def test_sentience_overlay_div_absent_when_morale_disabled(self, client):
        configure_morale_features(False)
        html = client.get("/").text
        assert 'id="sentience-overlay"' not in html

    def test_keybinds_does_not_override_arrow_keys(self, client):
        # Defense in depth: keybinds.js must not match arrow keys at all.
        body = client.get("/static/js/keybinds.js").text
        assert 'Arrow' not in body
