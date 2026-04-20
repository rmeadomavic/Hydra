"""Tests for the Ops / NVG / Lattice theme system.

Covers:
- variables.css declares all three theme selectors with token overrides
- settings.html renders three theme radio cards with swatches
- config_schema validates theme in {ops, nvg, lattice}
- settings.js wires the <html data-theme="..."> attribute and respects
  the existing save pattern (POST /api/config/full)
- regression: hud_layout is still in config_schema (PRESERVATION rule)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.config_schema import SCHEMA, FieldType, validate_config
from hydra_detect.web.server import app


REPO_ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "variables.css"
SETTINGS_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "settings.html"
SETTINGS_JS = REPO_ROOT / "hydra_detect" / "web" / "static" / "js" / "settings.js"


@pytest.fixture
def client():
    return TestClient(app)


class TestVariablesCssThemes:
    def test_css_has_all_three_theme_selectors(self):
        css = CSS_PATH.read_text()
        assert ':root[data-theme="nvg"]' in css
        assert ':root[data-theme="lattice"]' in css

    def test_nvg_theme_overrides_required_variables(self):
        css = CSS_PATH.read_text()
        start = css.index(':root[data-theme="nvg"]')
        end = css.index("}", start)
        block = css[start:end]
        # Task contract: NVG must override these tokens into monochrome green
        for var in (
            "--olive-primary",
            "--olive-muted",
            "--ogt-muted",
            "--text-primary",
            "--text-data",
            "--bg-panel",
            "--bg-panel-alt",
            "--bg-void",
            "--danger",
            "--warning",
            "--info",
            "--gold",
        ):
            assert var in block, f"NVG block missing {var}"

    def test_nvg_accent_is_monochrome_green(self):
        css = CSS_PATH.read_text()
        start = css.index(':root[data-theme="nvg"]')
        end = css.index("}", start)
        block = css[start:end]
        # tokens.js THEMES.nvg.accent = '#00ff41'
        assert "#00ff41" in block

    def test_lattice_theme_mirrors_tokens_js(self):
        css = CSS_PATH.read_text()
        start = css.index(':root[data-theme="lattice"]')
        end = css.index("}", start)
        block = css[start:end]
        # tokens.js THEMES.lattice: panel=#14161a, card=#1d2026,
        # border=#2a2e36, text=#dfe3eb, accent=#A6BC92
        assert "#14161a" in block
        assert "#1d2026" in block
        assert "#2a2e36" in block
        assert "#dfe3eb" in block
        assert "#A6BC92" in block


class TestSettingsHtmlThemePicker:
    def test_all_three_theme_radios_present(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'data-theme-option="ops"' in html
        assert 'data-theme-option="nvg"' in html
        assert 'data-theme-option="lattice"' in html

    def test_theme_picker_has_radio_inputs(self, client):
        resp = client.get("/")
        html = resp.text
        # Three <input type="radio" name="settings-theme" ...>
        assert html.count('name="settings-theme"') == 3
        for value in ('value="ops"', 'value="nvg"', 'value="lattice"'):
            assert value in html

    def test_theme_picker_renders_before_form_container(self, client):
        resp = client.get("/")
        html = resp.text
        picker_idx = html.index('id="settings-theme-picker"')
        form_idx = html.index('id="settings-form"')
        assert picker_idx < form_idx


class TestConfigSchemaTheme:
    def test_theme_field_exists(self):
        assert "theme" in SCHEMA["web"]
        spec = SCHEMA["web"]["theme"]
        assert spec.type is FieldType.ENUM
        assert spec.default == "ops"
        assert set(spec.choices) == {"ops", "nvg", "lattice"}

    def test_theme_validation_accepts_all_three(self):
        import configparser
        for choice in ("ops", "nvg", "lattice"):
            cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
            cfg.add_section("web")
            cfg.set("web", "theme", choice)
            result = validate_config(cfg)
            web_errors = [e for e in result.errors
                          if "[web]" in e and "theme" in e]
            assert not web_errors, (
                f"theme={choice} rejected: {web_errors}"
            )

    def test_theme_validation_rejects_unknown(self):
        import configparser
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.add_section("web")
        cfg.set("web", "theme", "hot_pink")
        result = validate_config(cfg)
        theme_errors = [e for e in result.errors
                        if "[web]" in e and "theme" in e]
        assert theme_errors


class TestSettingsJsThemeWiring:
    def test_js_sets_html_data_theme(self):
        js = SETTINGS_JS.read_text()
        assert "document.documentElement" in js
        assert "setAttribute('data-theme'" in js
        assert "removeAttribute('data-theme')" in js

    def test_js_posts_theme_to_config_api(self):
        js = SETTINGS_JS.read_text()
        assert "/api/config/full" in js
        assert "theme" in js

    def test_js_has_all_three_theme_choices(self):
        js = SETTINGS_JS.read_text()
        assert "THEME_CHOICES" in js
        assert "'ops'" in js and "'nvg'" in js and "'lattice'" in js


class TestPreservationHudLayoutIntact:
    """Regression: the preservation rules mark hud_layout as untouchable.
    This test exists to fail loudly if a future refactor removes it."""

    def test_hud_layout_still_in_schema(self):
        assert "hud_layout" in SCHEMA["web"]
        spec = SCHEMA["web"]["hud_layout"]
        assert spec.type is FieldType.ENUM
        assert spec.default == "classic"
        assert set(spec.choices) == {
            "classic", "operator", "graphs", "hybrid",
        }
