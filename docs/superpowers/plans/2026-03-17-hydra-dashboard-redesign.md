# Hydra Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Hydra web dashboard from a single-page scrolling sidebar into a three-view SPA (Monitor, Control, Settings) with dockable panels, refined SORCC aesthetics, and in-browser config editing.

**Architecture:** Single-page app served by FastAPI/Jinja2 using `{% include %}` to compose views. Client-side hash routing (`/#monitor`, `/#control`, `/#settings`) shows/hides view sections. MJPEG `<img>` lives in `base.html` with `position: fixed`, repositioned per-view via body CSS classes. SortableJS (vendored) handles panel drag-and-drop.

**Tech Stack:** Python 3.10+ / FastAPI / Jinja2 / vanilla JS / CSS custom properties / SortableJS (vendored)

**Spec:** `docs/superpowers/specs/2026-03-17-hydra-dashboard-redesign-design.md`

---

## File Map

### New Files

| File | Responsibility |
|------|---------------|
| `hydra_detect/web/static/css/variables.css` | SORCC design tokens (colors, type scale, radii, spacing) |
| `hydra_detect/web/static/css/base.css` | Reset, typography, shared components (buttons, pills, modals, toasts) |
| `hydra_detect/web/static/css/topbar.css` | Persistent top bar + footer |
| `hydra_detect/web/static/css/monitor.css` | Monitor view: overlays, auto-hide, quick actions, presentation mode |
| `hydra_detect/web/static/css/control.css` | Control view: panel grid, panel chrome, collapse states |
| `hydra_detect/web/static/css/settings.css` | Settings view: section nav, form layout, warning banners |
| `hydra_detect/web/static/js/app.js` | View router, polling coordinator, shared state, MJPEG management |
| `hydra_detect/web/static/js/monitor.js` | Overlay auto-hide, quick action handlers, presentation mode toggle |
| `hydra_detect/web/static/js/panels.js` | Panel collapse/expand, drag reorder (SortableJS), localStorage persistence |
| `hydra_detect/web/static/js/control.js` | Control view panel-specific logic (vehicle, target, RF, detection config, log) |
| `hydra_detect/web/static/js/settings.js` | Config form: load, validate, apply, reset, restore backup |
| `hydra_detect/web/static/js/vendor/Sortable.min.js` | Vendored SortableJS library (~10KB gzipped) |
| `hydra_detect/web/templates/base.html` | SPA shell: HTML head, top bar, MJPEG img, view containers, footer |
| `hydra_detect/web/templates/monitor.html` | Monitor view: overlay markup, quick action toolbar |
| `hydra_detect/web/templates/control.html` | Control view: panel grid with 6 default panels |
| `hydra_detect/web/templates/settings.html` | Settings view: section nav + form containers |
| `hydra_detect/web/config_api.py` | Config read/write logic: parse ini, serialize JSON, atomic write, file locking |
| `tests/test_config_api.py` | Tests for config read/write endpoints |

### Modified Files

| File | Changes |
|------|---------|
| `hydra_detect/web/server.py` | Add static file mount, config API endpoints, update `GET /` to serve `base.html` |

### Unchanged

| File | Note |
|------|------|
| `hydra_detect/web/templates/review.html` | Standalone, not part of the SPA |

---

## Task 1: Static File Serving + CSS Design System

**Files:**
- Create: `hydra_detect/web/static/css/variables.css`
- Create: `hydra_detect/web/static/css/base.css`
- Modify: `hydra_detect/web/server.py:1-34` (add StaticFiles mount)
- Test: `tests/test_web_api.py` (add static serving test)

This task establishes the foundation: static file serving and the CSS design token system that all views depend on.

- [ ] **Step 1: Write test for static file serving**

Add to `tests/test_web_api.py`:

```python
class TestStaticFileServing:
    def test_css_variables_served(self, client):
        resp = client.get("/static/css/variables.css")
        assert resp.status_code == 200
        assert "ogt-green" in resp.text

    def test_missing_static_file_404(self, client):
        resp = client.get("/static/nonexistent.css")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_api.py::TestStaticFileServing -v`
Expected: FAIL — no `/static/` route exists.

- [ ] **Step 3: Add StaticFiles mount to server.py**

In `hydra_detect/web/server.py`, add the import and mount. After the existing `TEMPLATE_DIR` line, add:

```python
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"
```

After the CORS middleware block (after line 32), add:

```python
# Static files — CSS, JS, vendored libraries
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
```

- [ ] **Step 4: Create the static directory structure**

Run:
```bash
mkdir -p hydra_detect/web/static/css
mkdir -p hydra_detect/web/static/js/vendor
```

- [ ] **Step 5: Create variables.css**

Create `hydra_detect/web/static/css/variables.css` with all design tokens from the spec. Extract from the existing `index.html` `:root` block and add the new refined tokens:

```css
/* SORCC Design System — Hydra Detect v2.0 */
:root {
    /* ── Brand Greens ── */
    --ogt-green: #385723;
    --ogt-green-dark: #2a4118;
    --ogt-muted: #A6BC92;
    --ogt-warm: #D8E2D0;
    --ogt-light: #EFF5EB;

    /* ── Dark Base ── */
    --panel-bg: #0c0c0c;
    --sidebar-bg: #141414;
    --card-bg: #1c1c1c;
    --card-bg-gradient: linear-gradient(145deg, #1c1c1c, #1a1f18);
    --card-border: #262626;

    /* ── Text ── */
    --text-primary: #e8e8e8;
    --text-secondary: #888;
    --text-dim: #555;

    /* ── Status Colors ── */
    --danger: #c53030;
    --danger-bg: #2a1010;
    --warning: #b45309;
    --success: #385723;

    /* ── Accent Glow ── */
    --glow-green: 0 0 12px rgba(56, 87, 35, 0.3);
    --glow-danger: 0 0 12px rgba(197, 48, 48, 0.3);

    /* ── Border Radii ── */
    --radius-sm: 4px;
    --radius-md: 6px;
    --radius-lg: 8px;
    --radius-xl: 12px;

    /* ── Type Scale ── */
    --font-xs: 0.65rem;
    --font-sm: 0.75rem;
    --font-base: 0.85rem;
    --font-md: 1rem;
    --font-lg: 1.2rem;
    --font-xl: 1.5rem;

    /* ── Letter Spacing ── */
    --ls-condensed: 0.08em;
    --ls-body: 0;
    --ls-mono: 0.02em;

    /* ── Spacing ── */
    --gap-xs: 4px;
    --gap-sm: 8px;
    --gap-md: 12px;
    --gap-lg: 16px;
    --gap-xl: 24px;

    /* ── Transitions ── */
    --transition-fast: 100ms ease-out;
    --transition-normal: 200ms ease-out;
    --transition-slow: 300ms ease-out;

    /* ── Layout ── */
    --topbar-height: 48px;
    --footer-height: 28px;
    --sidebar-width: 310px;
    --settings-nav-width: 160px;

    /* ── Overlay ── */
    --overlay-bg: rgba(0, 0, 0, 0.75);
    --overlay-blur: blur(8px);
}
```

- [ ] **Step 6: Create base.css**

Create `hydra_detect/web/static/css/base.css` with reset, typography, and shared component styles:

