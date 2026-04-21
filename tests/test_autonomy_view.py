"""Smoke tests for the Autonomy top-level view (Milestone 1 scaffold).

Scope: the autonomy view is a dashboard READER. Config edits stay in #settings.
Base-template integration (the #autonomy tab button + view-router registration)
is owned by a sibling orchestrator wave — so this test file focuses on the
pieces this workstream owns:

- /static/js/autonomy.js serves (HydraAutonomy IIFE + lifecycle hooks).
- /static/css/autonomy.css serves and uses only post-migration tokens.
- hydra_detect/web/templates/autonomy.html exists with the expected section
  anchors the orchestrator will wire into base.html.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "hydra_detect" / "web" / "templates" / "autonomy.html"
)


@pytest.fixture
def client():
    return TestClient(app)


class TestAutonomyStaticAssets:
    def test_autonomy_js_served(self, client):
        resp = client.get("/static/js/autonomy.js")
        assert resp.status_code == 200
        body = resp.text
        assert "HydraAutonomy" in body
        assert "onEnter" in body and "onLeave" in body
        assert "/api/autonomy/status" in body
        # Fallback path — must degrade gracefully when the endpoint is absent.
        assert "/api/config/full" in body
        assert "/api/stats" in body
        # Mode-change fail-safe — POST + connection-lost toast.
        assert "/api/autonomy/mode" in body
        assert "CONNECTION LOST" in body

    def test_autonomy_css_served(self, client):
        resp = client.get("/static/css/autonomy.css")
        assert resp.status_code == 200
        body = resp.text
        # Post-migration tokens used.
        assert "--s-" in body
        assert "--bg-panel" in body
        assert "--border-default" in body
        assert "--radius" in body
        # Forbidden legacy tokens.
        assert "--gap-" not in body
        assert "--bg-secondary" not in body
        # Safety-critical gate colors must reference tokens (not hardcoded
        # hexes), and the mode picker must be able to reach danger state.
        assert "--danger" in body
        assert "--olive-muted" in body
        assert "--warning" in body
        assert "--info" in body
        # Flat 2px radii only — no 999px rounded pills.
        assert "999px" not in body


class TestAutonomyTemplate:
    def test_template_file_exists(self):
        assert TEMPLATE_PATH.is_file()

    def test_template_contains_owned_sections(self):
        body = TEMPLATE_PATH.read_text(encoding="utf-8")
        # (a) mode picker
        assert 'id="autonomy-mode-card"' in body
        assert 'data-mode="dryrun"' in body
        assert 'data-mode="shadow"' in body
        assert 'data-mode="live"' in body
        # (b) geofence preview with SVG + self ⊕ glyph
        assert 'id="autonomy-geofence-svg"' in body
        assert "⊕" in body
        # (c) qualification criteria list
        assert 'id="autonomy-criteria-list"' in body
        # (d) 5-gate safety panel — one <li> per gate
        assert 'id="autonomy-gates-list"' in body
        for gate_id in (
            "geofence", "vehicle_mode", "operator_lock", "gps_fresh", "cooldown",
        ):
            assert f'data-gate="{gate_id}"' in body
        # (e) explainability log
        assert 'id="autonomy-log-list"' in body
        # Two-step LIVE confirm modal with callsign input
        assert 'id="autonomy-mode-modal"' in body
        assert 'id="autonomy-mode-modal-cs-input"' in body

    def test_template_has_placeholder_copy_when_endpoint_missing(self):
        """Graceful degradation copy is baked into the template so the view
        is not blank on first paint before /api/autonomy/status lands."""
        body = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "[awaiting /api/autonomy/status]" in body
