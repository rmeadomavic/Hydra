"""Smoke tests for the 5-tab OpsSidebar refactor on #ops.

Replaces the earlier 8-card stacked sidebar with a tab strip per
ops-station.jsx:1453-1482: TRACKS · RF · MAVLINK · TAK · EVENTS.

Covers:
  (a) 5 tab buttons + tab panel IDs present in ops.html.
  (b) ApproachPanel stays ABOVE the tab strip (not inside a panel).
  (c) ops.js tracks active tab in HydraApp.state.opsActiveTab (default
      'tracks') and exposes per-tab update functions.
  (d) Count badges wired for tracks/events/rf; TAK/MAVLink counts
      settable by refresh functions.
  (e) Mission + Pipeline sections relocated to the left mission rail.
  (f) REGRESSION: radial menu, confirm overlay, SIM GPS, approach
      abort, RTL handler, updateLockInfo all still wired.

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
OPS_SIDEBAR_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "ops-sidebar.css"


@pytest.fixture
def client():
    return TestClient(app)


# ── (a) Tab strip + tab panels present ──

class TestTabStripStructure:
    def test_five_tab_buttons_present(self):
        html = OPS_HTML.read_text()
        assert 'class="ops-tabs"' in html or "ops-tabs" in html
        for tab in ("tracks", "rf", "mavlink", "tak", "events"):
            assert f'data-tab="{tab}"' in html, f"missing tab button {tab!r}"

    def test_five_tab_labels_match_mock(self):
        html = OPS_HTML.read_text()
        for label in ("Tracks", "RF", "MAVLink", "TAK", "Events"):
            assert f">{label}<" in html, f"missing tab label {label!r}"

    def test_all_five_tab_panels_declared(self):
        html = OPS_HTML.read_text()
        for tab in ("tracks", "rf", "mavlink", "tak", "events"):
            assert f'id="ops-tab-panel-{tab}"' in html, f"missing panel id for {tab}"

    def test_tab_count_badges_present(self):
        html = OPS_HTML.read_text()
        # At minimum tracks + events + rf + tak + mavlink count spans exist
        for tab in ("tracks", "rf", "mavlink", "tak", "events"):
            assert f'id="ops-tab-count-{tab}"' in html

    def test_tracks_panel_retains_track_list(self):
        html = OPS_HTML.read_text()
        # TRACKS tab must host the existing #ops-track-list container
        assert 'id="ops-track-list"' in html

    def test_rf_panel_retains_rf_section(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-rf-section"' in html
        assert 'id="ops-rf-state-badge"' in html
        assert 'id="ops-rf-rssi"' in html

    def test_mavlink_panel_has_telemetry_strip(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-tab-mav-fps"' in html
        assert 'id="ops-tab-mav-latency"' in html
        assert 'id="ops-tab-mav-log"' in html

    def test_tak_panel_has_peers_and_commands_areas(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-tab-tak-peers"' in html
        assert 'id="ops-tab-tak-commands"' in html
        assert 'id="ops-tab-tak-callsign"' in html

    def test_events_panel_hosts_detlog_and_audit(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-detlog-section"' in html
        assert 'id="ops-detlog"' in html
        assert 'id="ops-tab-events-audit"' in html


# ── (b) ApproachPanel outside the tab strip (mock spec) ──

class TestApproachOutsideTabs:
    def test_approach_section_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-approach-section"' in html
        assert 'id="ops-btn-approach-abort"' in html

    def test_approach_precedes_tab_strip(self):
        """ApproachPanel renders above the tab strip (mock ops-station.jsx:1450)."""
        html = OPS_HTML.read_text()
        approach_idx = html.index('id="ops-approach-section"')
        tabs_idx = html.index('id="ops-tabs"')
        assert approach_idx < tabs_idx, (
            "ApproachPanel must render above the tab strip — mock spec."
        )

    def test_approach_not_inside_a_tab_panel(self):
        """ApproachPanel must not be nested inside any tab panel."""
        html = OPS_HTML.read_text()
        approach_idx = html.index('id="ops-approach-section"')
        for tab in ("tracks", "rf", "mavlink", "tak", "events"):
            panel_marker = f'id="ops-tab-panel-{tab}"'
            panel_idx = html.index(panel_marker)
            # Approach must appear before the FIRST tab panel
            assert approach_idx < panel_idx or approach_idx > html.index(
                '</div>', panel_idx
            ), f"approach must not live inside ops-tab-panel-{tab}"


# ── (c) JS state + per-tab update functions ──

class TestOpsJsTabState:
    def test_active_tab_stored_on_hydra_app_state(self):
        src = OPS_JS.read_text()
        assert "HydraApp.state.opsActiveTab" in src
        # Default must be 'tracks'
        assert "'tracks'" in src

    def test_tab_wire_function_binds_click(self):
        src = OPS_JS.read_text()
        assert "wireOpsTabs" in src
        assert "setActiveTab" in src
        # Tabs are clickable buttons
        assert ".ops-tab" in src

    def test_per_tab_updaters_exported(self):
        src = OPS_JS.read_text()
        for name in (
            "setActiveTab: setActiveTab",
            "getActiveTab: getActiveTab",
            "updateTabCounts: updateTabCounts",
            "updateTabMavlink: updateTabMavlink",
            "refreshTakTab: refreshTakTab",
            "refreshAuditLog: refreshAuditLog",
        ):
            assert name in src, f"ops.js should export {name!r}"

    def test_update_hud_routes_by_active_tab(self):
        src = OPS_JS.read_text()
        # Only the active tab's panel is updated each tick
        assert "var activeTab = getActiveTab()" in src
        assert "activeTab === 'tracks'" in src
        assert "activeTab === 'rf'" in src
        assert "activeTab === 'mavlink'" in src
        assert "activeTab === 'events'" in src

    def test_lock_info_is_non_throwing_noop_safe(self):
        """updateLockInfo must remain callable even when target panel is inside
        an inactive tab — it targets #ops-lock-overlay which is outside the
        tab strip and guards on missing overlay."""
        src = OPS_JS.read_text()
        assert "function updateLockInfo(target)" in src
        assert "if (!overlay) return" in src


# ── (d) Count badges update from state ──

class TestTabCountBadges:
    def test_count_badges_update_from_state(self):
        src = OPS_JS.read_text()
        assert "updateTabCounts" in src
        # Tracks count reads from HydraApp.state.tracks
        assert "HydraApp.state.tracks" in src
        assert "HydraApp.state.detections" in src

    def test_count_badges_rendered_inside_tab_buttons(self):
        html = OPS_HTML.read_text()
        # Badge spans live inside tab buttons
        assert 'class="ops-tab-count"' in html

    def test_empty_states_match_mock_tone(self):
        html = OPS_HTML.read_text()
        # "No RF activity" per task spec
        assert "No RF activity" in html or "No RF" in html
        assert "No MAVLink" in html
        assert "No inbound commands" in html
        assert "No audit events" in html


# ── (e) Mission + Pipeline relocated to left mission rail ──

class TestMissionRailRelocation:
    def test_mission_rail_slot_hosts_mission_section(self):
        html = OPS_HTML.read_text()
        rail_idx = html.index('id="ops-mission-rail-slot"')
        mission_idx = html.index('id="ops-mission-section"')
        sidebar_idx = html.index('id="ops-hud-sidebar"')
        assert rail_idx < mission_idx < sidebar_idx, (
            "mission section must live in the left mission rail, before the"
            " right sidebar"
        )

    def test_mission_rail_slot_hosts_pipeline_section(self):
        html = OPS_HTML.read_text()
        rail_idx = html.index('id="ops-mission-rail-slot"')
        pipe_idx = html.index('id="ops-pipeline-section"')
        sidebar_idx = html.index('id="ops-hud-sidebar"')
        assert rail_idx < pipe_idx < sidebar_idx

    def test_vehicle_info_in_left_rail(self):
        html = OPS_HTML.read_text()
        rail_idx = html.index('id="ops-mission-rail-slot"')
        vehicle_idx = html.index('id="ops-vehicle-info"')
        sidebar_idx = html.index('id="ops-hud-sidebar"')
        assert rail_idx < vehicle_idx < sidebar_idx


# ── (f) REGRESSION: protected code paths untouched ──

class TestProtectedCodePathsRegression:
    def test_radial_menu_module_present(self):
        src = OPS_JS.read_text()
        assert "function showContextMenu" in src
        assert "function hideContextMenu" in src
        assert "ops-radial-menu" in src

    def test_confirm_overlay_module_present(self):
        src = OPS_JS.read_text()
        assert "function showConfirmOverlay" in src
        assert "function hideConfirmOverlay" in src
        assert "ops-confirm-overlay" in src

    def test_sim_gps_sink_preserved(self):
        src = OPS_JS.read_text()
        assert "window.HydraSimGps.withSimSuffix(stats.position" in src
        html = OPS_HTML.read_text()
        assert 'id="ops-info-position"' in html

    def test_approach_abort_handler_wired(self):
        src = OPS_JS.read_text()
        assert "approachAbortBtn.addEventListener" in src
        assert "abortApproach()" in src
        html = OPS_HTML.read_text()
        assert 'id="ops-btn-approach-abort"' in html

    def test_rtl_button_handler_preserved(self):
        src = OPS_JS.read_text()
        assert "rtlBtn.addEventListener" in src
        assert "mode: 'RTL'" in src

    def test_on_enter_on_leave_exports_intact(self):
        src = OPS_JS.read_text()
        assert "onEnter: onEnter" in src
        assert "onLeave: onLeave" in src

    def test_double_click_fullscreen_preserved(self):
        src = OPS_JS.read_text()
        assert "addEventListener('dblclick'" in src
        assert "requestFullscreen" in src

    def test_minimap_canvas_survives(self):
        """test_ops_sidebar.py + test_ops_layout.py still require the minimap
        canvas ID to exist. Tab refactor must keep it alive."""
        html = OPS_HTML.read_text()
        assert 'id="ops-minimap-canvas"' in html


# ── CSS: tab strip styles ──

class TestTabStripCss:
    def test_tab_strip_css_served(self, client):
        resp = client.get("/static/css/ops-sidebar.css")
        assert resp.status_code == 200
        body = resp.text
        assert ".ops-tabs" in body
        assert ".ops-tab" in body
        assert ".ops-tab.active" in body
        assert ".ops-tab-count" in body

    def test_active_tab_background_matches_mock(self):
        css = OPS_SIDEBAR_CSS.read_text()
        # rgba(56,87,35,0.15) is the active-tab background per mock spec
        assert "rgba(56, 87, 35, 0.15)" in css or "rgba(56,87,35,0.15)" in css

    def test_active_tab_bottom_border_olive_muted(self):
        css = OPS_SIDEBAR_CSS.read_text()
        # 2px olive-muted bottom border on active tab
        assert "border-bottom: 2px solid var(--olive-muted)" in css

    def test_tab_label_typography_barlow_condensed(self):
        css = OPS_SIDEBAR_CSS.read_text()
        # Barlow Condensed via --font-cond token
        assert "font-family: var(--font-cond)" in css
        # Uppercase 0.1em letter-spacing per mock
        assert "0.1em" in css

    def test_inactive_tab_color_888(self):
        css = OPS_SIDEBAR_CSS.read_text()
        assert "color: #888" in css

    def test_count_badge_mono_10px(self):
        css = OPS_SIDEBAR_CSS.read_text()
        assert "font-family: var(--font-mono)" in css
        # JetBrains Mono 10px #666 per mock
        assert "font-size: 10px" in css
        assert "color: #666" in css

    def test_mavlink_log_styles_present(self):
        css = OPS_SIDEBAR_CSS.read_text()
        assert ".ops-mavlink-strip" in css
        assert ".ops-mavlink-log" in css

    def test_empty_state_color_uses_text_dim(self):
        css = OPS_SIDEBAR_CSS.read_text()
        assert ".ops-tab-empty" in css
        assert "var(--text-dim)" in css