```css
/* Note: variables.css is loaded via <link> in base.html. No @import needed. */

/* ── Reset ── */
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Barlow', 'Segoe UI', system-ui, sans-serif;
    font-size: var(--font-base);
    background: var(--panel-bg);
    color: var(--text-primary);
    line-height: 1.4;
    overflow: hidden;
    height: 100vh;
}

/* ── Typography ── */
h1, h2, h3, .label-heading {
    font-family: 'Barlow Condensed', 'Barlow', sans-serif;
    text-transform: uppercase;
    letter-spacing: var(--ls-condensed);
    font-weight: 600;
}
.mono, code, .stat-value {
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: var(--ls-mono);
}

/* ── Buttons ── */
.btn {
    font-family: 'Barlow Condensed', sans-serif;
    text-transform: uppercase;
    letter-spacing: var(--ls-condensed);
    font-size: var(--font-sm);
    font-weight: 600;
    padding: 6px 14px;
    border: 1px solid var(--card-border);
    border-radius: var(--radius-md);
    background: linear-gradient(180deg, #2a2a2a, #1e1e1e);
    color: var(--text-primary);
    cursor: pointer;
    transition: filter var(--transition-fast), transform var(--transition-fast);
    min-height: 32px;
}
.btn:hover { filter: brightness(1.2); }
.btn:active { transform: scale(0.97); }
.btn:focus-visible { outline: 2px solid var(--ogt-muted); outline-offset: 2px; }

.btn-green {
    background: linear-gradient(180deg, var(--ogt-green), var(--ogt-green-dark));
    border-color: var(--ogt-green-dark);
}
.btn-danger {
    background: linear-gradient(180deg, #8b2020, #6b1818);
    border-color: var(--danger);
    color: #fca5a5;
}

/* ── Pills / Badges ── */
.pill {
    display: inline-flex;
    align-items: center;
    gap: var(--gap-xs);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: var(--font-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: var(--ls-condensed);
    padding: 2px 8px;
    border-radius: 999px;
}
.pill-live {
    background: var(--ogt-green);
    color: var(--ogt-light);
    animation: pulse-glow 2s ease-in-out infinite;
}
.pill-offline {
    background: var(--danger);
    color: #fca5a5;
}

@keyframes pulse-glow {
    0%, 100% { box-shadow: 0 0 4px rgba(56, 87, 35, 0.3); }
    50% { box-shadow: 0 0 12px rgba(56, 87, 35, 0.6); }
}

/* ── Form Inputs ── */
.input, select, input[type="text"], input[type="number"], input[type="password"] {
    font-family: 'JetBrains Mono', monospace;
    font-size: var(--font-sm);
    padding: 6px 10px;
    border: 1px solid var(--card-border);
    border-radius: var(--radius-md);
    background: #111;
    color: var(--text-primary);
    min-height: 32px;
}
input:focus, select:focus {
    outline: none;
    border-color: var(--ogt-green);
    box-shadow: var(--glow-green);
}

/* ── Slider ── */
input[type="range"] {
    -webkit-appearance: none;
    width: 100%;
    height: 4px;
    background: var(--card-border);
    border-radius: 2px;
    outline: none;
}
input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--ogt-green);
    cursor: pointer;
    border: 2px solid var(--ogt-green-dark);
}

/* ── Modal ── */
.modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    z-index: 1000;
    align-items: center;
    justify-content: center;
}
.modal-overlay.active { display: flex; }
.modal {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: var(--radius-xl);
    padding: var(--gap-xl);
    max-width: 420px;
    width: 90%;
}

/* ── Toast Notifications ── */
.toast-container {
    position: fixed;
    top: calc(var(--topbar-height) + var(--gap-md));
    right: var(--gap-md);
    z-index: 900;
    display: flex;
    flex-direction: column;
    gap: var(--gap-sm);
    pointer-events: none;
}
.toast {
    pointer-events: auto;
    background: var(--danger-bg);
    border: 1px solid var(--danger);
    border-radius: var(--radius-lg);
    padding: var(--gap-sm) var(--gap-md);
    color: #fca5a5;
    font-size: var(--font-sm);
    max-width: 320px;
    animation: toast-in 300ms cubic-bezier(0.34, 1.56, 0.64, 1);
    cursor: pointer;
}
.toast.dismissing {
    animation: toast-out 200ms ease-in forwards;
}
@keyframes toast-in {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
}
@keyframes toast-out {
    from { transform: translateX(0); opacity: 1; }
    to { transform: translateX(100%); opacity: 0; }
}

/* ── Hex Pattern Overlay ── */
.hex-pattern {
    position: absolute;
    inset: 0;
    pointer-events: none;
    opacity: 0.04;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='28' height='49' viewBox='0 0 28 49'%3E%3Cg fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='1'%3E%3Cpath d='M13.99 9.25l13 7.5v15l-13 7.5L1 31.75v-15l12.99-7.5zM3 17.9v12.7l10.99 6.34 11-6.35V17.9l-11-6.34L3 17.9zM0 15l12.98-7.5V0h-2v6.35L0 12.69v2.3zm0 18.5L12.98 41v8h-2v-6.85L0 35.81v-2.3zM15 0v7.5L27.99 15H28v-2.31h-.01L17 6.35V0h-2zm0 49v-8l12.99-7.5H28v2.31h-.01L17 42.15V49h-2z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
}

/* ── Color Bars (GPU/CPU/RAM) ── */
.color-bar {
    height: 4px;
    border-radius: 2px;
    background: var(--card-border);
    overflow: hidden;
}
.color-bar-fill {
    height: 100%;
    border-radius: 2px;
    transition: width var(--transition-normal), background-color var(--transition-normal);
}

/* ── Utility ── */
.visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}

/* ── Touch Target (Steam Deck / tablet) ── */
@media (pointer: coarse) {
    .btn { min-height: 44px; min-width: 44px; padding: 10px 16px; }
    input[type="range"]::-webkit-slider-thumb { width: 24px; height: 24px; }
}
```

- [ ] **Step 7: Run test to verify static serving works**

Run: `python -m pytest tests/test_web_api.py::TestStaticFileServing -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add hydra_detect/web/static/ hydra_detect/web/server.py tests/test_web_api.py
git commit -m "feat(web): add static file serving and SORCC CSS design system"
```

---

## Task 2: Config API Backend

**Files:**
- Create: `hydra_detect/web/config_api.py`
- Modify: `hydra_detect/web/server.py` (add config endpoints)
- Create: `tests/test_config_api.py`

This task adds the `GET/POST /api/config/full` endpoints with atomic writes, file locking, auth, token redaction, and body size limits.

- [ ] **Step 1: Write failing tests for config API**

Create `tests/test_config_api.py`:

