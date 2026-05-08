"""Tests that the Phase 2 docs were actually landed.

Covers:
    (a) each public doc file exists under ``docs/``;
    (b) ``docs/api-reference.md`` mentions every endpoint declared with
        an ``@app.<method>`` decorator in ``hydra_detect/web/server.py``.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
SERVER_PY = REPO_ROOT / "hydra_detect" / "web" / "server.py"

_DECORATOR_RE = re.compile(
    r'^@app\.(?:get|post|put|delete|patch)\('
    r'\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)


def _declared_paths() -> list[str]:
    src = SERVER_PY.read_text(encoding="utf-8")
    return _DECORATOR_RE.findall(src)


def _normalize(path: str) -> str:
    """Strip path-parameter braces so ``/api/foo/{id}`` matches
    ``/api/foo/{track_id}`` in the docs."""
    return re.sub(r"\{[^}]+\}", "{}", path)


# ---------------------------------------------------------------------
# (a) doc files exist
# ---------------------------------------------------------------------


class TestDocsExist:
    def test_dashboard_user_guide_exists(self):
        assert (DOCS / "dashboard-user-guide.md").is_file()

    def test_architecture_exists(self):
        assert (DOCS / "architecture.md").is_file()

    def test_api_reference_exists(self):
        assert (DOCS / "api-reference.md").is_file()

    def test_readme_exists(self):
        assert (REPO_ROOT / "README.md").is_file()


# ---------------------------------------------------------------------
# (b) api-reference.md mentions every route declared in server.py
# ---------------------------------------------------------------------


class TestApiReferenceCoversEveryRoute:
    @classmethod
    def setup_class(cls):
        cls.body = (DOCS / "api-reference.md").read_text(encoding="utf-8")
        cls.normalized_body = _normalize(cls.body)

    def test_decorator_scan_finds_routes(self):
        # Sanity: the scan must find a non-trivial set of paths —
        # if this ever drops to zero, the regex has drifted.
        paths = _declared_paths()
        assert len(paths) >= 80, (
            f"expected >=80 decorated routes, found {len(paths)}"
        )

    def test_every_route_is_documented(self):
        missing: list[str] = []
        for path in _declared_paths():
            normalized = _normalize(path)
            if normalized not in self.normalized_body:
                missing.append(path)
        assert not missing, (
            "api-reference.md is missing these server.py routes: "
            + ", ".join(sorted(set(missing)))
        )

    def test_five_tonight_routes_documented(self):
        # The five endpoints shipped tonight — hard-pin so a future
        # rewrite cannot drop them.
        for route in (
            "/api/tak/type_counts",
            "/api/tak/peers",
            "/api/servo/status",
            "/api/rf/ambient_scan",
            "/api/audit/summary",
        ):
            assert route in self.body, f"api-reference.md missing {route}"

    def test_autonomy_routes_documented(self):
        for route in ("/api/autonomy/status", "/api/autonomy/mode"):
            assert route in self.body, f"api-reference.md missing {route}"
