"""Tests for the theme system (locked to Lattice).

Originally supported ops/nvg/lattice with a settings picker. The picker
was removed when the dashboard was permanently locked to Lattice — these
tests now assert the lock-down invariants:

- variables.css still declares the lattice theme selector + tokens
- settings.html does NOT render a theme picker
- config_schema lists only "lattice" as a valid choice
- settings.js always applies lattice, ignores incoming values
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
BASE_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "base.html"
SETTINGS_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "settings.html"
SETTINGS_JS = REPO_ROOT / "hydra_detect" / "web" / "static" / "js" / "settings.js"


@pytest.fixture
def client():
    return TestClient(app)


class TestVariablesCssLattice:
    def test_css_has_lattice_theme_selector(self):
        css = CSS_PATH.read_text()
        assert ':root[data-theme="lattice"]' in css

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


class TestBaseHtmlLocksTheme:
    def test_html_root_has_lattice_attribute(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        # <html lang="en" data-theme="lattice"> locks the theme at load
        # time, before any JS runs — no flash of non-lattice.
        assert 'data-theme="lattice"' in html


class TestSettingsHtmlNoPicker:
    def test_theme_picker_not_rendered(self, client):
        resp = client.get("/")
        html = resp.text
        # Picker removed. None of the old hooks should be in the DOM.
        assert 'id="settings-theme-picker"' not in html
        assert 'name="settings-theme"' not in html
        assert 'data-theme-option' not in html

    def test_settings_html_source_is_clean(self):
        html = SETTINGS_HTML.read_text()
        assert 'settings-theme-picker' not in html
        assert 'settings-theme-grid' not in html
        assert 'data-theme-option' not in html


class TestConfigSchemaThemeLockedToLattice:
    def test_theme_field_locked_to_lattice(self):
        assert "theme" in SCHEMA["web"]
        spec = SCHEMA["web"]["theme"]
        assert spec.type is FieldType.ENUM
        assert spec.default == "lattice"
        assert set(spec.choices) == {"lattice"}

    def test_theme_validation_accepts_lattice(self):
        import configparser
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.add_section("web")
        cfg.set("web", "theme", "lattice")
        result = validate_config(cfg)
        web_errors = [e for e in result.errors
                      if "[web]" in e and "theme" in e]
        assert not web_errors

    def test_theme_validation_rejects_removed_choices(self):
        import configparser
        for removed in ("ops", "nvg", "hot_pink"):
            cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
            cfg.add_section("web")
            cfg.set("web", "theme", removed)
            result = validate_config(cfg)
            theme_errors = [e for e in result.errors
                            if "[web]" in e and "theme" in e]
            assert theme_errors, (
                f"theme={removed} should be rejected after lock-down"
            )


class TestSettingsJsAppliesLattice:
    def test_js_theme_choices_collapsed_to_lattice(self):
        js = SETTINGS_JS.read_text()
        assert "THEME_CHOICES" in js
        # Only 'lattice' remains; 'ops' and 'nvg' must not appear as
        # choices anywhere in the module.
        assert "'lattice'" in js
        assert "'ops'" not in js
        assert "'nvg'" not in js

    def test_js_applyTheme_forces_lattice(self):
        js = SETTINGS_JS.read_text()
        # The applyTheme function should unconditionally set lattice.
        assert "setAttribute('data-theme', 'lattice')" in js

    def test_js_theme_picker_init_is_noop(self):
        js = SETTINGS_JS.read_text()
        # initThemePicker should no longer wire a change listener.
        assert "addEventListener('change'" not in js or \
               "settings-theme-grid" not in js


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