```python
"""Tests for the full config read/write API endpoints."""

from __future__ import annotations

import configparser
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state


@pytest.fixture(autouse=True)
def _reset_state():
    configure_auth(None)
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config.ini for testing."""
    config = configparser.ConfigParser()
    config["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "30"}
    config["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.45"}
    config["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": "secret-test-token"}
    config["tracker"] = {"track_thresh": "0.5", "track_buffer": "30"}
    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        config.write(f)
    return path


class TestConfigGetEndpoint:
    def test_get_config_returns_all_sections(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200
        data = resp.json()
        assert "camera" in data
        assert "detector" in data
        assert data["camera"]["source"] == "auto"

    def test_get_config_redacts_api_token(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200
        assert resp.json()["web"]["api_token"] == "***"

    def test_get_config_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token")
        resp = client.get("/api/config/full")
        assert resp.status_code == 401


class TestConfigPostEndpoint:
    def test_post_config_writes_values(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "camera": {"fps": "15"},
            })
        assert resp.status_code == 200
        # Verify the file was actually updated
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["fps"] == "15"
        # Verify untouched values preserved
        assert config["camera"]["source"] == "auto"

    def test_post_config_preserves_token_on_masked_value(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "web": {"api_token": "***"},
            })
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["web"]["api_token"] == "secret-test-token"

    def test_post_config_creates_backup(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "camera": {"fps": "15"},
            })
        assert resp.status_code == 200
        assert (tmp_config.parent / "config.ini.bak").exists()

    def test_post_config_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token")
        resp = client.post("/api/config/full", json={"camera": {"fps": "15"}})
        assert resp.status_code == 401

    def test_post_config_rejects_oversized_body(self, client, tmp_config):
        # 64KB limit
        huge = {"camera": {"source": "x" * 70000}}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json=huge)
        assert resp.status_code == 413

    def test_post_config_returns_restart_required_fields(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "web": {"port": "9090"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "restart_required" in data
        assert any("port" in f for f in data["restart_required"])

    def test_post_config_reports_skipped_fields(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "nonexistent_section": {"foo": "bar"},
                "camera": {"nonexistent_field": "baz"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skipped"]) == 2


class TestConfigAuthPositiveCases:
    def test_get_config_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full", headers=headers)
        assert resp.status_code == 200
        assert "camera" in resp.json()

    def test_post_config_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={"camera": {"fps": "15"}}, headers=headers)
        assert resp.status_code == 200


class TestConfigAtomicWrite:
    def test_failed_write_does_not_corrupt_original(self, client, tmp_config):
        """If write fails (e.g., bad permissions on dir), original config is intact."""
        original_content = tmp_config.read_text()
        # Make the directory read-only to force write failure
        import stat
        tmp_config.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
                resp = client.post("/api/config/full", json={"camera": {"fps": "15"}})
            assert resp.status_code == 500
            # Restore permissions and verify original is intact
            tmp_config.parent.chmod(stat.S_IRWXU)
            assert tmp_config.read_text() == original_content
        finally:
            tmp_config.parent.chmod(stat.S_IRWXU)


class TestConfigRestoreBackup:
    def test_restore_backup_works(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            # First, make a change (creates backup)
            client.post("/api/config/full", json={"camera": {"fps": "15"}})
            # Now restore
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["fps"] == "30"  # original value

    def test_restore_backup_no_backup(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config_api.py -v`
Expected: FAIL — `config_api` module does not exist, endpoints don't exist.

- [ ] **Step 3: Create config_api.py**

Create `hydra_detect/web/config_api.py`:

```python
"""Config file read/write with atomic writes and file locking for Jetson safety."""

from __future__ import annotations

import configparser
import fcntl
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default config path — can be overridden by pipeline at startup
_config_path: Path | None = None

# Fields that require a service restart to take effect
RESTART_REQUIRED_FIELDS = {
    "web": {"host", "port"},
    "mavlink": {"connection_string", "baud", "source_system"},
    "camera": {"source", "width", "height"},
    "detector": {"yolo_model"},
}

# Fields that must be redacted in GET responses
REDACTED_FIELDS = {
    "web": {"api_token"},
    "rf_homing": {"kismet_pass"},
}

REDACTED_VALUE = "***"
MAX_BODY_SIZE = 65536  # 64KB


def set_config_path(path: Path | str) -> None:
    """Set the config.ini path (called by pipeline at startup)."""
    global _config_path
    _config_path = Path(path)


def get_config_path() -> Path:
    """Return the current config.ini path."""
    if _config_path is None:
        return Path("config.ini")
    return _config_path


def read_config() -> dict[str, dict[str, str]]:
    """Read config.ini and return as nested dict. Redacts sensitive fields."""
    path = get_config_path()
    config = configparser.ConfigParser()
    config.read(path)

    result: dict[str, dict[str, str]] = {}
    for section in config.sections():
        result[section] = dict(config[section])
        # Redact sensitive fields
        if section in REDACTED_FIELDS:
            for field in REDACTED_FIELDS[section]:
                if field in result[section] and result[section][field]:
                    result[section][field] = REDACTED_VALUE

    return result


def write_config(updates: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Merge updates into config.ini with atomic write and file locking.

    Returns a dict with 'restart_required' and 'skipped' lists.
    """
    path = get_config_path()
    restart_needed: list[str] = []
    skipped: list[str] = []

    # Read current config
    config = configparser.ConfigParser()
    config.read(path)

    # Apply updates
    for section, fields in updates.items():
        if not isinstance(fields, dict):
            continue
        if not config.has_section(section):
            skipped.append(f"{section} (unknown section)")
            continue
        for key, value in fields.items():
            if not isinstance(value, str):
                value = str(value)
            # Skip redacted placeholder — preserve existing value
            if section in REDACTED_FIELDS and key in REDACTED_FIELDS[section]:
                if value == REDACTED_VALUE:
                    continue
            if not config.has_option(section, key):
                skipped.append(f"{section}.{key} (unknown field)")
                continue
            old_value = config.get(section, key)
            if old_value != value:
                config.set(section, key, value)
                # Check if restart required
                if section in RESTART_REQUIRED_FIELDS and key in RESTART_REQUIRED_FIELDS[section]:
                    restart_needed.append(f"{section}.{key}")

    # Backup existing file
    bak_path = Path(str(path) + ".bak")
    if path.exists():
        shutil.copy2(path, bak_path)

    # Atomic write with file locking on the TARGET file (not temp file).
    # This prevents concurrent writes from multiple browser sessions.
    dir_path = path.parent
    lock_fd = os.open(str(path), os.O_RDONLY | os.O_CREAT)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                config.write(f)
            os.replace(tmp_path, path)
            logger.info("Config written to %s (%d fields updated)", path, len(restart_needed))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    return {"restart_required": restart_needed, "skipped": skipped}


def restore_backup() -> bool:
    """Restore config.ini from config.ini.bak. Returns True on success."""
    path = get_config_path()
    bak_path = Path(str(path) + ".bak")
    if not bak_path.exists():
        return False
    shutil.copy2(bak_path, path)
    logger.info("Config restored from backup: %s", bak_path)
    return True


def has_backup() -> bool:
    """Check if a config backup exists."""
    path = get_config_path()
    return Path(str(path) + ".bak").exists()
```

- [ ] **Step 4: Add config endpoints to server.py**

In `hydra_detect/web/server.py`, add after the existing imports:

```python
from hydra_detect.web.config_api import (
    MAX_BODY_SIZE,
    has_backup,
    read_config,
    restore_backup,
    write_config,
)
```

Add the endpoints before the `# ── Server launcher ──` section:

```python
# ── Full Config ────────────────────────────────────────────────

@app.get("/api/config/full")
async def api_get_full_config(authorization: str | None = Header(None)):
    """Return all config.ini sections as JSON. Sensitive fields are redacted."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    try:
        return read_config()
    except Exception as e:
        logger.error("Failed to read config: %s", e)
        return JSONResponse({"error": "Failed to read configuration"}, status_code=500)


@app.post("/api/config/full")
async def api_set_full_config(request: Request, authorization: str | None = Header(None)):
    """Update config.ini fields. Returns list of fields requiring restart."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    # Enforce body size limit: read raw bytes first, reject before parsing.
    # This prevents a malicious client from omitting Content-Length and
    # sending a huge payload that exhausts Jetson RAM.
    import json as _json
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_SIZE:
        return JSONResponse({"error": "Request body too large"}, status_code=413)
    try:
        body = _json.loads(body_bytes)
    except (ValueError, _json.JSONDecodeError):
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        result = write_config(body)
        _audit(request, "config_update", target=str(len(body)))
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("Failed to write config: %s", e)
        return JSONResponse({"error": f"Failed to save configuration: {e}"}, status_code=500)


@app.post("/api/config/restore-backup")
async def api_restore_config_backup(request: Request, authorization: str | None = Header(None)):
    """Restore config.ini from backup."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    if not has_backup():
        return JSONResponse({"error": "No backup file exists"}, status_code=404)
    try:
        restore_backup()
        _audit(request, "config_restore_backup")
        return {"status": "ok", "message": "Configuration restored from backup"}
    except Exception as e:
        logger.error("Failed to restore config backup: %s", e)
        return JSONResponse({"error": f"Failed to restore: {e}"}, status_code=500)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_api.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `python -m pytest tests/ -v`
Expected: All existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add hydra_detect/web/config_api.py hydra_detect/web/server.py tests/test_config_api.py
git commit -m "feat(web): add config read/write API with atomic writes and auth"
```

