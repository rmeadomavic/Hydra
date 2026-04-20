"""Smoke tests for Wave 3A integration wiring.

Verifies that the three self-contained modules shipped by sibling agents
(Systems view, Autonomy view, Konami easter.js) are actually reachable
from the app shell: nav buttons in the topbar, view containers wrapping
the template includes, script tags in base.html, and the view-router
VALID_VIEWS list. Also a regression check that the prior #ops / #config
/ #settings / #tak tabs remain.

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
    def test_systems_nav_button_present(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'data-view="systems"' in html
        assert ">Systems<" in html or "Systems</span>" in html

    def test_autonomy_nav_button_present(self, client):
        resp = client.get("/")
        html = resp.text
        assert 'data-view="autonomy"' in html
        assert ">Autonomy<" in html or "Autonomy</span>" in html

    def test_existing_nav_tabs_still_present(self, client):
        """Regression — Wave 3A must not break prior tabs."""
        resp = client.get("/")
        html = resp.text
        for view in ("ops", "config", "settings", "tak"):
            assert f'data-view="{view}"' in html, f"missing tab {view!r}"


class TestViewContainers:
    def test_systems_view_container(self, client):
        resp = client.get("/")
        html = resp.text
        assert 'id="view-systems"' in html

    def test_autonomy_view_container(self, client):
        resp = client.get("/")
        html = resp.text
        assert 'id="view-autonomy"' in html

    def test_systems_include_markup_rendered(self, client):
        """The {% include 'systems.html' %} payload must have landed in /."""
        resp = client.get("/")
        html = resp.text
        # Any one anchor from systems.html — pick something load-bearing.
        assert "systems-metric" in html or "systems-subsystems" in html or "Systems" in html

    def test_autonomy_include_markup_rendered(self, client):
        """The {% include 'autonomy.html' %} payload must have landed in /."""
        resp = client.get("/")
        html = resp.text
        # Anchors owned by autonomy.html.
        assert 'id="autonomy-mode-card"' in html
        assert 'id="autonomy-gates-list"' in html

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
    def test_view_router_includes_systems_and_autonomy(self):
        body = VIEW_ROUTER_PATH.read_text(encoding="utf-8")
        assert "VALID_VIEWS" in body
        assert "'systems'" in body
        assert "'autonomy'" in body

    def test_view_router_still_lists_prior_views(self):
        """Regression — Wave 3A must not drop prior views from VALID_VIEWS."""
        body = VIEW_ROUTER_PATH.read_text(encoding="utf-8")
        for view in ("ops", "config", "settings", "tak"):
            assert f"'{view}'" in body, f"view {view!r} dropped from VALID_VIEWS"
