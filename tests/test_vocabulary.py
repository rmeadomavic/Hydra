"""SORCC vocabulary enforcement across UI copy.

The canonical vocabulary (from design_handoff_hydra_alignment/README.md):
  * SORCC          — always ALL CAPS.
  * uncrewed       — not "unmanned".
  * sortie         — not "mission" or "run" (in UI copy only).
  * platform       — not "vehicle" (in UI copy only).

Scope of enforcement: rendered HTML served from the FastAPI app AND the
JavaScript string literals surfaced through .textContent / .value / toast /
alert / confirm calls. Internal identifiers (API paths like /api/mission/*,
Python vars like mission_name, config keys, element IDs, CSS class names)
are explicitly out of scope — the vocabulary rule is an operator-voice rule,
not a refactor.

These tests enforce the vocabulary sweep shipped alongside
hydra_detect/web/templates/*.html and hydra_detect/web/static/js/*.js.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "hydra_detect" / "web" / "templates"
JS_DIR = REPO_ROOT / "hydra_detect" / "web" / "static" / "js"


# ── Helpers ──────────────────────────────────────────────────────────────

def _strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def _strip_style_and_script_blocks(html: str) -> str:
    """Remove <style>...</style> and <script>...</script> contents. CSS
    selectors and inline JS are not operator-visible text, so they are out
    of scope for the vocabulary sweep (class names and variable names are
    explicitly exempt per the sweep spec).
    """
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    return html


def _strip_attribute_values(html: str) -> str:
    """Strip id=, for=, class=, data-*, name=, style=, href=, src= attribute
    values so that only the rendered text / label / placeholder / title /
    aria-label content remains. We deliberately KEEP aria-label, title,
    placeholder, alt — those are operator-visible.
    """
    strip_attrs = (
        "id", "for", "class", "data-[a-z0-9_-]+", "name", "style",
        "href", "src", "onerror", "role", "tabindex",
        "value", "maxlength", "rel", "type",
    )
    pattern = r'\s(?:' + '|'.join(strip_attrs) + r')\s*=\s*"[^"]*"'
    return re.sub(pattern, "", html)


def _rendered_operator_text(html: str) -> str:
    """Approximation of what an operator actually sees in a page response:
    <style>/<script> blocks dropped, HTML comments dropped, attribute soup
    stripped except for user-visible attributes. What remains: visible text
    + alt/title/placeholder/aria-label.
    """
    return _strip_attribute_values(
        _strip_style_and_script_blocks(_strip_html_comments(html))
    )


@pytest.fixture
def client():
    return TestClient(app)


# ── (a) Forbidden tokens: zero occurrences in rendered operator text ─────

class TestForbiddenTokensAbsent:
    """Rendered HTML must not contain 'unmanned' / 'Unmanned', 'mission-run',
    'Mission Run', or 'vehicle' / 'Vehicle' (outside the HYDRA-{team}-{vehicle}
    callsign placeholder, which is a deliberate operator-facing contract).
    """

    def test_no_unmanned_anywhere_in_templates(self):
        for tpl in TEMPLATES_DIR.glob("*.html"):
            text = tpl.read_text()
            assert "unmanned" not in text.lower(), (
                f"Forbidden token 'unmanned' in {tpl.name}; use 'uncrewed'."
            )

    def test_no_mission_run_in_rendered_text(self, client):
        for route in ("/", "/review", "/setup", "/control", "/instructor"):
            resp = client.get(route)
            if resp.status_code != 200:
                continue
            visible = _rendered_operator_text(resp.text)
            assert "mission-run" not in visible.lower(), (
                f"Forbidden token 'mission-run' in rendered {route}."
            )
            assert "Mission Run" not in visible, (
                f"Forbidden token 'Mission Run' in rendered {route}."
            )

    def test_no_vehicle_in_visible_text_outside_callsign_placeholder(self):
        """Walk every template: strip comments + attribute values except the
        callsign placeholder slot, then assert 'vehicle' and 'Vehicle' do not
        appear. The one allowed site is the {vehicle} literal inside
        HYDRA-{team}-{vehicle}.
        """
        for tpl in TEMPLATES_DIR.glob("*.html"):
            raw = tpl.read_text()
            # Drop the HYDRA-{team}-{vehicle} literal so the remaining corpus
            # can be checked strictly.
            scrubbed = raw.replace("HYDRA-{team}-{vehicle}", "HYDRA-CALLSIGN")
            visible = _rendered_operator_text(scrubbed)
            # Remove any residual `for="showVehicleTrack"`-style id refs that
            # survived because `for=` was meant to be kept (it points at an id).
            # These are not user-facing.
            visible = re.sub(r'\bfor\s*=\s*"[^"]*"', "", visible)
            visible_lc = visible.lower()
            assert "vehicle" not in visible_lc, (
                f"'vehicle' appears in operator-visible text of {tpl.name}; "
                f"use 'platform' except in the HYDRA-{{team}}-{{vehicle}} "
                f"callsign placeholder."
            )


# ── (b) Keep-clauses: internal identifiers preserved ────────────────────

class TestKeepClauses:
    """The vocabulary sweep must not rename API paths, Python vars, or
    config keys. Those stay verbatim — they are machine contracts.
    """

    def test_api_mission_start_still_resolvable(self, client):
        # The endpoint exists; without auth + empty body it should either
        # 401 or 400, but not 404. A 404 would mean someone renamed the
        # route — which is explicitly forbidden.
        resp = client.post("/api/mission/start", json={})
        assert resp.status_code != 404, (
            "POST /api/mission/start must remain a valid route (machine contract)."
        )

    def test_api_mission_end_still_resolvable(self, client):
        resp = client.post("/api/mission/end", json={})
        assert resp.status_code != 404, (
            "POST /api/mission/end must remain a valid route (machine contract)."
        )

    def test_api_vehicle_mode_still_resolvable(self, client):
        resp = client.post("/api/vehicle/mode", json={})
        assert resp.status_code != 404, (
            "POST /api/vehicle/mode must remain a valid route (machine contract)."
        )

    def test_mission_name_python_var_preserved(self):
        """The event logger still carries mission_name — the Python-side
        data contract must not track the UI vocabulary rename.
        """
        src = (REPO_ROOT / "hydra_detect" / "event_logger.py").read_text()
        assert "_mission_name" in src or "mission_name" in src

    def test_callsign_placeholder_preserved_in_setup(self):
        """The callsign format literal {vehicle} must survive the sweep —
        renaming it would break an operator-facing contract.
        """
        html = (TEMPLATES_DIR / "setup.html").read_text()
        assert "HYDRA-{team}-{vehicle}" in html


# ── (c) Positive presence: new vocabulary is actually shipped ────────────

class TestCanonicalVocabularyPresent:
    def test_sortie_appears_in_ops_hud(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "SORTIE" in resp.text or "Sortie" in resp.text, (
            "Rendered root must contain 'Sortie' or 'SORTIE' in place of "
            "'Mission' in UI copy."
        )

    def test_platform_appears_in_rendered_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "PLATFORM" in resp.text or "Platform" in resp.text, (
            "Rendered root must contain 'Platform' in place of 'Vehicle' "
            "in UI copy (Topbar + Ops sidebar)."
        )

    def test_sortie_review_link_in_settings_view(self, client):
        resp = client.get("/")
        assert "Sortie Review" in resp.text

    def test_platform_type_label_in_setup(self, client):
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert "Platform Type" in resp.text

    def test_sortie_header_in_review_page(self, client):
        resp = client.get("/review")
        assert resp.status_code == 200
        assert "SORTIE REVIEW" in resp.text
        assert "Sortie Review" in resp.text  # <title>


# ── (d) SORCC is always uppercase in UI copy ─────────────────────────────

class TestSorccAlwaysUppercase:
    def test_no_lowercase_sorcc_in_rendered_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        # Any standalone 'sorcc' or 'Sorcc' token is a style violation.
        # We allow sub-string matches inside URLs / class names where the
        # canonical token is uppercase, so we check word-boundary variants.
        assert not re.search(r"\bsorcc\b", body), (
            "'sorcc' must be uppercased to 'SORCC' in UI copy."
        )
        assert not re.search(r"\bSorcc\b", body), (
            "'Sorcc' must be uppercased to 'SORCC' in UI copy."
        )

    def test_sorcc_uppercase_present(self, client):
        resp = client.get("/")
        assert "SORCC" in resp.text


# ── (e) Brand invariants preserved: UNCLASSIFIED + Payload Integrator ────

class TestBrandInvariantsPreserved:
    def test_unclassified_footer_preserved(self, client):
        resp = client.get("/")
        assert "UNCLASSIFIED" in resp.text

    def test_payload_integrator_preserved(self, client):
        resp = client.get("/")
        assert "SORCC Payload Integrator" in resp.text


# ── (f) JS-surface string sweep: no Mission/Vehicle in user-facing JS strings

class TestJsStringsSwept:
    """The JS string literals that hit toast/confirm/.textContent must be
    swept. We assert on the key files that the canonical replacements are
    present and the forbidden literals are absent.
    """

    def test_ops_js_toast_uses_sortie(self):
        src = (JS_DIR / "ops.js").read_text()
        assert "'Sortie ended'" in src
        assert "'Mission ended'" not in src
        assert "End current sortie" in src
        assert "End current mission" not in src

    def test_config_js_toast_uses_sortie(self):
        src = (JS_DIR / "config.js").read_text()
        assert "Sortie started: " in src
        assert "Sortie ended" in src
        assert "End current sortie" in src
        assert "Enter a sortie name before starting" in src
        assert "sortie profile" in src
        # Forbidden literals
        assert "Mission started: " not in src
        assert "'Mission ended'" not in src
        assert "End current mission" not in src
        assert "Enter a mission name before starting" not in src

    def test_config_js_confirm_uses_platform(self):
        src = (JS_DIR / "config.js").read_text()
        assert "'Command platform to LOITER?'" in src
        assert "'Resume AUTO sortie?'" in src
        assert "Platform will switch to GUIDED mode." in src
        assert "'Command vehicle to LOITER?'" not in src
        assert "'Resume AUTO mission?'" not in src
        assert "Vehicle will switch to GUIDED mode." not in src

    def test_instructor_js_uses_sortie_and_platform(self):
        src = (JS_DIR / "instructor.js").read_text()
        assert "'Sortie'" in src
        assert "platform unreachable" in src
        assert "vehicle unreachable" not in src

    def test_autonomy_js_uses_platform_modes_label(self):
        src = (JS_DIR / "autonomy.js").read_text()
        assert "'Allowed platform modes'" in src
        assert "'Allowed vehicle modes'" not in src