---

## Task 3: SPA Shell (base.html + View Router)

**Files:**
- Create: `hydra_detect/web/templates/base.html`
- Create: `hydra_detect/web/static/css/topbar.css`
- Create: `hydra_detect/web/static/js/app.js`
- Modify: `hydra_detect/web/server.py` (update `GET /` route)
- Test: `tests/test_web_api.py` (add SPA shell test)

This task creates the SPA shell: top bar, footer, MJPEG `<img>` element, view containers, and the client-side hash router.

- [ ] **Step 1: Write test for SPA shell serving**

Add to `tests/test_web_api.py`:

```python
class TestSPAShell:
    def test_index_serves_base_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "HYDRA DETECT" in resp.text
        assert "view-monitor" in resp.text
        assert "view-control" in resp.text
        assert "view-settings" in resp.text
        assert "stream.mjpeg" in resp.text

    def test_index_includes_static_css(self, client):
        resp = client.get("/")
        assert "/static/css/variables.css" in resp.text
        assert "/static/js/app.js" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_api.py::TestSPAShell -v`
Expected: FAIL — `base.html` doesn't exist yet, current `index.html` doesn't contain the new structure.

- [ ] **Step 3: Create topbar.css**

Create `hydra_detect/web/static/css/topbar.css`:

```css
/* ── Top Bar ── */
.topbar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: var(--topbar-height);
    background: linear-gradient(135deg, var(--ogt-green) 0%, var(--ogt-green-dark) 60%, #1e3312 100%);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 var(--gap-lg);
    z-index: 100;
    border-bottom: 1px solid rgba(166, 188, 146, 0.15);
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.4);
}

.topbar-left {
    display: flex;
    align-items: center;
    gap: var(--gap-md);
}
.topbar-brand {
    font-family: 'Barlow Condensed', sans-serif;
    font-weight: 700;
    font-size: var(--font-lg);
    letter-spacing: var(--ls-condensed);
    text-transform: uppercase;
    color: var(--ogt-light);
}
.topbar-badge {
    width: 34px;
    height: 34px;
    flex-shrink: 0;
}

.topbar-center {
    display: flex;
    gap: var(--gap-xs);
}
.topbar-tab {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: var(--font-sm);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: var(--ls-condensed);
    padding: 4px 14px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    background: transparent;
    color: rgba(255, 255, 255, 0.7);
    cursor: pointer;
    transition: all var(--transition-fast);
}
.topbar-tab:hover {
    background: rgba(255, 255, 255, 0.1);
    color: #fff;
}
.topbar-tab.active {
    background: rgba(255, 255, 255, 0.2);
    color: #fff;
    border-color: rgba(255, 255, 255, 0.3);
}

.topbar-right {
    display: flex;
    align-items: center;
    gap: var(--gap-md);
}
.topbar-fps {
    font-family: 'JetBrains Mono', monospace;
    font-size: var(--font-xs);
    color: rgba(255, 255, 255, 0.7);
}

/* ── Mini Video Thumbnail ──
   Uses a second <img> pointing at the same /stream.mjpeg URL.
   Browsers deduplicate the connection for the same MJPEG URL,
   so this does NOT open a second stream. The container clips
   and scales the feed to thumbnail size. */
.topbar-thumbnail {
    width: 120px;
    height: 80px;
    overflow: hidden;
    border-radius: var(--radius-sm);
    border: 1px solid rgba(255, 255, 255, 0.15);
    cursor: pointer;
    display: none;
    position: relative;
}
body.view-control .topbar-thumbnail,
body.view-settings .topbar-thumbnail {
    display: block;
}
.topbar-thumbnail img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

/* ── Footer ── */
.footer {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: var(--footer-height);
    background: var(--sidebar-bg);
    border-top: 1px solid var(--card-border);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.3);
}
.footer::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--ogt-green), transparent);
}
.footer-text {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: var(--font-xs);
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--text-dim);
}

/* ── MJPEG Stream Positioning ── */
#mjpeg-stream {
    position: fixed;
    z-index: 1;
    background: #000;
    object-fit: contain;
    transition: all var(--transition-normal);
}
body.view-monitor #mjpeg-stream {
    top: var(--topbar-height);
    left: 0;
    right: 0;
    bottom: var(--footer-height);
    width: 100%;
    height: calc(100vh - var(--topbar-height) - var(--footer-height));
}
body.view-control #mjpeg-stream {
    top: var(--topbar-height);
    left: 0;
    width: 60%;
    height: calc(100vh - var(--topbar-height) - var(--footer-height));
}
body.view-settings #mjpeg-stream {
    opacity: 0;
    pointer-events: none;
    top: 0;
    left: 0;
    width: 0;
    height: 0;
}

/* ── Presentation Mode ── */
body.presentation .topbar,
body.presentation .footer {
    display: none;
}
body.presentation #mjpeg-stream {
    top: 0;
    bottom: 0;
    height: 100vh;
}

/* ── View Containers ── */
.view { display: none; }
body.view-monitor .view-monitor { display: block; }
body.view-control .view-control { display: flex; }
body.view-settings .view-settings { display: flex; }

.view-monitor,
.view-control,
.view-settings {
    position: fixed;
    top: var(--topbar-height);
    left: 0;
    right: 0;
    bottom: var(--footer-height);
    z-index: 2;
}

/* ── Responsive: Steam Deck (800-1279px) ── */
@media (max-width: 1279px) {
    .topbar-tab .tab-label { display: none; }
    .topbar-thumbnail { display: none !important; }

    body.view-control #mjpeg-stream {
        width: 100%;
        height: 40vh;
    }
}
```

- [ ] **Step 4: Create base.html**

