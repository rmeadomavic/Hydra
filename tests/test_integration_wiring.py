"""Smoke tests for dashboard integration wiring.

Verifies that all per-view modules are reachable from the app shell:
nav buttons in the topbar, template includes rendered into the page,
script tags in base.html, and the view-router VALID_VIEWS list.

After the 6-tab → 4-tab streamline, Autonomy is folded into Config and
Systems is folded into Settings — they're no longer top-level views, but
their templates and JS still ship and render inside their new parents.

No headless JS — lexical substring presence is sufficient.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


ROOT = Path(__file__).resolve().parent.parent
VIEW_ROUTER_PATH = (
    ROOT / "hydra_detect" / "web" / "static" / "js" / "router" / "view-router.js"
)
BASE_HTML_PATH = ROOT / "hydra_detect" / "web" / "templates" / "base.html"


@pytest.fixture
def client():
    return TestClient(app)


class TestNavButtons:
    def test_top_level_nav_tabs(self, client):
        """The four top-level tabs must all be present."""
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        for view in ("ops", "config", "settings", "tak"):
            assert f'data-view="{view}"' in html, f"missing tab {view!r}"

    def test_folded_views_no_longer_top_level(self, client):
        """Autonomy and Systems folded into Config and Settings — they must
        NOT appear as top-level nav buttons."""
        resp = client.get("/")
        html = resp.text
        assert 'data-view="autonomy"' not in html
        assert 'data-view="systems"' not in html


class TestViewContainers:
    def test_systems_include_rendered_inside_settings(self, client):
        """systems.html is now embedded inside the Settings view as the
        'System Tools' panel, not as its own top-level view."""
        resp = client.get("/")
        html = resp.text
        # Anchor owned by systems.html must still land on the page.
        assert 'id="systems-card-fps"' in html
        # Container that hosts it lives inside Settings.
        assert 'id="settings-systems-panel"' in html
        # And there's no longer a standalone view container.
        assert 'id="view-systems"' not in html

    def test_autonomy_include_rendered_inside_config(self, client):
        """autonomy.html is now embedded at the bottom of the Config view."""
        resp = client.get("/")
        html = resp.text
        # Anchors owned by autonomy.html.
        assert 'id="autonomy-mode-card"' in html
        assert 'id="autonomy-gates-list"' in html
        # And there's no longer a standalone view container.
        assert 'id="view-autonomy"' not in html

    def test_sentience_overlay_preserved(self, client):
        """PRESERVATION_RULES.md:8-15 — the Konami overlay receivers must
        remain in base.html so the restored easter.js can target them."""
        resp = client.get("/")
        html = resp.text
        assert 'id="sentience-overlay"' in html
        assert 'id="sentience-terminal"' in html
        assert 'id="sentience-crosshair"' in html


class TestScriptTags:
    def test_systems_script_tag_present(self, client):
        resp = client.get("/")
        html = resp.text
        assert "/static/js/systems.js" in html

    def test_autonomy_script_tag_present(self, client):
        resp = client.get("/")
        html = resp.text
        assert "/static/js/autonomy.js" in html

    def test_easter_script_tag_present(self, client):
        resp = client.get("/")
        html = resp.text
        assert "/static/js/easter.js" in html

    def test_easter_loads_before_main(self):
        """easter.js self-attaches a document keydown listener on load. It
        must be included BEFORE main.js so it's registered before main.js
        wires up any topbar input handlers that could swallow keystrokes."""
        html = BASE_HTML_PATH.read_text(encoding="utf-8")
        easter_idx = html.find("/static/js/easter.js")
        main_idx = html.find("/static/js/main.js")
        assert easter_idx != -1, "easter.js <script> tag not found"
        assert main_idx != -1, "main.js <script> tag not found"
        assert easter_idx < main_idx, (
            "easter.js must be included before main.js so the Konami "
            "listener attaches before any input handlers"
        )


class TestViewRouterRegistration:
    def test_view_router_lists_top_level_views(self):
        """VALID_VIEWS must include the four surviving tabs."""
        body = VIEW_ROUTER_PATH.read_text(encoding="utf-8")
        assert "VALID_VIEWS" in body
        for view in ("ops", "config", "settings", "tak"):
            assert f"'{view}'" in body, f"view {view!r} dropped from VALID_VIEWS"

    def test_view_router_aliases_old_hash_routes(self):
        """Old #autonomy and #systems URLs must redirect to their new homes
        so existing bookmarks and external links keep working."""
        body = VIEW_ROUTER_PATH.read_text(encoding="utf-8")
        assert "VIEW_ALIASES" in body
        assert "'autonomy'" in body and "'config'" in body
        assert "'systems'" in body and "'settings'" in body
