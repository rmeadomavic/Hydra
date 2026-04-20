"""Tests for the mock-aligned topbar (primitives.jsx:68-159).

Covers:
- Logo lockup (SORCC shield, HYDRA DETECT wordmark, OPERATOR DASHBOARD
  subtitle, OGT image) is present with the expected IDs/classes.
- Health blips render all six data-blip attributes (MAV/GPS/SIM/KIS/TAK/CAM).
- SIM GPS pill is present in markup but `hidden` by default.
- ABORT button is present with btn-danger-lg styling class.
- Emergency flash overlay div (#emergency-flash) is present in base.html.
- Regression guard: main.js callsign-swap block (lines ~36-42) is preserved
  verbatim.
- Regression guard: preservation-critical blocks (sentience overlay,
  power-user modal, footer) remain untouched.
- Topbar CSS: @keyframes pulse-red and pulse-glow are defined in base.css.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "base.html"
BASE_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "base.css"
MAIN_JS = REPO_ROOT / "hydra_detect" / "web" / "static" / "js" / "main.js"


class TestTopbarMarkup:
    def test_topbar_has_mock_data_attribute(self):
        html = BASE_HTML.read_text()
        assert 'class="topbar" data-mock="1"' in html, (
            "Topbar must carry data-mock=1 so base.css overrides win cascade."
        )

    def test_logo_lockup_elements_present(self):
        html = BASE_HTML.read_text()
        # Lockup container
        assert 'id="tb-lockup"' in html
        assert 'class="tb-lockup"' in html
        # SORCC shield SVG (brand invariant)
        assert 'class="tb-shield"' in html
        # HYDRA DETECT wordmark + .topbar-brand preserved (callsign swap target)
        assert 'class="topbar-brand tb-wordmark"' in html
        assert "HYDRA DETECT" in html
        # Operator Dashboard subtitle
        assert "OPERATOR DASHBOARD" in html
        assert 'class="tb-subtitle"' in html
        # OGT image
        assert 'id="tb-ogt"' in html
        assert "OGT_Horizontal_White.png" in html

    def test_six_health_blips_present(self):
        html = BASE_HTML.read_text()
        for blip in ("mav", "gps", "sim", "kis", "tak", "cam"):
            assert f'data-blip="{blip}"' in html, f"missing blip data-blip={blip!r}"
        # Each blip uses the tb-dot primitive
        assert html.count("tb-dot") >= 6

    def test_sim_pill_present_and_hidden(self):
        html = BASE_HTML.read_text()
        assert 'id="sim-gps-pill"' in html
        # The pill is the new tb-sim-pill styling and must be hidden by default
        assert 'class="tb-sim-pill sim-gps-pill"' in html
        # Look for the hidden attribute on that element
        pill_start = html.index('id="sim-gps-pill"')
        tag_start = html.rfind("<", 0, pill_start)
        tag_end = html.index(">", pill_start)
        tag = html[tag_start:tag_end]
        assert "hidden" in tag, f"SIM pill tag missing hidden attr: {tag!r}"

    def test_abort_button_present_with_danger_lg(self):
        html = BASE_HTML.read_text()
        assert 'id="tb-abort"' in html
        # btn-danger-lg is the spec-named class; btn btn-danger is the base
        assert "btn-danger-lg" in html
        # ABORT text + glyph
        assert "ABORT" in html

    def test_emergency_flash_overlay_present(self):
        html = BASE_HTML.read_text()
        assert 'id="emergency-flash"' in html
        # Sanity: the overlay is a standalone div (not nested inside topbar)
        idx = html.index('id="emergency-flash"')
        ctx = html[max(0, idx - 80):idx]
        assert "</header>" in ctx or "Emergency" in ctx


class TestTopbarCss:
    def test_pulse_red_keyframe_defined(self):
        css = BASE_CSS.read_text()
        assert "@keyframes pulse-red" in css

    def test_pulse_glow_keyframe_preserved(self):
        css = BASE_CSS.read_text()
        assert "@keyframes pulse-glow" in css

    def test_topbar_mock_height_and_gradient(self):
        css = BASE_CSS.read_text()
        assert '.topbar[data-mock="1"]' in css
        # Mock spec: height 64px and linear-gradient(180deg,#141414,#0a0a0a)
        start = css.index('.topbar[data-mock="1"] {')
        block = css[start:start + 800]
        assert "height: 64px" in block
        assert "linear-gradient(180deg, #141414, #0a0a0a)" in block

    def test_body_emerg_attr_drives_abort_and_flash(self):
        css = BASE_CSS.read_text()
        assert 'body[data-emerg="1"] .topbar[data-mock="1"] .tb-abort' in css
        assert 'body[data-emerg="1"] #emergency-flash' in css


class TestPreservationGuards:
    def test_callsign_swap_block_unchanged(self):
        """main.js:36-42 rewrites document.title and the topbar brand to
        `${callsign} — SORCC` on first /api/stats. Preservation rule says
        this block must remain verbatim across any topbar rewrite."""
        js = MAIN_JS.read_text()
        assert "if (data.callsign && !callsignSet) {" in js
        assert "document.querySelector('.topbar-brand')" in js
        assert "brandEl.textContent = `${data.callsign}`;" in js
        assert "document.title = `${data.callsign} — SORCC`;" in js
        assert "callsignSet = true;" in js

    def test_duplicate_callsign_warning_preserved(self):
        js = MAIN_JS.read_text()
        # main.js:45-48 multi-team collision warning
        assert "duplicate_callsign" in js
        assert "DUPLICATE CALLSIGN" in js

    def test_sentience_overlay_preserved(self):
        html = BASE_HTML.read_text()
        assert 'id="sentience-overlay"' in html
        assert 'id="sentience-terminal"' in html
        assert 'id="sentience-crosshair"' in html
        # Matrix-green glyph (⊕) should remain
        assert "⊕" in html

    def test_power_user_modal_preserved(self):
        html = BASE_HTML.read_text()
        assert 'id="power-user-modal"' in html
        assert 'id="power-user-cancel"' in html
        assert 'id="power-user-enable"' in html

    def test_footer_preserved(self):
        html = BASE_HTML.read_text()
        assert 'class="footer"' in html
        assert "UNCLASSIFIED" in html
        assert "SORCC Payload Integrator" in html
        assert 'id="footer-left"' in html
