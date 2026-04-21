"""Regression + visual-shell tests for the #config and #settings view redesigns.

These tests guard the Phase 2 mock-alignment pass on config.html + settings.html
+ config.css + settings.css while making sure the easter-egg power-user flow
(footer + rickroll) still survives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "hydra_detect" / "web"
CONFIG_HTML = WEB / "templates" / "config.html"
SETTINGS_HTML = WEB / "templates" / "settings.html"
CONFIG_CSS = WEB / "static" / "css" / "config.css"
SETTINGS_CSS = WEB / "static" / "css" / "settings.css"
SETTINGS_JS = WEB / "static" / "js" / "settings.js"

DEPRECATED_TOKENS = (
    "--radius-sm",
    "--radius-md",
    "--radius-lg",
    "--radius-xl",
    "--card-bg-gradient",
    "--text-tertiary",
)


@pytest.fixture(scope="module")
def config_html() -> str:
    return CONFIG_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_html() -> str:
    return SETTINGS_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def config_css() -> str:
    return CONFIG_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_css() -> str:
    return SETTINGS_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_js() -> str:
    return SETTINGS_JS.read_text(encoding="utf-8")


# ── config.html mock-shell markers ───────────────────────────────────────

def test_config_html_uses_mock_card_stack(config_html: str) -> None:
    """The operations-panels container carries the mock card-stack marker."""
    assert "mock-card-stack" in config_html


def test_config_html_panels_carry_mock_panel_class(config_html: str) -> None:
    """Every .panel block is tagged mock-panel so config.css can style it."""
    # 5 panels in the streamlined config.html (mission, detection, vehicle
    # mode, rf, outputs). Telemetry / pipeline-stats / target / detection-log
    # were dropped (live on Ops or the topbar). Each panel + its header +
    # body must carry a mock-* class so tests downstream can verify the
    # mock vocabulary took effect.
    assert config_html.count('class="panel mock-panel"') == 5
    assert config_html.count("mock-panel-head") == 5
    assert config_html.count("mock-panel-body") == 5


def test_config_html_titles_are_section_labels(config_html: str) -> None:
    """Panel titles wear the mock-section-label class (Barlow-Cond 0.18em)."""
    assert "mock-section-label" in config_html
    assert config_html.count("panel-title mock-section-label") == 5


def test_config_html_preserves_dynamic_ids(config_html: str) -> None:
    """Auto-render + save handlers target specific IDs — keep them intact."""
    for marker in (
        "operations-panels",
        "ctrl-thresh-slider",
        "ctrl-alert-class-list",
        "ctrl-rf-rssi-chart",
        "ctrl-mission-name",
        "ctrl-power-mode",
        "ctrl-rtsp-toggle",
        "ctrl-btn-loiter",
    ):
        assert f'id="{marker}"' in config_html, f"missing #{marker}"


# ── settings.html mock-shell + easter-egg preservation ───────────────────

def test_settings_html_preserves_power_footer(settings_html: str) -> None:
    """The #settings-power-footer + #settings-power-user ids are the receiver
    that settings.js toggles on the Logging section and that the click
    handler routes into the rickroll modal. Do NOT delete these."""
    assert 'id="settings-power-footer"' in settings_html
    assert 'id="settings-power-user"' in settings_html
    assert "Power User Options" in settings_html


def test_settings_html_uses_mock_tweaks_content(settings_html: str) -> None:
    """The right-column carries mock-tweaks-content and surfaces a
    mock-style panel head (Settings / config.ini editor)."""
    assert "mock-tweaks-content" in settings_html
    assert "mock-panel-head" in settings_html
    assert "mock-panel-ttl" in settings_html


def test_settings_html_form_is_mock_tweaks_card(settings_html: str) -> None:
    """The dynamic form container keeps its id AND picks up the mock card."""
    assert 'id="settings-form"' in settings_html
    assert "mock-tweaks-card" in settings_html


def test_settings_html_section_labels_are_mock(settings_html: str) -> None:
    """Recovery Tools + Quick Links + Tweaks labels use mock-section-label."""
    # 3 occurrences: Tweaks nav label, Recovery Tools, Quick Links.
    assert settings_html.count("mock-section-label") >= 3


def test_settings_js_still_has_rickroll(settings_js: str) -> None:
    """Regression guard for the Power User easter egg — commit to the bit."""
    assert "youtube-nocookie" in settings_js
    assert "dQw4w9WgXcQ" in settings_js
    assert "settings-power-user" in settings_js


def test_settings_js_still_gates_power_footer_to_logging(settings_js: str) -> None:
    """Logging-section-only visibility rule for the power-user footer."""
    assert "settings-power-footer" in settings_js
    # The string literal 'logging' appears in the visibility check.
    assert "'logging'" in settings_js or '"logging"' in settings_js


# ── CSS token audit (no legacy tokens should survive) ───────────────────

@pytest.mark.parametrize("token", DEPRECATED_TOKENS)
def test_config_css_is_free_of_deprecated_tokens(
    config_css: str, token: str
) -> None:
    assert token not in config_css, f"config.css still uses {token}"


@pytest.mark.parametrize("token", DEPRECATED_TOKENS)
def test_settings_css_is_free_of_deprecated_tokens(
    settings_css: str, token: str
) -> None:
    assert token not in settings_css, f"settings.css still uses {token}"


# ── CSS mock-vocabulary smoke checks ────────────────────────────────────

def test_config_css_applies_mock_panel_gradient(config_css: str) -> None:
    """Mock panel cards use the 180deg vertical gradient from the reference
    tweaks-panel + TAK column heads (#0f1214 → #0a0d10)."""
    assert "linear-gradient(180deg, #0f1214, #0a0d10)" in config_css


def test_config_css_panel_head_padding_matches_mock(config_css: str) -> None:
    """Mock panel head is 10px 14px with a 1px border-default bottom."""
    assert "padding: 10px 14px" in config_css
    assert "border-bottom: 1px solid var(--border-default)" in config_css


def test_settings_css_section_label_typography(settings_css: str) -> None:
    """Section labels match mock: Barlow Condensed 10px / 0.18em uppercase."""
    assert ".mock-section-label" in settings_css
    assert "letter-spacing: 0.18em" in settings_css
    assert "var(--font-cond)" in settings_css


def test_settings_css_uses_flat_radius_token(settings_css: str) -> None:
    """Flat 2px radii — all radii must go through var(--radius)."""
    assert "var(--radius)" in settings_css


def test_settings_css_tweaks_card_uses_mock_gradient(settings_css: str) -> None:
    assert "linear-gradient(180deg, #0f1214, #0a0d10)" in settings_css


def test_settings_css_preserves_form_scaffolding(settings_css: str) -> None:
    """Form grid + schema slider rules must survive the mock rewrite so
    settings.js auto-render keeps producing usable fields."""
    assert ".slider-container" in settings_css
    assert ".settings-field" in settings_css
    assert ".settings-section-btn" in settings_css
    assert ".settings-actions" in settings_css
