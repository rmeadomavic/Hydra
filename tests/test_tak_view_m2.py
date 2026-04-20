"""Smoke tests for the TAK view M2 expansion.

M2 scope (this file):
- tak.html contains the three new section IDs (tak-type-counts, tak-peers,
  tak-audit-footer).
- tak.js polls the three new endpoints (/api/tak/type_counts,
  /api/tak/peers, /api/audit/summary).
- The three update helpers handle empty/missing data without throwing
  (checked via source-grep for defensive access patterns).
- Regression: center-column command feed markup + poll remain wired.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


@pytest.fixture
def client():
    return TestClient(app)


class TestTakM2Markup:
    """tak.html carries the three new section IDs required by M2."""

    def test_index_has_type_counts_section(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="tak-type-counts"' in resp.text
        assert 'id="tak-type-total"' in resp.text
        assert 'id="tak-type-list"' in resp.text

    def test_index_has_peers_section(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="tak-peers"' in resp.text
        assert 'id="tak-peers-list"' in resp.text
        assert 'id="tak-security"' in resp.text
        assert 'id="tak-security-hmac"' in resp.text
        assert 'id="tak-security-allowed"' in resp.text
        assert 'id="tak-security-dup"' in resp.text
        assert 'id="tak-security-targets"' in resp.text

    def test_index_has_audit_footer_section(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="tak-audit-footer"' in resp.text
        # Six tiles by id
        for tid in (
            "tak-audit-accepted",
            "tak-audit-rejected",
            "tak-audit-hmac-invalid",
            "tak-audit-approach-arm",
            "tak-audit-drop",
            "tak-audit-strike",
        ):
            assert f'id="{tid}"' in resp.text
        assert 'id="tak-audit-events-list"' in resp.text


class TestTakM2PollerWiring:
    """tak.js polls each of the three new endpoints."""

    def test_tak_js_polls_type_counts(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        assert "/api/tak/type_counts" in resp.text

    def test_tak_js_polls_peers(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        assert "/api/tak/peers" in resp.text

    def test_tak_js_polls_audit_summary(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        assert "/api/audit/summary" in resp.text


class TestTakM2DefensiveUpdate:
    """tak.js update helpers must cope with empty / missing-field payloads
    without crashing the view. We verify by checking the source for the
    defensive-access patterns the updaters rely on."""

    def test_update_type_counts_guards_missing_counts(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        # counts access is guarded by the typeof-object check (falls back to {})
        assert "typeof data.counts === 'object'" in resp.text
        # total + window_seconds both checked with typeof === 'number'
        assert "typeof data.total === 'number'" in resp.text
        assert "typeof data.window_seconds === 'number'" in resp.text

    def test_update_peers_guards_missing_arrays(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        assert "Array.isArray(data.peers)" in resp.text
        assert "Array.isArray(data.unicast_targets)" in resp.text
        assert "Array.isArray(data.allowed_callsigns)" in resp.text

    def test_update_audit_guards_missing_counts(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        assert "Array.isArray(data.recent_events)" in resp.text
        # counts access is guarded by the typeof-object check (falls back to {})
        assert "typeof data.counts === 'object'" in resp.text


class TestTakM2CenterColumnRegression:
    """Center column (M1) must still render and still poll /api/tak/commands."""

    def test_commands_feed_markup_intact(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="tak-feed"' in resp.text
        assert 'id="tak-feed-list"' in resp.text
        assert 'id="tak-feed-empty"' in resp.text
        assert 'id="tak-commands-meta"' in resp.text

    def test_commands_poll_intact(self, client):
        resp = client.get("/static/js/tak.js")
        assert resp.status_code == 200
        assert "/api/tak/commands" in resp.text
        assert "HydraTak" in resp.text
