"""Smoke tests for the Systems top-level view.

The orchestrator wires the `#systems` nav button and `{% include %}` into
base.html in a follow-up integration step, so these tests scope themselves
to the OWNED files for this workstream:
- hydra_detect/web/templates/systems.html (partial renders standalone)
- hydra_detect/web/static/js/systems.js  (served + IIFE shape)
- hydra_detect/web/static/css/systems.css (served + uses post-migration tokens)
- /api/stats shape covers the metric fields the view consumes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, stream_state


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Template partial — exists, contains the structural anchors HydraSystems
# expects. Rendered standalone via Jinja so we don't depend on base.html.
# ---------------------------------------------------------------------------

class TestSystemsTemplate:
    def test_partial_exists(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "hydra_detect" / "web" / "templates" / "systems.html"
        )
        assert path.is_file(), "systems.html partial must exist"

    def test_partial_has_metric_card_anchors(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "hydra_detect" / "web" / "templates" / "systems.html"
        )
        body = path.read_text(encoding="utf-8")
        # Anchors that systems.js writes into.
        for anchor in (
            'id="systems-card-fps"',
            'id="systems-card-cpu"',
            'id="systems-card-gpu"',
            'id="systems-card-ram"',
            'id="systems-fps-value"',
            'id="systems-cpu-value"',
            'id="systems-gpu-value"',
            'id="systems-ram-value"',
            'id="systems-status-chip"',
        ):
            assert anchor in body, f"systems.html missing anchor {anchor}"

    def test_partial_has_subsystem_rows(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "hydra_detect" / "web" / "templates" / "systems.html"
        )
        body = path.read_text(encoding="utf-8")
        # MAVLink, RTSP, pipeline status are the task-required subsystems.
        for key in ("mavlink", "rtsp", "pipeline"):
            assert f'data-key="{key}"' in body, f"systems.html missing subsystem row {key}"

    def test_partial_has_aria_labels(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "hydra_detect" / "web" / "templates" / "systems.html"
        )
        body = path.read_text(encoding="utf-8")
        assert 'aria-label="Pipeline FPS metric"' in body
        # Sparklines must declare role + descriptive aria-label.
        assert body.count('role="img"') >= 4
        assert "trend over the last 60 samples" in body


# ---------------------------------------------------------------------------
# JS module — served, is an IIFE, exposes HydraSystems with onEnter/onLeave.
# ---------------------------------------------------------------------------

class TestSystemsJs:
    def test_systems_js_served(self, client):
        resp = client.get("/static/js/systems.js")
        assert resp.status_code == 200

    def test_systems_js_iife_shape(self, client):
        body = client.get("/static/js/systems.js").text
        # IIFE pattern matching HydraTak / HydraOps style.
        assert "const HydraSystems = (() => {" in body
        assert "return { onEnter, onLeave };" in body
        # window export so main.js can dispatch by name.
        assert "window.HydraSystems = HydraSystems" in body

    def test_systems_js_polls_api_stats_only(self, client):
        body = client.get("/static/js/systems.js").text
        # This task scope: only /api/stats. Any other /api/... call would mean
        # we invented a backend endpoint without flagging it — fail the test.
        api_calls = [line.strip() for line in body.splitlines() if "fetch('/api/" in line]
        assert api_calls, "systems.js should at least poll /api/stats"
        for call in api_calls:
            assert "/api/stats" in call, (
                f"systems.js fetches a non-/api/stats endpoint: {call!r} — "
                "if a new backend endpoint is needed, flag in /tmp/messages/"
            )


# ---------------------------------------------------------------------------
# CSS — served, uses post-migration tokens only (no deprecated ones).
# ---------------------------------------------------------------------------

class TestSystemsCss:
    DEPRECATED_TOKENS = (
        "--ogt-green",
        "--panel-bg",
        "--bg-secondary",
        "--text-secondary",
        "var(--gap-",  # legacy gap- spacing scale
    )

    def test_systems_css_served(self, client):
        resp = client.get("/static/css/systems.css")
        assert resp.status_code == 200

    def test_systems_css_no_deprecated_tokens(self, client):
        body = client.get("/static/css/systems.css").text
        for tok in self.DEPRECATED_TOKENS:
            assert tok not in body, (
                f"systems.css references deprecated token {tok!r}"
            )

    def test_systems_css_uses_flat_radii(self, client):
        body = client.get("/static/css/systems.css").text
        # Radii must come from var(--radius...) — no inline px radii.
        # Allow border-radius: 50% (the dot circle marker).
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("border-radius"):
                continue
            assert (
                "var(--radius" in stripped
                or "50%" in stripped
            ), f"systems.css uses raw border-radius outside design system: {stripped!r}"


# ---------------------------------------------------------------------------
# /api/stats — confirm the shape the systems view depends on. Existing
# test_web_api covers the 200 + "fps" key; here we just exercise the union
# of fields that HydraSystems applies.
# ---------------------------------------------------------------------------

class TestStatsShapeForSystemsView:
    def test_stats_returns_metric_fields_after_pipeline_publishes(self, client):
        stream_state.update_stats(
            fps=12.4,
            inference_ms=42.0,
            cpu_temp_c=58.2,
            gpu_temp_c=66.1,
            ram_used_mb=4200,
            ram_total_mb=8000,
            mavlink=True,
            gps_fix=3,
            detector="yolo",
            rtsp_clients=1,
        )
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "fps", "inference_ms",
            "cpu_temp_c", "gpu_temp_c",
            "ram_used_mb", "ram_total_mb",
            "mavlink", "gps_fix", "detector",
        ):
            assert key in body, f"/api/stats missing {key} after publish"
