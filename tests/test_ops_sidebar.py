"""Smoke tests for the composite operator-station sidebar on #ops.

Covers:
  a) ops.html contains each new sidebar card section ID
  b) ops.js exposes the expected sidebar-update functions
  c) Sidebar update functions handle missing data without throwing
     (verified lexically: the functions include null-guards and
     the DOM is resilient to undefined state)
  d) Approach panel continues to work — regression guard that the
     previously shipped #ops-approach-section + abort wiring survives
     the sidebar restack.

Lexical/HTML presence is enough; no headless JS runtime here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


REPO_ROOT = Path(__file__).resolve().parents[1]
OPS_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "ops.html"
OPS_JS = REPO_ROOT / "hydra_detect" / "web" / "static" / "js" / "ops.js"
OPS_SIDEBAR_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "ops-sidebar.css"


@pytest.fixture
def client():
    return TestClient(app)


# ── (a) ops.html contains each new sidebar card ID ──

class TestSidebarCardIds:
    def test_tracks_section_preserved(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-track-list"' in html

    def test_approach_section_preserved(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-approach-section"' in html

    def test_rf_card_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-rf-section"' in html
        assert 'id="ops-rf-state-badge"' in html
        assert 'id="ops-rf-rssi"' in html
        assert 'id="ops-rf-best"' in html

    def test_mission_card_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-mission-section"' in html
        assert 'id="ops-mission-badge"' in html
        assert 'id="ops-mission-elapsed"' in html
        assert 'id="ops-btn-mission-end"' in html
        assert 'id="ops-btn-mission-export"' in html

    def test_pipeline_card_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-pipeline-section"' in html
        assert 'id="ops-btn-pipeline-pause"' in html
        assert 'id="ops-btn-pipeline-stop"' in html
        # Plain-English confirm language lives in the card note
        assert "Python" in html and "Docker" in html

    def test_detlog_card_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-detlog-section"' in html
        assert 'id="ops-detlog"' in html

    def test_vehicle_and_map_preserved(self):
        html = OPS_HTML.read_text()
        # Existing must-preserve IDs — map canvas + vehicle info block
        assert 'id="ops-minimap-canvas"' in html
        assert 'id="ops-vehicle-info"' in html
        assert 'id="ops-info-position"' in html  # SIM GPS sink


# ── (b) ops.js exposes expected sidebar-update functions ──

class TestOpsJsSidebarExports:
    def test_exports_include_new_sidebar_functions(self):
        src = OPS_JS.read_text()
        for name in (
            "updateSidebarRF: updateSidebarRF",
            "updateSidebarMission: updateSidebarMission",
            "updateSidebarPipeline: updateSidebarPipeline",
            "updateSidebarDetLog: updateSidebarDetLog",
        ):
            assert name in src, f"ops.js should export {name!r}"

    def test_update_hud_invokes_new_cards(self):
        src = OPS_JS.read_text()
        # All four card updaters must be called each tick
        for call in (
            "updateSidebarRF(",
            "updateSidebarMission(",
            "updateSidebarPipeline(",
            "updateSidebarDetLog(",
        ):
            assert call in src

    def test_sidebar_css_loaded(self, client):
        # New thin sidebar stylesheet is served by the static mount
        resp = client.get("/static/css/ops-sidebar.css")
        assert resp.status_code == 200
        body = resp.text
        # Key class names the JS targets
        assert ".ops-card-badge" in body
        assert ".ops-rf-bar" in body
        assert ".ops-detlog-entry" in body


# ── (c) Card update functions handle missing data without throwing ──

class TestSidebarCardsGuardMissingData:
    """Lexical guard: each new updater must tolerate null/undefined inputs
    rather than deref-crash. We check for explicit fallbacks to '--' and
    null/typeof-guards in the source.
    """

    def test_rf_card_renders_dash_when_no_data(self):
        src = OPS_JS.read_text()
        # updateSidebarRF handles rf === null/undefined
        assert "if (!rf || typeof rf !== 'object')" in src
        # Missing fields render '--' with text-dim colour
        assert "setDim(rssiEl, '--', true)" in src
        assert "var(--text-dim)" in src

    def test_mission_card_renders_dash_when_idle(self):
        src = OPS_JS.read_text()
        # Disabled End button when no mission_name
        assert "endBtn.disabled = !isActive" in src
        assert "s.mission_name" in src

    def test_pipeline_card_defaults_to_dash(self):
        src = OPS_JS.read_text()
        # fps/inference_ms checked with typeof number
        assert "typeof s.fps === 'number'" in src
        assert "typeof s.inference_ms === 'number'" in src

    def test_detlog_empty_state_preserved(self):
        src = OPS_JS.read_text()
        assert "No detections yet" in src
        # Guards dets === undefined with Array.isArray
        assert "Array.isArray(detections)" in src


# ── (d) Approach panel + protected code paths still wired (regression) ──

class TestProtectedCodePathsIntact:
    """Guards that the SIM GPS / approach / radial-menu / confirm-modal
    code paths previously landed in ops.js survived the sidebar rewrite.
    """

    def test_approach_section_still_in_html(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-approach-section"' in html
        assert 'id="ops-btn-approach-abort"' in html
        assert 'id="ops-approach-mode"' in html

    def test_approach_handler_still_wired(self):
        src = OPS_JS.read_text()
        assert "approachAbortBtn.addEventListener" in src
        assert "abortApproach()" in src

    def test_sim_gps_sink_still_referenced(self):
        src = OPS_JS.read_text()
        assert "window.HydraSimGps.withSimSuffix(stats.position" in src

    def test_radial_menu_and_confirm_overlay_preserved(self):
        src = OPS_JS.read_text()
        assert "function showContextMenu" in src
        assert "function showConfirmOverlay" in src
        assert "ops-radial-menu" in src
        assert "ops-confirm-overlay" in src

    def test_lock_overlay_update_still_called(self):
        src = OPS_JS.read_text()
        assert "updateLockInfo(HydraApp.state.target)" in src