Create `hydra_detect/web/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HYDRA DETECT — SORCC</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/static/css/variables.css">
    <link rel="stylesheet" href="/static/css/base.css">
    <link rel="stylesheet" href="/static/css/topbar.css">
    <link rel="stylesheet" href="/static/css/monitor.css">
    <link rel="stylesheet" href="/static/css/control.css">
    <link rel="stylesheet" href="/static/css/settings.css">
</head>
<body class="view-monitor">
    <!-- ── MJPEG Stream (persists across views) ── -->
    <img id="mjpeg-stream" src="/stream.mjpeg" alt="Live video feed">

    <!-- ── Top Bar ── -->
    <header class="topbar" role="banner">
        <div class="topbar-left">
            <svg class="topbar-badge" viewBox="0 0 34 34" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="17" y="2" width="21" height="21" rx="2" transform="rotate(45 17 2)" fill="#385723" stroke="#A6BC92" stroke-width="1"/>
                <text x="17" y="20" text-anchor="middle" fill="#EFF5EB" font-family="Barlow Condensed" font-size="8" font-weight="700" letter-spacing="0.08em">SORCC</text>
            </svg>
            <span class="topbar-brand">Hydra Detect</span>
        </div>

        <nav class="topbar-center" role="navigation" aria-label="View navigation">
            <button class="topbar-tab active" data-view="monitor" aria-label="Monitor view">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                <span class="tab-label">Monitor</span>
            </button>
            <button class="topbar-tab" data-view="control" aria-label="Control view">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
                <span class="tab-label">Control</span>
            </button>
            <button class="topbar-tab" data-view="settings" aria-label="Settings view">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
                <span class="tab-label">Settings</span>
            </button>
        </nav>

        <div class="topbar-right">
            <!-- Mini thumbnail: a clipping container that CSS-scales the main
                 MJPEG <img> element via a duplicated reference. JavaScript
                 clones the img src into this element on load. This avoids a
                 second MJPEG stream connection — the browser caches the same
                 stream URL. -->
            <div class="topbar-thumbnail" id="mini-thumbnail" title="Click to go to Monitor">
                <img id="mjpeg-thumbnail" src="/stream.mjpeg" alt="">
            </div>
            <span class="pill pill-live" id="connection-pill" aria-label="Connection status">
                <span class="pill-dot"></span>
                <span id="connection-text">LIVE</span>
            </span>
            <span class="topbar-fps" id="fps-display" aria-label="Frames per second">-- FPS</span>
        </div>
    </header>

    <!-- ── Toast Notifications ── -->
    <div class="toast-container" id="toast-container" role="alert" aria-live="polite"></div>

    <!-- ── View: Monitor ── -->
    <div class="view view-monitor" id="view-monitor">
        {% include 'monitor.html' %}
    </div>

    <!-- ── View: Control ── -->
    <div class="view view-control" id="view-control">
        {% include 'control.html' %}
    </div>

    <!-- ── View: Settings ── -->
    <div class="view view-settings" id="view-settings">
        {% include 'settings.html' %}
    </div>

    <!-- ── Strike Confirmation Modal ── -->
    <div class="modal-overlay" id="strike-modal">
        <div class="modal">
            <h3 style="color: var(--danger); margin-bottom: var(--gap-md);">Confirm Strike</h3>
            <p style="margin-bottom: var(--gap-sm);">Target: <strong id="strike-target-label">--</strong></p>
            <p style="font-size: var(--font-sm); color: var(--text-secondary); margin-bottom: var(--gap-lg);">
                Vehicle will navigate toward the target. Manual GCS override is always available.
            </p>
            <div style="display: flex; gap: var(--gap-sm); justify-content: flex-end;">
                <button class="btn" id="strike-cancel">Cancel</button>
                <button class="btn btn-danger" id="strike-confirm">Confirm Strike</button>
            </div>
        </div>
    </div>

    <!-- ── Footer ── -->
    <footer class="footer" role="contentinfo">
        <span class="footer-text">Unclassified</span>
    </footer>

    <!-- ── Scripts ── -->
    <script src="/static/js/vendor/Sortable.min.js"></script>
    <script src="/static/js/app.js"></script>
    <script src="/static/js/monitor.js"></script>
    <script src="/static/js/panels.js"></script>
    <script src="/static/js/control.js"></script>
    <script src="/static/js/settings.js"></script>
</body>
</html>
```

- [ ] **Step 5: Create placeholder templates for included views**

Create minimal placeholder files so `base.html` renders without errors:

`hydra_detect/web/templates/monitor.html`:
```html
<!-- Monitor view content (populated in Task 4) -->
<div class="monitor-overlays" id="monitor-overlays"></div>
```

`hydra_detect/web/templates/control.html`:
```html
<!-- Control view content (populated in Task 5) -->
<div class="control-panels" id="control-panels"></div>
```

`hydra_detect/web/templates/settings.html`:
```html
<!-- Settings view content (populated in Task 6) -->
<div class="settings-container" id="settings-container"></div>
```

Create placeholder CSS files:

`hydra_detect/web/static/css/monitor.css`:
```css
/* Monitor view styles (populated in Task 4) */
```

`hydra_detect/web/static/css/control.css`:
```css
/* Control view styles (populated in Task 5) */
```

`hydra_detect/web/static/css/settings.css`:
```css
/* Settings view styles (populated in Task 6) */
```

- [ ] **Step 6: Create app.js with view router and polling coordinator**

Create `hydra_detect/web/static/js/app.js`:

