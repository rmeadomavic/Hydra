"""Regression tests for topbar blip order + cockpit strip cell order.

Spec (from ~/Downloads/Hydra/design_handoff_hydra_alignment):
- primitives.jsx:121-132 — health blips left-to-right: CAM, MAV, GPS, KIS, TAK.
- ops-station.jsx:286-290 — cockpit strip grid-template-columns '260px 200px 1fr'
  with cells in DOM order: ServoPanDial, CockpitTakMap, CockpitSDR.

These tests pin the DOM order against future drift; a non-SIM `data-blip` may
appear after TAK (e.g. the existing SIM blip) without failing, but the five
named blips must appear in spec order relative to each other.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "base.html"
OPS_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "ops.html"


def _positions(haystack: str, needles: list[str]) -> list[int]:
    """Return byte offsets of first occurrence of each needle, in input order."""
    return [haystack.index(n) for n in needles]


class TestTopbarBlipOrder:
    def test_five_spec_blips_are_in_order_cam_mav_gps_kis_tak(self):
        html = BASE_HTML.read_text()
        spec = [
            'data-blip="cam"',
            'data-blip="mav"',
            'data-blip="gps"',
            'data-blip="kis"',
            'data-blip="tak"',
        ]
        positions = _positions(html, spec)
        assert positions == sorted(positions), (
            f"Spec blips out of order. Got offsets {positions} for {spec}; "
            "expected monotonically increasing (CAM · MAV · GPS · KIS · TAK)."
        )

    def test_sim_pill_is_sibling_of_blips(self):
        """SIM pill stays as separate element beside blips."""
        html = BASE_HTML.read_text()
        blips_start = html.index('class="tb-blips"')
        blips_end = html.index("</div>", blips_start)
        pill_pos = html.index('id="sim-gps-pill"')
        assert blips_start < pill_pos < blips_end, (
            "sim-gps-pill must live inside the tb-blips container (beside blips)."
        )


class TestCockpitStripOrder:
    def test_cockpit_cells_in_order_servo_tak_sdr(self):
        html = OPS_HTML.read_text()
        ids = [
            'id="ops-cockpit-servo"',
            'id="ops-cockpit-tak"',
            'id="ops-cockpit-sdr"',
        ]
        positions = _positions(html, ids)
        assert positions == sorted(positions), (
            f"Cockpit cells out of order. Got offsets {positions} for {ids}; "
            "expected servo · tak · sdr (matches 260px 200px 1fr grid)."
        )

    def test_cockpit_strip_container_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-cockpit-strip"' in html
        assert 'class="cockpit-strip"' in html
