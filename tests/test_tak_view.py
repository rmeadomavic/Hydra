"""Smoke tests for the TAK top-level view (M1 scaffold).

M1 scope:
- GET / includes the TAK tab, view-tak container, and tak.js script tag.
- Static assets (tak.css, tak.js, tak.html partial via the main include)
  are reachable.
- /api/tak/commands already has coverage in test_web_api.py; this file
  covers navigation-level wiring only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


@pytest.fixture
def client():
    return TestClient(app)


class TestTakTabNavigation:
    def test_index_includes_tak_tab(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'data-view="tak"' in resp.text
        assert ">TAK<" in resp.text or "tab-label\">TAK" in resp.text

    def test_index_includes_tak_view_container(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="view-tak"' in resp.text
        assert "view-tak" in resp.text

    def test_index_includes_tak_partial_content(self, client):
        """tak.html is {% include %}d — its markers should be in the shell."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="tak-grid"' in resp.text
        assert 'id="tak-feed"' in resp.text
        assert 'id="tak-audit"' in resp.text

    def test_index_includes_tak_script_and_css(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "/static/js/tak.js" in resp.text
        assert "/static/css/tak.css" in resp.text

    def test_tak_placeholder_copy_present(self, client):
        """M2 stub columns — copy must be visible so Kyle sees B2/B3/B9 state."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "B2 — not yet built" in resp.text
        assert "B3 — not yet built" in resp.text
        assert "B9 — not yet built" in resp.text


class TestTakStaticAssets:
    def test_tak_js_served(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        assert "HydraTak" in resp.text
        assert "/api/tak/commands" in resp.text
        assert "onEnter" in resp.text and "onLeave" in resp.text

    def test_tak_css_served(self, client):
        resp = client.get("/static/css/tak.css")
        assert resp.status_code == 200
        # Post-migration tokens only — sanity check.
        assert "--s-" in resp.text
        assert "--bg-panel" in resp.text
        assert "--ogt-" not in resp.text
        assert "--gap-" not in resp.text

    def test_view_router_registers_tak(self, client):
        resp = client.get("/static/js/router/view-router.js")
        assert resp.status_code == 200
        assert "'tak'" in resp.text
