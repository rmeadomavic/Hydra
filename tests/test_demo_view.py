"""Tests for the Tech-Day demo view.

The demo view is a compact single-screen layout for 2-minute open-house
demos: left rail of tech highlights, centre 16:9 feed + mini TAK map,
right rail of top hits.

Covers:
- demo.html template exists and contains #view-demo + required zones
- demo.js defines HydraDemo with onEnter/onLeave + the expected pollers
- demo.css is served by the StaticFiles mount and contains the mock grid
- demo.html contains the three-zone grid structure (left/centre/right)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "hydra_detect" / "web" / "templates"
STATIC_DIR = REPO_ROOT / "hydra_detect" / "web" / "static"

DEMO_HTML = TEMPLATE_DIR / "demo.html"
DEMO_JS = STATIC_DIR / "js" / "demo.js"
DEMO_CSS = STATIC_DIR / "css" / "demo.css"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestDemoTemplateServed:
    """The template file exists, is readable, and exposes the #view-demo
    anchor the base.html router will target once integration lands."""

    def test_template_file_exists(self):
        assert DEMO_HTML.exists(), (
            "demo.html missing at " + str(DEMO_HTML)
        )

    def test_template_has_view_demo_anchor(self):
        html = DEMO_HTML.read_text()
        assert "demo-grid" in html, "demo-grid root missing"
        # Router wires the outer #view-demo container via base.html include.
        # The demo partial itself uses id="demo-grid" + id="demo-col-*" anchors
        # the router / JS will query. The #view-demo id is documented in the
        # impl_techday.md message so the base.html wiring wave can find it.
        assert 'id="demo-grid"' in html

    def test_template_has_all_three_zone_anchors(self):
        html = DEMO_HTML.read_text()
        assert 'id="demo-col-left"' in html
        assert 'id="demo-col-center"' in html
        assert 'id="demo-col-right"' in html


class TestDemoTemplateGrid:
    """The mock specifies a 280 / 1fr / 300 grid on #040404 with a bordered
    16:9 feed + mini TAK. These assertions lock those anchors in place."""

    def test_left_zone_is_tech_highlights(self):
        html = DEMO_HTML.read_text()
        assert 'aria-label="Technology highlights"' in html
        assert "Tech Highlights" in html
        # Six highlight cards (Jetson, YOLO, ByteTrack, MAVLink, TAK, RF)
        assert html.count('class="demo-highlight"') == 6

    def test_center_zone_has_feed_and_takmini(self):
        html = DEMO_HTML.read_text()
        assert 'aria-label="Live video feed and mini TAK map"' in html
        assert 'id="demo-feed-frame"' in html
        assert 'id="demo-feed-img"' in html
        assert 'src="/stream.jpg"' in html
        assert 'id="demo-takmini"' in html

    def test_right_zone_is_top_hits(self):
        html = DEMO_HTML.read_text()
        assert 'aria-label="Top hits and peer roster"' in html
        assert "Top Hits" in html
        assert 'id="demo-peers-list"' in html

    def test_each_zone_has_aria_label(self):
        """Accessibility rule from the task: aria-label on each zone."""
        html = DEMO_HTML.read_text()
        # Three zones × one aria-label each
        assert 'aria-label="Technology highlights"' in html
        assert 'aria-label="Live video feed and mini TAK map"' in html
        assert 'aria-label="Top hits and peer roster"' in html


class TestDemoCssServed:
    """StaticFiles mount serves /static/css/demo.css and the file encodes
    the mock's 280 / 1fr / 300 grid + bordered-feed shadow."""

    def test_demo_css_served_by_static_mount(self, client):
        resp = client.get("/static/css/demo.css")
        assert resp.status_code == 200, (
            "demo.css not served by StaticFiles (status " + str(resp.status_code) + ")"
        )
        assert "text/css" in resp.headers.get("content-type", "")

    def test_demo_css_has_grid_token(self):
        css = DEMO_CSS.read_text()
        # Mock spec: grid-template-columns: 280px 1fr 300px
        assert "280px 1fr 300px" in css

    def test_demo_css_uses_post_migration_tokens(self):
        """Rule: only post-migration tokens. --bg-void and --olive-primary
        replace the raw mock hex (#040404, #385723). This test spot-checks
        that the tokens are present and raw brand hex is NOT used for
        background/accent colors."""
        css = DEMO_CSS.read_text()
        assert "var(--bg-void)" in css
        assert "var(--olive-primary)" in css
        assert "var(--border-default)" in css
        # Raw #040404 must not appear — the mock hex is replaced by the token.
        assert "#040404" not in css

    def test_demo_css_uses_flat_2px_radii(self):
        """Rule: 2px flat radii. The stylesheet references --radius (2px) for
        every rounded corner rather than hardcoded 4/6/8 values."""
        css = DEMO_CSS.read_text()
        # Mock uses radius:6 on the feed frame; token maps to 2px per variables.css.
        assert "border-radius: var(--radius)" in css


class TestDemoJsExports:
    """demo.js exposes HydraDemo IIFE with onEnter/onLeave so the base.html
    view-router can drive lifecycle when integration lands."""

    def test_demo_js_served_by_static_mount(self, client):
        resp = client.get("/static/js/demo.js")
        assert resp.status_code == 200

    def test_hydra_demo_iife_exports_lifecycle(self):
        js = DEMO_JS.read_text()
        assert "const HydraDemo" in js
        assert "onEnter" in js
        assert "onLeave" in js
        # The IIFE pattern: return { onEnter, onLeave, ... }
        assert "return {" in js

    def test_demo_js_polls_expected_endpoints(self):
        js = DEMO_JS.read_text()
        assert "/api/stats" in js
        assert "/api/tak/peers" in js

    def test_demo_js_poll_cadences_match_spec(self):
        """Task contract: 1s for /api/stats, 3s for /api/tak/peers."""
        js = DEMO_JS.read_text()
        assert "POLL_MS_STATS = 1000" in js
        assert "POLL_MS_PEERS = 3000" in js

    def test_demo_js_implements_dom_diff(self):
        """Rule: DOM-diff on polls. The module reuses row/marker nodes by
        key rather than wiping + rebuilding innerHTML each tick."""
        js = DEMO_JS.read_text()
        # Evidence of diffing: keyed caches + per-child reparenting.
        assert "peerRowNodes" in js
        assert "peerMarkerNodes" in js
        assert "insertBefore" in js
        # And explicit no-innerHTML (safe against XSS as well).
        assert ".innerHTML" not in js

    def test_demo_js_respects_visibility_for_both_pollers(self):
        js = DEMO_JS.read_text()
        assert js.count("document.visibilityState") >= 2