```javascript
/**
 * Hydra Detect v2.0 — SPA View Router & Polling Coordinator
 *
 * Manages view switching, MJPEG stream lifecycle, centralized API polling,
 * toast notifications, and shared application state.
 */

'use strict';

const HydraApp = (() => {
    // ── State ──
    let currentView = 'monitor';
    const pollers = {};
    let pollFailCount = 0;
    const MAX_BACKOFF = 10000;
    const toasts = [];
    const MAX_TOASTS = 3;
    const TOAST_DEDUP_MS = 5000;
    let lastActivity = Date.now();
    let apiToken = '';  // Set via setApiToken()

    // ── Shared Data (updated by pollers, read by views) ──
    const state = {
        stats: {},
        tracks: [],
        target: { locked: false },
        detections: [],
        rfStatus: { state: 'unavailable' },
    };

    // ── Auth Header ──
    function authHeaders() {
        const h = { 'Content-Type': 'application/json' };
        if (apiToken) h['Authorization'] = `Bearer ${apiToken}`;
        return h;
    }

    function setApiToken(token) { apiToken = token; }

    // ── View Router ──
    function initRouter() {
        window.addEventListener('hashchange', onHashChange);
        // Set initial view from hash or default to monitor
        const hash = window.location.hash.replace('#', '') || 'monitor';
        switchView(hash);

        // Tab click handlers
        document.querySelectorAll('.topbar-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const view = tab.dataset.view;
                window.location.hash = view;
            });
        });

        // Mini thumbnail click → monitor
        const thumb = document.getElementById('mini-thumbnail');
        if (thumb) {
            thumb.addEventListener('click', () => {
                window.location.hash = 'monitor';
            });
        }
    }

    function onHashChange() {
        const hash = window.location.hash.replace('#', '') || 'monitor';
        switchView(hash);
    }

    function switchView(view) {
        if (!['monitor', 'control', 'settings'].includes(view)) view = 'monitor';
        const prev = currentView;
        currentView = view;

        // Update body class (remove only known view classes to avoid clobbering others)
        ['view-monitor', 'view-control', 'view-settings'].forEach(c =>
            document.body.classList.remove(c));
        document.body.classList.add(`view-${view}`);

        // Update tab active state
        document.querySelectorAll('.topbar-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.view === view);
        });

        // Update polling
        updatePollers();

        // Notify view-specific handlers
        if (typeof HydraMonitor !== 'undefined' && prev !== view) {
            if (view === 'monitor') HydraMonitor.onEnter();
            if (prev === 'monitor') HydraMonitor.onLeave();
        }
        if (typeof HydraControl !== 'undefined' && prev !== view) {
            if (view === 'control') HydraControl.onEnter();
            if (prev === 'control') HydraControl.onLeave();
        }
        if (typeof HydraSettings !== 'undefined' && prev !== view) {
            if (view === 'settings') HydraSettings.onEnter();
            if (prev === 'settings') HydraSettings.onLeave();
        }
    }

    // ── Polling Coordinator ──
    function startPoller(name, url, intervalMs, callback) {
        if (pollers[name]) clearTimeout(pollers[name].timer);
        const entry = { baseInterval: intervalMs, callback, url, timer: null };
        pollers[name] = entry;

        const schedule = () => {
            // Exponential backoff: double interval on each failure, cap at MAX_BACKOFF
            const delay = pollFailCount === 0
                ? entry.baseInterval
                : Math.min(entry.baseInterval * Math.pow(2, pollFailCount), MAX_BACKOFF);
            entry.timer = setTimeout(poll, delay);
        };

        const poll = async () => {
            try {
                const resp = await fetch(url);
                if (resp.ok) {
                    const data = await resp.json();
                    callback(data);
                    pollFailCount = 0;
                    updateConnectionStatus(true);
                } else {
                    onPollFail();
                }
            } catch (e) {
                onPollFail();
            }
            // Only reschedule if poller still exists (wasn't stopped)
            if (pollers[name]) schedule();
        };

        poll(); // immediate first fetch
    }

    function stopPoller(name) {
        if (pollers[name]) {
            clearTimeout(pollers[name].timer);
            delete pollers[name];
        }
    }

    function onPollFail() {
        pollFailCount++;
        updateConnectionStatus(false);
    }

    function updatePollers() {
        // Always-on: stats (serves top bar FPS + connection status)
        if (!pollers['stats']) {
            startPoller('stats', '/api/stats', 2000, data => {
                state.stats = data;
                updateTopBarStats(data);
            });
        }

        // Monitor + Control: tracks, target, RF
        const needsTracks = ['monitor', 'control'].includes(currentView);
        if (needsTracks && !pollers['tracks']) {
            startPoller('tracks', '/api/tracks', 1000, data => { state.tracks = data; });
            startPoller('target', '/api/target', 1000, data => { state.target = data; });
            startPoller('rf', '/api/rf/status', 2000, data => { state.rfStatus = data; });
        } else if (!needsTracks) {
            stopPoller('tracks');
            stopPoller('target');
            stopPoller('rf');
        }

        // Control only: detections
        if (currentView === 'control' && !pollers['detections']) {
            startPoller('detections', '/api/detections', 3000, data => { state.detections = data; });
        } else if (currentView !== 'control') {
            stopPoller('detections');
        }
    }

    // ── Top Bar Updates ──
    function updateTopBarStats(data) {
        const fpsEl = document.getElementById('fps-display');
        if (fpsEl) fpsEl.textContent = `${(data.fps || 0).toFixed(1)} FPS`;
    }

    function updateConnectionStatus(connected) {
        const pill = document.getElementById('connection-pill');
        const text = document.getElementById('connection-text');
        if (!pill || !text) return;
        if (connected) {
            pill.className = 'pill pill-live';
            text.textContent = 'LIVE';
        } else {
            pill.className = 'pill pill-offline';
            text.textContent = 'OFFLINE';
        }
    }

    // ── Toast Notifications ──
    function showToast(message, type = 'error') {
        const container = document.getElementById('toast-container');
        if (!container) return;

        // Dedup: suppress identical messages within window
        const now = Date.now();
        const isDupe = toasts.some(t => t.message === message && (now - t.time) < TOAST_DEDUP_MS);
        if (isDupe) return;

        // Enforce max toasts
        while (toasts.length >= MAX_TOASTS) {
            const oldest = toasts.shift();
            if (oldest.el && oldest.el.parentNode) {
                oldest.el.classList.add('dismissing');
                setTimeout(() => oldest.el.remove(), 200);
            }
        }

        const el = document.createElement('div');
        el.className = `toast toast-${type}`;
        el.textContent = message;
        el.addEventListener('click', () => dismissToast(el));
        container.appendChild(el);

        const entry = { el, message, time: now };
        toasts.push(entry);

        // Auto-dismiss after 10s
        setTimeout(() => dismissToast(el), 10000);
    }

    function dismissToast(el) {
        el.classList.add('dismissing');
        setTimeout(() => {
            el.remove();
            const idx = toasts.findIndex(t => t.el === el);
            if (idx !== -1) toasts.splice(idx, 1);
        }, 200);
    }

    // ── API Helpers ──
    async function apiPost(url, body) {
        try {
            const resp = await fetch(url, {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (!resp.ok) {
                showToast(data.error || `Request failed (${resp.status})`);
                return null;
            }
            return data;
        } catch (e) {
            showToast('Network error — check connection');
            return null;
        }
    }

    async function apiGet(url) {
        try {
            const resp = await fetch(url, { headers: authHeaders() });
            if (!resp.ok) return null;
            return await resp.json();
        } catch (e) {
            return null;
        }
    }

    // ── Activity Tracking (for auto-hide) ──
    function trackActivity() {
        ['mousemove', 'touchstart', 'keydown'].forEach(evt => {
            document.addEventListener(evt, () => { lastActivity = Date.now(); });
        });
    }

    function isIdle(thresholdMs) {
        return (Date.now() - lastActivity) > thresholdMs;
    }

    // ── Modal: Escape to close ──
    function initModalEscape() {
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                document.querySelectorAll('.modal-overlay.active').forEach(m => {
                    m.classList.remove('active');
                });
            }
        });
    }

    // ── Presentation Mode ──
    function initPresentationMode() {
        document.addEventListener('keydown', e => {
            if (e.ctrlKey && e.shiftKey && e.key === 'P') {
                if (document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
                e.preventDefault();
                document.body.classList.toggle('presentation');
            }
        });
    }

    // ── Init ──
    function init() {
        initRouter();
        trackActivity();
        initPresentationMode();
        initModalEscape();
        updatePollers();
    }

    // Start on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Public API
    return {
        state,
        currentView: () => currentView,
        switchView,
        showToast,
        apiPost,
        apiGet,
        authHeaders,
        setApiToken,
        isIdle,
    };
})();
```

- [ ] **Step 7: Create placeholder JS files**

Create minimal placeholder files so the HTML doesn't error on missing scripts:

`hydra_detect/web/static/js/monitor.js`:
```javascript
'use strict';
const HydraMonitor = (() => {
    function onEnter() {}
    function onLeave() {}
    return { onEnter, onLeave };
})();
```

`hydra_detect/web/static/js/panels.js`:
```javascript
'use strict';
const HydraPanels = (() => {
    function init() {}
    return { init };
})();
```

`hydra_detect/web/static/js/control.js`:
```javascript
'use strict';
const HydraControl = (() => {
    function onEnter() {}
    function onLeave() {}
    return { onEnter, onLeave };
})();
```

`hydra_detect/web/static/js/settings.js`:
```javascript
'use strict';
const HydraSettings = (() => {
    function onEnter() {}
    function onLeave() {}
    return { onEnter, onLeave };
})();
```

- [ ] **Step 8: Download and vendor SortableJS**

Run:
```bash
curl -sL https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js -o hydra_detect/web/static/js/vendor/Sortable.min.js
```

Verify the file was downloaded:
```bash
wc -c hydra_detect/web/static/js/vendor/Sortable.min.js
```
Expected: ~40-50KB (uncompressed).

- [ ] **Step 9: Update server.py to serve base.html instead of index.html**

In `hydra_detect/web/server.py`, change the `GET /` route:

```python
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the operator dashboard SPA."""
    return templates.TemplateResponse("base.html", {"request": request})
```

- [ ] **Step 10: Update existing tests that reference index.html**

The existing `test_web_api.py` does not have explicit tests checking `index.html` content, but verify that `TestReadOnlyEndpoints` and the `GET /` route still work with `base.html`. If any test checks for content specific to the old `index.html` (e.g., sidebar section text), update it to check for equivalent `base.html` content.

- [ ] **Step 11: Run tests**

