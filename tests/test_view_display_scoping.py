"""Regression test: no CSS file may set an unconditional display property
on a top-level view container by id. Such rules override the default
.view { display: none } hide rule and cause view content to stack.

Bug caught 2026-04-20 overnight: #view-ops and #view-config had
unconditional display rules, so config.html's operations-panels rendered
in the #ops view's right column at the same time as the new sidebar.

Fix pattern: scope to body.view-X #view-X, not bare #view-X.
"""
import re
from pathlib import Path

import pytest

CSS_DIR = Path(__file__).parent.parent / "hydra_detect" / "web" / "static" / "css"
VIEW_IDS = ("ops", "config", "settings", "tak", "systems", "autonomy")


def _css_files():
    return sorted(CSS_DIR.glob("*.css"))


@pytest.mark.parametrize("view_id", VIEW_IDS)
def test_no_unconditional_view_display_rule(view_id):
    """No CSS file may open a selector with bare #view-X { ... display: ... }.

    The rule must be scoped to body.view-X #view-X or descendant-only
    (#view-X > .thing) so it never competes with the hide rule.
    """
    pattern = re.compile(rf"^\s*#view-{view_id}\s*\{{", re.MULTILINE)
    offenders = []
    for css in _css_files():
        text = css.read_text()
        if pattern.search(text):
            # Verify the offending block contains a display declaration
            # (descendant-only rules like #view-X > .foo {} are allowed,
            # but those don't match the pattern above anyway).
            offenders.append(css.name)
    assert not offenders, (
        f"Unconditional #view-{view_id} rule in {offenders} overrides the "
        f".view hide rule. Prefix with body.view-{view_id} instead."
    )


def test_hide_rule_still_present():
    """The baseline .view { display: none } must exist so the id-scoped
    show rules have something to override."""
    topbar = (CSS_DIR / "topbar.css").read_text()
    assert re.search(r"\.view\s*\{\s*display:\s*none\s*;?\s*\}", topbar), (
        "static/css/topbar.css must define .view { display: none }"
    )


@pytest.mark.parametrize("view_id", VIEW_IDS)
def test_show_rule_scoped_to_body_class(view_id):
    """Each view must have a body.view-X .view.view-X { display: ... } rule
    so only the active view renders."""
    pattern = re.compile(
        rf"body\.view-{view_id}\s+(\.view\.view-{view_id}|#view-{view_id})\s*\{{"
    )
    found_in = [css.name for css in _css_files() if pattern.search(css.read_text())]
    assert found_in, (
        f"No show rule found for #view-{view_id}. Add "
        f"body.view-{view_id} #view-{view_id} {{ display: ... }}"
    )
