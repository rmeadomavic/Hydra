"""Smoke tests for the FlightHUD rail + Cockpit strip on #ops.

Covers:
  (a) ops.html contains #ops-flight-hud + #ops-cockpit-strip elements.
  (b) ops.js exports updateFlightHud + updateCockpitStrip.
  (c) Layout picker emits the four valid hud_layout values
      (classic / operator / graphs / hybrid).
  (d) Regression — right-sidebar cards still present
      (#ops-rf-section, #ops-mission-section, #ops-pipeline-section).
  (e) Regression — approach section + abort button still wired.
  (f) Regression — radial menu, confirm overlay, SIM GPS sink, lock-info,
      and approach-panel updaters still intact.

Lexical/HTML checks only — no headless browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


REPO_ROOT = Path(__file__).resolve().parents[1]
OPS_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "ops.html"
OPS_JS = REPO_ROOT / "hydra_detect" / "web" / "static" / "js" / "ops.js"
OPS_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "ops.css"
FLIGHT_HUD_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "flight-hud.css"
COCKPIT_STRIP_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "cockpit-strip.css"


@pytest.fixture
def client():
    return TestClient(app)


# ── (a) New zone elements present in ops.html ──

class TestNewZoneElements:
    def test_flight_hud_root_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-flight-hud"' in html
        assert 'class="flight-hud"' in html

    def test_flight_hud_subzones_present(self):
        html = OPS_HTML.read_text()
        # HDG tape, VTapes, ReadoutCards, gimbal, target, status
        for needle in (
            'id="ops-fhud-hdg-svg"',
            'id="ops-fhud-spd-svg"',
            'id="ops-fhud-alt-svg"',
            'id="ops-fhud-card-batt"',
            'id="ops-fhud-card-link"',
            'id="ops-fhud-card-pos"',
            'id="ops-fhud-card-gps"',
            'id="ops-fhud-gimbal"',
            'id="ops-fhud-target"',
            'id="ops-fhud-status"',
        ):
            assert needle in html, f"ops.html should contain {needle}"

    def test_cockpit_strip_root_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-cockpit-strip"' in html
        assert 'class="cockpit-strip"' in html

    def test_cockpit_strip_three_cells(self):
        html = OPS_HTML.read_text()
        # Cell 2 (TAK) migrated from SVG radar to Leaflet map in 2a8f48f —
        # `ops-cockpit-tak-map` div is now the render target.
        for needle in (
            'id="ops-cockpit-servo"',
            'id="ops-cockpit-tak"',
            'id="ops-cockpit-sdr"',
            'id="ops-cockpit-servo-svg"',
            'id="ops-cockpit-tak-map"',
            'id="ops-cockpit-sdr-spectrum"',
            'id="ops-cockpit-sdr-list"',
        ):
            assert needle in html, f"ops.html should contain {needle}"

    def test_outer_grid_uses_mock_template(self):
        css = OPS_CSS.read_text()
        # Mock spec after a4effd8 widened bounds with minmax() to stop
        # the grid blowing out on narrower viewports.
        assert "grid-template-columns: auto minmax(0, 1fr) minmax(280px, 360px)" in css
        assert "grid-template-rows: minmax(0, 1fr) 220px" in css


# ── (b) ops.js exports updateFlightHud + updateCockpitStrip ──

class TestOpsJsExports:
    def test_exports_include_new_updaters(self):
        src = OPS_JS.read_text()
        for name in (
            "updateFlightHud: updateFlightHud",
            "updateCockpitStrip: updateCockpitStrip",
            "applyHudLayout: applyHudLayout",
        ):
            assert name in src, f"ops.js should export {name!r}"

    def test_update_hud_calls_flight_hud(self):
        src = OPS_JS.read_text()
        # Must be invoked from updateHUD each tick
        assert "updateFlightHud(stats)" in src

    def test_aux_zones_polled_on_enter(self):
        src = OPS_JS.read_text()
        # New 1Hz aux poller and 700ms SDR ticker registered in onEnter
        assert "setInterval(refreshAuxZones, 1000)" in src
        assert "setInterval(animateSdrSpectrum, 700)" in src
        # And torn down in onLeave
        assert "if (auxTimer)" in src
        assert "if (sdrTickTimer)" in src


# ── (c) Layout picker emits all four valid hud_layout values ──

class TestLayoutPickerValues:
    def test_picker_offers_four_layouts(self):
        html = OPS_HTML.read_text()
        for layout in ("classic", "operator", "graphs", "hybrid"):
            assert (
                f'value="{layout}"' in html
            ), f"layout picker should offer {layout}"

    def test_picker_writes_web_section(self):
        src = OPS_JS.read_text()
        # POST shape matches /api/config/full nested-dict contract
        assert "/api/config/full" in src
        assert "{ web: { hud_layout: layout } }" in src

    def test_picker_layouts_match_schema_choices(self):
        from hydra_detect.config_schema import SCHEMA
        spec = SCHEMA["web"]["hud_layout"]
        assert spec.choices == ["classic", "operator", "graphs", "hybrid"]
        html = OPS_HTML.read_text()
        for choice in spec.choices:
            assert f'value="{choice}"' in html

    def test_picker_loaded_from_config(self):
        src = OPS_JS.read_text()
        assert "loadHudLayoutFromConfig" in src
        assert "cfg.web.hud_layout" in src


# ── (d) Right-sidebar cards still present (regression) ──

class TestSidebarCardsPreserved:
    def test_rf_section_preserved(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-rf-section"' in html
        assert 'id="ops-rf-rssi"' in html

    def test_mission_section_preserved(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-mission-section"' in html
        assert 'id="ops-mission-elapsed"' in html

    def test_pipeline_section_preserved(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-pipeline-section"' in html
        assert 'id="ops-pipeline-fps"' in html

    def test_detlog_and_vehicle_preserved(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-detlog-section"' in html
        assert 'id="ops-vehicle-info"' in html
        # SIM GPS sink ID
        assert 'id="ops-info-position"' in html

    def test_sidebar_export_handlers_present(self):
        src = OPS_JS.read_text()
        for fn in (
            "updateSidebarRF",
            "updateSidebarMission",
            "updateSidebarPipeline",
            "updateSidebarDetLog",
        ):
            assert fn in src


# ── (e) Approach panel + button still wired ──

class TestApproachPanelPreserved:
    def test_approach_section_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-approach-section"' in html
        assert 'id="ops-btn-approach-abort"' in html
        assert 'id="ops-approach-mode"' in html

    def test_approach_handlers_still_wired(self):
        src = OPS_JS.read_text()
        assert "approachAbortBtn.addEventListener" in src
        assert "abortApproach()" in src

    def test_approach_panel_updater_called(self):
        src = OPS_JS.read_text()
        assert "updateApproachPanel(stats)" in src


# ── (f) Radial menu + confirm overlay + SIM GPS + lock-info intact ──

class TestProtectedCodePathsIntact:
    def test_radial_menu_handlers_present(self):
        src = OPS_JS.read_text()
        assert "function showContextMenu" in src
        assert "function hideContextMenu" in src
        assert "ops-radial-menu" in src

    def test_confirm_overlay_handlers_present(self):
        src = OPS_JS.read_text()
        assert "function showConfirmOverlay" in src
        assert "function hideConfirmOverlay" in src
        assert "ops-confirm-overlay" in src

    def test_sim_gps_sink_still_referenced(self):
        src = OPS_JS.read_text()
        assert "window.HydraSimGps.withSimSuffix(stats.position" in src

    def test_lock_overlay_updater_called(self):
        src = OPS_JS.read_text()
        assert "updateLockInfo(HydraApp.state.target)" in src

    def test_double_click_fullscreen_preserved(self):
        src = OPS_JS.read_text()
        # Easter-egg-adjacent feature noted in PRESERVATION_RULES.md
        assert "addEventListener('dblclick'" in src
        assert "requestFullscreen" in src


# ── New CSS files served + key class names present ──

class TestNewCssAssets:
    def test_flight_hud_css_served(self, client):
        resp = client.get("/static/css/flight-hud.css")
        assert resp.status_code == 200
        body = resp.text
        for cls in (".flight-hud", ".flight-hud-hdg", ".flight-hud-vtape",
                    ".flight-hud-card", ".flight-hud-gimbal",
                    ".flight-hud-target", ".flight-hud-status"):
            assert cls in body

    def test_cockpit_strip_css_served(self, client):
        resp = client.get("/static/css/cockpit-strip.css")
        assert resp.status_code == 200
        body = resp.text
        for cls in (".cockpit-strip", ".cockpit-cell-servo",
                    ".cockpit-cell-tak", ".cockpit-cell-sdr",
                    ".cockpit-sdr-list", ".cockpit-tak-self-ring"):
            assert cls in body

    def test_ops_css_imports_new_sheets(self):
        css = OPS_CSS.read_text()
        assert "flight-hud.css" in css
        assert "cockpit-strip.css" in css

    def test_flight_hud_layout_visibility_rules(self):
        # Each of the four layouts gates a specific subset of bodies
        css = FLIGHT_HUD_CSS.read_text()
        for layout in ("classic", "operator", "graphs", "hybrid"):
            assert f'data-hud-layout="{layout}"' in css