Run: `python -m pytest tests/test_web_api.py::TestSPAShell -v`
Expected: PASS

Run: `python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 12: Commit**

```bash
git add hydra_detect/web/templates/base.html hydra_detect/web/templates/monitor.html hydra_detect/web/templates/control.html hydra_detect/web/templates/settings.html hydra_detect/web/static/ hydra_detect/web/server.py tests/test_web_api.py
git commit -m "feat(web): add SPA shell with view router, top bar, and MJPEG positioning"
```

---

## Task 4: Monitor View

**Files:**
- Modify: `hydra_detect/web/templates/monitor.html`
- Modify: `hydra_detect/web/static/css/monitor.css`
- Modify: `hydra_detect/web/static/js/monitor.js`

This task builds the full Monitor view: floating overlays (system/vehicle vitals, detection summary, target lock indicator), auto-hide behavior, quick action toolbar, and stream error states.

- [ ] **Step 1: Write monitor.html**

Replace the placeholder `hydra_detect/web/templates/monitor.html` with the full overlay markup. Include:
- Bottom-left overlay: pipeline vitals + vehicle telemetry (IDs matching what `app.js` state provides)
- Bottom-right overlay: track summary + RF status
- Top-center: target lock indicator
- Bottom-center: quick action toolbar (Lock, Strike, Release | Loiter, RTL, Auto)
- Loading state: "Connecting to video stream..." spinner

Refer to the spec sections "Monitor View", "Floating Overlays", "Quick Action Toolbar".

- [ ] **Step 2: Write monitor.css**

Replace the placeholder with full styles. Include:
- Overlay positioning (bottom-left, bottom-right, top-center, bottom-center)
- Semi-transparent background with `backdrop-filter: blur()` and solid fallback
- Auto-hide transition (`opacity` + `pointer-events`)
- Quick action toolbar layout (flex, divider between groups)
- Target lock indicator (green for tracking, red for strike)
- Loading spinner
- Stream-lost overlay badge
- Presentation mode adjustments

- [ ] **Step 3: Write monitor.js**

Replace the placeholder with full logic. Include:
- `onEnter()` / `onLeave()` lifecycle hooks
- Auto-hide timer: check `HydraApp.isIdle(5000)` every 500ms, toggle overlay visibility
- Update functions that read from `HydraApp.state` and populate overlay elements:
  - `updateVitals()` — FPS, inference, GPU temp/load from `state.stats`
  - `updateVehicle()` — mode, armed, battery, alt, heading, GPS, speed from `state.stats`
  - `updateTracks()` — track count + class labels from `state.tracks`
  - `updateRF()` — RF hunt status from `state.rfStatus` (show/hide based on state)
  - `updateLockIndicator()` — target lock from `state.target`
- Quick action button handlers:
  - Lock: show quick-pick of active tracks from `state.tracks`, call `apiPost('/api/target/lock', {track_id})`
  - Strike: open strike modal, on confirm call `apiPost('/api/target/strike', {track_id, confirm: true})`
  - Release: call `apiPost('/api/target/unlock')`
  - Loiter/RTL/Auto: call `apiPost('/api/vehicle/mode', {mode})`
- MJPEG stream error handling: detect `<img>` error event, show "STREAM LOST" badge, retry `src` every 2s

- [ ] **Step 4: Test manually in browser**

Open `http://localhost:8080/#monitor` (or whatever host the Jetson is on).
Verify:
- Video stream loads and fills the view
- Overlays appear on mouse movement, fade after 5 seconds
- FPS and connection pill update in top bar
- Quick action toolbar is visible on hover
- Switching to `/#control` and back preserves the stream

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/templates/monitor.html hydra_detect/web/static/css/monitor.css hydra_detect/web/static/js/monitor.js
git commit -m "feat(web): implement Monitor view with overlays and quick actions"
```

---

## Task 5: Control View + Panel System

**Files:**
- Modify: `hydra_detect/web/templates/control.html`
- Modify: `hydra_detect/web/static/css/control.css`
- Modify: `hydra_detect/web/static/js/panels.js`
- Modify: `hydra_detect/web/static/js/control.js`

This task builds the operator cockpit: 6 dockable panels with collapse/expand, drag reorder via SortableJS, and localStorage persistence.

- [ ] **Step 1: Write control.html**

Replace the placeholder with the panel grid. Include 6 panels, each with the structure:

```html
<div class="panel" data-panel-id="vehicle" id="panel-vehicle">
    <div class="panel-header">
        <span class="panel-drag-handle">⠿</span>
        <h3 class="panel-title">Vehicle Telemetry</h3>
        <button class="panel-collapse-btn" aria-label="Collapse panel">▾</button>
    </div>
    <div class="panel-body">
        <!-- Panel content -->
    </div>
