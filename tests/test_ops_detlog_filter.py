"""Lexical tests for the #ops-detlog client-side filter (class + min confidence + clear).

Covers:
  a) ops.html contains the filter UI (class <select>, range input, clear button)
  b) ops.js contains the localStorage key pattern `hydra-detlog-filter-<callsign>`
  c) ops.js reads persisted filter on load (loadDetlogFilter hook)
  d) clear button handler is wired

No headless JS runtime — presence-level assertions are sufficient for the
wave and match the sibling `test_ops_sidebar.py` style.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPS_HTML = REPO_ROOT / "hydra_detect" / "web" / "templates" / "ops.html"
OPS_JS = REPO_ROOT / "hydra_detect" / "web" / "static" / "js" / "ops.js"
OPS_SIDEBAR_CSS = REPO_ROOT / "hydra_detect" / "web" / "static" / "css" / "ops-sidebar.css"


# ── (a) ops.html contains the filter UI ──

class TestDetlogFilterUi:
    def test_filter_container_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-detlog-filter"' in html

    def test_class_select_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-detlog-class"' in html
        assert "<select" in html
        # Default "All" option renders before any class is known.
        assert ">All</option>" in html

    def test_class_select_has_accessible_label(self):
        html = OPS_HTML.read_text()
        # Either a <label for=> or aria-label satisfies accessibility — we
        # ship both for the class selector.
        assert 'for="ops-detlog-class"' in html
        assert 'aria-label="Filter detections by class"' in html

    def test_confidence_range_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-detlog-conf"' in html
        assert 'type="range"' in html
        assert 'min="0"' in html
        assert 'max="1"' in html
        assert 'step="0.05"' in html

    def test_confidence_range_has_aria_label(self):
        html = OPS_HTML.read_text()
        assert 'aria-label="Minimum detection confidence"' in html

    def test_clear_button_present(self):
        html = OPS_HTML.read_text()
        assert 'id="ops-detlog-clear"' in html
        assert ">clear</button>" in html


# ── (b) ops.js contains the localStorage key pattern ──

class TestDetlogFilterStorageKey:
    def test_storage_key_prefix(self):
        js = OPS_JS.read_text()
        assert "'hydra-detlog-filter-'" in js

    def test_callsign_fallback_default(self):
        js = OPS_JS.read_text()
        # Callsign resolver must fall back to 'default' when state.callsign
        # is unset — this keeps the key valid on first boot before /api/stats
        # has populated the store.
        assert "'default'" in js
        assert "detlogCallsign" in js

    def test_callsign_sourced_from_hydraapp_state(self):
        js = OPS_JS.read_text()
        assert "HydraApp" in js
        assert "state.callsign" in js or "app.state.callsign" in js


# ── (c) ops.js reads persisted filter on load ──

class TestDetlogFilterPersistenceLoad:
    def test_load_function_defined(self):
        js = OPS_JS.read_text()
        assert "function loadDetlogFilter" in js

    def test_load_called_from_on_enter(self):
        js = OPS_JS.read_text()
        # Lifecycle hook must invoke loadDetlogFilter before the first render
        # so persisted selections apply immediately on view entry.
        assert "loadDetlogFilter()" in js
        # Verify it's inside onEnter specifically.
        on_enter_idx = js.index("function onEnter")
        next_fn_idx = js.index("function onLeave")
        assert "loadDetlogFilter()" in js[on_enter_idx:next_fn_idx]

    def test_load_uses_localstorage_get(self):
        js = OPS_JS.read_text()
        assert "localStorage.getItem" in js

    def test_load_parses_saved_json(self):
        js = OPS_JS.read_text()
        assert "JSON.parse" in js

    def test_save_writes_localstorage(self):
        js = OPS_JS.read_text()
        assert "localStorage.setItem" in js
        assert "JSON.stringify" in js


# ── (d) clear button handler wired ──

class TestDetlogFilterClearWiring:
    def test_clear_function_defined(self):
        js = OPS_JS.read_text()
        assert "function clearDetlogFilter" in js

    def test_clear_removes_localstorage_entry(self):
        js = OPS_JS.read_text()
        assert "localStorage.removeItem" in js

    def test_clear_button_event_bound(self):
        js = OPS_JS.read_text()
        # Handler wired to the clear button id in wireDetlogFilter.
        assert "'ops-detlog-clear'" in js
        assert "addEventListener('click', clearDetlogFilter)" in js

    def test_filter_controls_bound_to_change_and_input(self):
        js = OPS_JS.read_text()
        # Class select uses 'change', range slider uses 'input' for
        # live updates while dragging.
        assert "addEventListener('change'" in js
        assert "addEventListener('input'" in js


# ── filter logic / CSS regression guards ──

class TestDetlogFilterRendering:
    def test_filter_applied_in_update_sidebar_detlog(self):
        js = OPS_JS.read_text()
        # The filter block must live inside the detlog updater — the task
        # requires re-filtering on every poll tick.
        update_idx = js.index("function updateSidebarDetLog")
        # Find the next top-level function to bound the search.
        tail_idx = js.index("function ", update_idx + 1)
        body = js[update_idx:tail_idx]
        assert "detlogFilter" in body
        assert "minConf" in body

    def test_css_has_filter_row_class(self):
        css = OPS_SIDEBAR_CSS.read_text()
        assert ".ops-detlog-filter" in css
        assert ".ops-detlog-filter-row" in css
        assert ".ops-detlog-filter-clear" in css

    def test_original_detlog_rendering_preserved(self):
        js = OPS_JS.read_text()
        # Regression guard: empty-state message and entry DOM structure must
        # still exist after the filter rewrite.
        assert "'No detections yet'" in js
        assert "ops-detlog-entry" in js
