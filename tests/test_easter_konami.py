"""Smoke tests for the restored Konami sentience easter egg.

Verifies that the DOM receivers in base.html are present when morale features
are enabled, AND that the restored listener file
(hydra_detect/web/static/js/easter.js) is served and contains both trigger
sequences. Lexical presence is enough — no headless JS.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_morale_features


@pytest.fixture(autouse=True)
def reset_morale_off():
    configure_morale_features(False)
    yield
    configure_morale_features(False)


@pytest.fixture
def client():
    return TestClient(app)


class TestSentienceDomTargetsPresent:
    def test_root_has_sentience_overlay_when_morale_enabled(self, client):
        configure_morale_features(True)
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'id="sentience-overlay"' in html
        assert 'id="sentience-terminal"' in html

    def test_root_no_sentience_overlay_when_morale_disabled(self, client):
        configure_morale_features(False)
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'id="sentience-overlay"' not in html
        assert 'id="sentience-terminal"' not in html


class TestEasterJsServed:
    def test_easter_js_200(self, client):
        resp = client.get("/static/js/easter.js")
        assert resp.status_code == 200

    def test_easter_js_contains_both_sequences(self, client):
        resp = client.get("/static/js/easter.js")
        body = resp.text
        assert "Up,Up,Down,Down,Left,Right,Left,Right,B,A" in body
        assert "Down,Down,Up,Up,ArrowLeft,ArrowRight,ArrowLeft,ArrowRight,KeyB,KeyA" in body

    def test_easter_js_exposes_hydra_easter_global(self, client):
        resp = client.get("/static/js/easter.js")
        body = resp.text
        assert "window.HydraEaster" in body
        assert "attached: true" in body