</div>
```

Panel contents — refer to the spec "Default Panels" section and the existing `index.html` for the data layout of each:

1. **Vehicle Telemetry** — stat grid (mode, armed, battery V/%, speed, alt, heading, GPS) + mode buttons
2. **Target Control** — lock indicator + track list + Lock/Strike/Release buttons
3. **Pipeline Stats** — FPS, inference, GPU/CPU/RAM color bars + power mode dropdown + pause/stop
4. **Detection Config** — model dropdown + confidence slider + alert class categorized checklist
5. **RF Hunt** — status/config form (replicate from current `index.html` RF section)
6. **Detection Log** — scrollable div for recent detection entries

Include a **panel visibility menu** button in the panel area header.

- [ ] **Step 2: Write control.css**

Styles for the control view layout and panel system:
- `.control-panels` — positioned to the right 40% of the screen, grid layout (2 columns)
- `.panel` — card styling with `var(--card-bg-gradient)`, rounded corners, border
- `.panel-header` — flex row, drag handle, title, collapse button
- `.panel-body` — content area with padding, collapsible via `max-height` transition
- `.panel.collapsed .panel-body` — `max-height: 0; overflow: hidden; padding: 0`
- `.panel.hidden` — `display: none`
- Stat grids inside panels (2-col and 3-col layouts)
- Color bars for GPU/CPU/RAM
- Track list styling
- RF Hunt form layout
- Detection log scroll container
- Panel visibility menu (dropdown with checkboxes)
- Responsive: single column below 1280px, panels below 40vh video

- [ ] **Step 3: Write panels.js**

Full panel system logic:
- `init()` — called on DOM ready
- SortableJS initialization on the panel container
- Collapse/expand toggle (click handler on `.panel-collapse-btn`)
- Panel visibility menu (show/hide individual panels)
- `saveLayout()` — serialize panel order + collapsed state + visibility to localStorage
  - Key includes breakpoint: `hydra-panels-${window.innerWidth >= 1280 ? 'desktop' : 'mobile'}`
- `loadLayout()` — restore from localStorage, validate against known panel IDs, fall back to defaults
- `getDefaultLayout()` — returns the 6 default panels in order, all expanded and visible

- [ ] **Step 4: Write control.js**

Panel-specific data update logic:
- `onEnter()` — start update intervals
- `onLeave()` — clear intervals
- Update functions that read `HydraApp.state` and populate panel content:
  - `updateVehiclePanel()` — telemetry from `state.stats`
  - `updateTargetPanel()` — tracks from `state.tracks`, lock from `state.target`
  - `updatePipelinePanel()` — stats from `state.stats` (FPS, inference, GPU/CPU/RAM bars)
  - `updateDetectionLog()` — entries from `state.detections`
  - `updateRFPanel()` — status from `state.rfStatus`
- Pipeline offline state: when `state.stats.fps === 0` for >5 seconds, show
  a "Pipeline offline" banner across the panel area and gray out panel values
- Event handlers for control actions:
  - Mode buttons (Loiter/Auto/RTL) → `HydraApp.apiPost('/api/vehicle/mode', {mode})`
  - Lock/Strike/Release → same as Monitor view handlers
  - Confidence slider → `apiPost('/api/config/threshold', {threshold})`
  - Alert class filter → `apiPost('/api/config/alert-classes', {classes})`
  - Model switch → `apiPost('/api/models/switch', {model})`
  - Power mode → `apiPost('/api/system/power-mode', {mode_id})`
  - Pause/Stop → `apiPost('/api/pipeline/pause' or '/stop')`
  - RF start/stop → `apiPost('/api/rf/start' or '/stop', config)`
- Populate dropdowns on enter (models, power modes, camera sources via one-time fetches)

- [ ] **Step 5: Test manually**

Open `http://localhost:8080/#control`. Verify:
- Video on left, panels on right
- Panels collapse/expand on click
- Panels drag to reorder
- Layout persists after page refresh
- Panel visibility menu works
- Data updates in panels match what the API returns
- Control actions (mode, threshold, etc.) work

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/web/templates/control.html hydra_detect/web/static/css/control.css hydra_detect/web/static/js/panels.js hydra_detect/web/static/js/control.js
git commit -m "feat(web): implement Control view with 6 dockable panels and SortableJS"
```

---

## Task 6: Settings View

**Files:**
- Modify: `hydra_detect/web/templates/settings.html`
- Modify: `hydra_detect/web/static/css/settings.css`
- Modify: `hydra_detect/web/static/js/settings.js`

This task builds the full config.ini editor with section navigation, form generation, validation, apply/reset, and the autonomous strike warning banner.

- [ ] **Step 1: Write settings.html**

Replace the placeholder with the settings layout:
- Left nav with 9 section buttons (Camera, Detector, Tracker, MAVLink, Web, OSD, Autonomous, RF Homing, Logging)
- Right content area with a `<form>` container
- Apply / Reset to Saved / Restore Backup buttons in a sticky footer bar
- Error banner area (hidden by default)
- Autonomous section warning banner (red background, warning text)

- [ ] **Step 2: Write settings.css**

Styles for the settings layout:
- `.settings-container` — flex row, full height
- `.settings-nav` — left column, `var(--settings-nav-width)`, vertical button list
- `.settings-content` — right column, flex: 1, scrollable
- `.settings-section-btn` — nav buttons, active state with green accent
- `.settings-form` — form layout with label/input pairs
- `.settings-field` — label + input wrapper
- `.settings-warning` — red banner for autonomous section
- `.settings-actions` — sticky bottom bar with Apply/Reset buttons
- `.settings-restart-icon` — small warning icon for restart-required fields
- Responsive: nav collapses to dropdown below 1280px

- [ ] **Step 3: Write settings.js**

Full settings logic:
- `onEnter()` — fetch config via `HydraApp.apiGet('/api/config/full')`, populate forms
- `onLeave()` — warn if unsaved changes
- `buildForm(section, data)` — dynamically generate form inputs based on config data:
  - Text inputs for strings
  - Number inputs for numeric values
  - Toggle switches for boolean (true/false) fields
  - Textarea for long values (geofence_polygon, alert_classes)
  - Password input with show/hide toggle for api_token
  - Mark restart-required fields with icon (check against known list)
- Section navigation: click nav button → show that section's form, hide others
- `handleApply()` — collect changed fields, POST to `/api/config/full`, show toast on success/error, display restart-required fields in response
- `handleReset()` — re-fetch config, repopulate forms
- `handleRestoreBackup()` — POST to a new endpoint or show confirmation, then re-fetch
- Frontend validation:
  - Numeric ranges (check min/max from config knowledge)
  - Required fields (connection_string, etc.)
  - BSSID format (XX:XX:XX:XX:XX:XX)
  - Geofence polygon format (semicolon-separated lat,lon pairs)

- [ ] **Step 4: Test manually**

Open `http://localhost:8080/#settings`. Verify:
- All 9 config sections appear in nav
- Clicking a section shows its form
- Values match what's in config.ini
- API token is masked
- Autonomous section has red warning banner
- Apply saves changes (check config.ini on disk)
- Reset reverts form to saved values
- Restart-required fields show warning icon
- Validation catches bad input

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/templates/settings.html hydra_detect/web/static/css/settings.css hydra_detect/web/static/js/settings.js
git commit -m "feat(web): implement Settings view with config.ini editor"
```

---

## Task 7: Responsive Polish & Final Integration

**Files:**
- Modify: all CSS files (responsive refinements)
- Modify: `hydra_detect/web/static/js/app.js` (stream error handling)
- Delete: `hydra_detect/web/templates/index.html` (replaced by base.html)
- Test: full test suite

This task handles responsive breakpoints, Steam Deck touch targets, stream error states, and removes the old index.html.

- [ ] **Step 1: Add MJPEG stream error handling to app.js**

Add to `app.js` init:

```javascript
// MJPEG stream error handling
const streamImg = document.getElementById('mjpeg-stream');
if (streamImg) {
    streamImg.addEventListener('error', () => {
        // Show stream-lost overlay
        document.getElementById('stream-lost')?.classList.add('active');
        // Retry every 2s
        setTimeout(() => {
            streamImg.src = '/stream.mjpeg?' + Date.now();
        }, 2000);
    });
    streamImg.addEventListener('load', () => {
        document.getElementById('stream-lost')?.classList.remove('active');
    });
}
```

- [ ] **Step 2: Verify responsive breakpoints**

Test at these widths (Chrome DevTools or actual devices):
- 1920px — full desktop layout
- 1280px — breakpoint boundary
- 1280x800 — Steam Deck resolution
- 800px — small tablet

For each, verify:
- Monitor: video fills, overlays readable, touch targets adequate
- Control: panels switch to single column below 1280px
- Settings: nav collapses to dropdown below 1280px
- Top bar: labels collapse to icons below 1280px

- [ ] **Step 3: Test backdrop-filter performance**

If running on Jetson, check GPU impact:
```bash
tegrastats
```

If `backdrop-filter: blur()` causes noticeable frame drops, the CSS already has the spec's fallback: replace with `rgba(0,0,0,0.85)` solid background.

- [ ] **Step 4: Remove old index.html**

Once the new SPA is verified working:

```bash
git rm hydra_detect/web/templates/index.html
```

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass. Update any tests that referenced `index.html` content to match `base.html`.

- [ ] **Step 6: Run linters**

Run: `flake8 hydra_detect/ tests/`
Run: `mypy hydra_detect/`

Fix any issues.

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat(web): complete dashboard redesign — remove old index.html, add responsive polish"
```

---

## Task Summary

| Task | Description | Dependencies |
|------|-------------|-------------|
| 1 | Static file serving + CSS design system | None |
| 2 | Config API backend (read/write/auth/safety) | None |
| 3 | SPA shell (base.html, router, top bar, MJPEG) | Task 1 |
| 4 | Monitor view (overlays, quick actions) | Task 3 |
| 5 | Control view (6 panels, SortableJS) | Task 3 |
| 6 | Settings view (config editor) | Task 2, Task 3 |
| 7 | Responsive polish, integration, cleanup | Tasks 4-6 |

**Tasks 1 and 2 are independent** — can be worked in parallel.
**Tasks 4, 5, and 6 are independent** of each other — can be worked in parallel after Task 3.
**Task 7 depends on all previous tasks.**
