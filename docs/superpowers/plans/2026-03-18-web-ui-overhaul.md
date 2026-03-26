# Web UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the Monitor and Control views into a single Operations view with tiered panel priority and no auto-hide.

**Architecture:** Rename control.{html,js,css} to operations.{html,js,css}, absorb the lock indicator overlay and stream status elements from monitor.html, remove the monitor files entirely, update base.html and app.js to reference only Operations and Settings views. Remove the idle timer auto-hide mechanism.

**Tech Stack:** HTML/Jinja2, vanilla JS, CSS, FastAPI (no backend changes)

**Spec:** `docs/superpowers/specs/2026-03-18-web-ui-overhaul-design.md`

---

### Task 1: Create operations.html from control.html + monitor overlays

**Files:**
- Create: `hydra_detect/web/templates/operations.html`
- Delete: `hydra_detect/web/templates/monitor.html`
- Delete: `hydra_detect/web/templates/control.html`

- [ ] **Step 1: Copy control.html to operations.html**

```bash
cp hydra_detect/web/templates/control.html hydra_detect/web/templates/operations.html
```

- [ ] **Step 2: Add lock indicator and stream status elements to operations.html**

At the very top of `operations.html` (before the panel toolbar), add the lock indicator and stream status elements from monitor.html with renamed IDs:

```html
<!-- Operations View — video overlays + dockable panels -->

<!-- Loading State -->
<div class="ops-loading" id="ops-loading">
    <div class="ops-loading-spinner"></div>
    <span>Connecting to video stream...</span>
</div>

<!-- Stream Lost Badge -->
<div class="ops-stream-lost" id="ops-stream-lost" style="display:none;">
    <span>STREAM LOST — RECONNECTING</span>
</div>

<!-- Target Lock Indicator (top center, overlaid on video) -->
<div class="ops-lock-indicator" id="ops-lock-indicator" style="display:none;">
    <span class="lock-icon">⊕</span>
    <span class="lock-label" id="lock-label">--</span>
    <span class="lock-mode" id="lock-mode">TRACK</span>
</div>

<!-- Panel Visibility Toggle -->
```

- [ ] **Step 3: Rename container ID**

In `operations.html`, change:
```html
<div class="control-panels" id="control-panels">
```
to:
```html
<div class="operations-panels" id="operations-panels">
```

- [ ] **Step 4: Delete old template files**

```bash
rm hydra_detect/web/templates/monitor.html hydra_detect/web/templates/control.html
```

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/templates/
git commit -m "feat(ui): create operations.html, remove monitor.html and control.html

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Create operations.css from control.css + monitor lock indicator styles

**Files:**
- Create: `hydra_detect/web/static/css/operations.css`
- Delete: `hydra_detect/web/static/css/monitor.css`
- Delete: `hydra_detect/web/static/css/control.css`

- [ ] **Step 1: Copy control.css to operations.css**

```bash
cp hydra_detect/web/static/css/control.css hydra_detect/web/static/css/operations.css
```

- [ ] **Step 2: Update class references in operations.css**

In `operations.css`, find and replace:
- `.control-panels` → `.operations-panels`

- [ ] **Step 3: Append lock indicator, loading, and stream-lost styles**

Append to `operations.css` the styles from monitor.css that we need (lock indicator, loading spinner, stream lost badge), renamed from `monitor-*` to `ops-*`:

```css
/* ── Video Overlay: Target Lock Indicator ── */
.ops-lock-indicator {
    position: fixed;
    top: calc(var(--topbar-height) + var(--gap-md));
    left: 30%;
    transform: translateX(-50%);
    z-index: 10;
    background: rgba(0, 0, 0, 0.7);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border-radius: var(--radius-lg);
    padding: var(--gap-xs) var(--gap-md);
    display: flex;
    align-items: center;
    gap: var(--gap-sm);
    border: 1px solid var(--ogt-green);
    box-shadow: var(--glow-green);
}

.ops-lock-indicator.strike {
    border-color: var(--danger);
    box-shadow: var(--glow-danger);
}

.ops-lock-indicator .lock-icon {
    font-size: var(--font-lg);
    color: var(--ogt-muted);
}
.ops-lock-indicator.strike .lock-icon {
    color: #fca5a5;
}
.ops-lock-indicator .lock-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: var(--font-sm);
    font-weight: 600;
    text-transform: uppercase;
    color: var(--text-primary);
}
.ops-lock-indicator .lock-mode {
    font-family: 'JetBrains Mono', monospace;
    font-size: var(--font-xs);
    color: var(--ogt-muted);
}
.ops-lock-indicator.strike .lock-mode {
    color: #fca5a5;
}

/* ── Loading & Stream States ── */
.ops-loading {
    position: fixed;
    top: var(--topbar-height);
    left: 0;
    width: 60%;
    bottom: var(--footer-height);
    z-index: 5;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--gap-md);
    color: var(--text-secondary);
    font-size: var(--font-sm);
    background: var(--panel-bg);
}

.ops-loading-spinner {
    width: 32px;
    height: 32px;
    border: 3px solid var(--card-border);
    border-top-color: var(--ogt-green);
    border-radius: 50%;
    animation: spin 1s linear infinite;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

.ops-stream-lost {
    position: fixed;
    top: calc(var(--topbar-height) + var(--gap-md));
    left: 30%;
    transform: translateX(-50%);
    z-index: 15;
    background: var(--danger-bg);
    border: 1px solid var(--danger);
    border-radius: var(--radius-lg);
    padding: var(--gap-xs) var(--gap-md);
    color: #fca5a5;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: var(--font-sm);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: var(--ls-condensed);
    animation: pulse-glow 2s ease-in-out infinite;
}

/* ── Responsive ── */
@media (max-width: 1279px) {
    .ops-loading { width: 100%; }
    .ops-lock-indicator { left: 50%; }
    .ops-stream-lost { left: 50%; }
}
```

Note: The lock indicator uses `left: 30%` (center of the 60% video area) instead of `left: 50%` (center of viewport). In compact mode it falls back to `left: 50%`.

- [ ] **Step 4: Delete old CSS files**

```bash
rm hydra_detect/web/static/css/monitor.css hydra_detect/web/static/css/control.css
```

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/static/css/
git commit -m "feat(ui): create operations.css, remove monitor.css and control.css

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Create operations.js from control.js + lock indicator logic

**Files:**
- Create: `hydra_detect/web/static/js/operations.js`
- Delete: `hydra_detect/web/static/js/monitor.js`
- Delete: `hydra_detect/web/static/js/control.js`

- [ ] **Step 1: Copy control.js to operations.js**

```bash
cp hydra_detect/web/static/js/control.js hydra_detect/web/static/js/operations.js
```

- [ ] **Step 2: Rename the module from HydraControl to HydraOperations**

In `operations.js`:
- Change `const HydraControl = (() => {` to `const HydraOperations = (() => {`
- Update the file header comment from "Control View Logic" to "Operations View Logic"

- [ ] **Step 3: Add lock indicator update to updatePanels()**

In `operations.js`, find the `updatePanels()` function. Add `updateLockOverlay();` after the existing update calls. Then add the function (taken from monitor.js `updateLockIndicator`, adapted for new element ID):

```javascript
    function updateLockOverlay() {
        const t = HydraApp.state.target;
        const el = document.getElementById('ops-lock-indicator');
        if (!el) return;
        if (t.locked) {
            el.style.display = '';
            const labelEl = document.getElementById('lock-label');
            const modeEl = document.getElementById('lock-mode');
            if (labelEl) labelEl.textContent = '#' + t.track_id + ' ' + (t.label || '');
            if (modeEl) modeEl.textContent = (t.mode || 'track').toUpperCase();
            el.classList.toggle('strike', t.mode === 'strike');
        } else {
            el.style.display = 'none';
        }
    }
```

- [ ] **Step 4: Add stream watcher initialization to onEnter()**

In `operations.js`, add to `onEnter()` after `HydraPanels.init();`:

```javascript
        initStreamWatcher();
```

Then add the function:

```javascript
    function initStreamWatcher() {
        const img = document.getElementById('mjpeg-stream');
        const loading = document.getElementById('ops-loading');
        const lost = document.getElementById('ops-stream-lost');
        if (!img) return;

        if (img.complete && img.naturalWidth > 0) {
            if (loading) loading.style.display = 'none';
        }

        img.addEventListener('load', () => {
            if (loading) loading.style.display = 'none';
            if (lost) lost.style.display = 'none';
        }, { once: true });
    }
```

- [ ] **Step 5: Delete old JS files**

```bash
rm hydra_detect/web/static/js/monitor.js hydra_detect/web/static/js/control.js
```

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/web/static/js/
git commit -m "feat(ui): create operations.js, remove monitor.js and control.js

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Update base.html — tabs, includes, scripts, view containers

**Files:**
- Modify: `hydra_detect/web/templates/base.html`

- [ ] **Step 1: Read base.html**

Read the current file to get exact content for edits.

- [ ] **Step 2: Update CSS includes**

Replace:
```html
    <link rel="stylesheet" href="/static/css/monitor.css">
    <link rel="stylesheet" href="/static/css/control.css">
```
with:
```html
    <link rel="stylesheet" href="/static/css/operations.css">
```

- [ ] **Step 3: Update default body class**

Replace:
```html
<body class="view-monitor">
```
with:
```html
<body class="view-operations">
```

- [ ] **Step 4: Replace tab navigation — remove Monitor, rename Control to Operations**

Replace the entire `<nav>` block (the three tab buttons) with two buttons:

```html
        <nav class="topbar-center" role="navigation" aria-label="View navigation">
            <button class="topbar-tab active" data-view="operations" aria-label="Operations view">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                <span class="tab-label">Operations</span>
            </button>
            <button class="topbar-tab" data-view="settings" aria-label="Settings view">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
                <span class="tab-label">Settings</span>
            </button>
        </nav>
```

- [ ] **Step 5: Update thumbnail click target**

Replace:
```html
            <div class="topbar-thumbnail" id="mini-thumbnail" title="Click to go to Monitor">
```
with:
```html
            <div class="topbar-thumbnail" id="mini-thumbnail" title="Click to go to Operations">
```

- [ ] **Step 6: Replace view containers — remove Monitor and Control, add Operations**

Replace:
```html
    <!-- ── View: Monitor ── -->
    <div class="view view-monitor" id="view-monitor">
        {% include 'monitor.html' %}
    </div>

    <!-- ── View: Control ── -->
    <div class="view view-control" id="view-control">
        {% include 'control.html' %}
    </div>
```
with:
```html
    <!-- ── View: Operations ── -->
    <div class="view view-operations" id="view-operations">
        {% include 'operations.html' %}
    </div>
```

- [ ] **Step 7: Update script includes**

Replace:
```html
    <script src="/static/js/monitor.js"></script>
    <script src="/static/js/panels.js"></script>
    <script src="/static/js/control.js"></script>
```
with:
```html
    <script src="/static/js/panels.js"></script>
    <script src="/static/js/operations.js"></script>
```

- [ ] **Step 8: Commit**

```bash
git add hydra_detect/web/templates/base.html
git commit -m "feat(ui): update base.html for Operations view — remove Monitor tab and container

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Update app.js — routing, pollers, remove idle timer

**Files:**
- Modify: `hydra_detect/web/static/js/app.js`

- [ ] **Step 1: Read app.js**

Read the current file.

- [ ] **Step 2: Remove activity tracking and isIdle**

Remove `lastActivity` declaration (line 19), the `trackActivity()` function (lines 254-258), the `isIdle()` function (lines 260-262), and the `trackActivity()` call in `init()` (line 307). Remove `isIdle` from the return object (line 329).

- [ ] **Step 3: Update default view and valid views**

Change default from `'monitor'` to `'operations'`:
- Line 12: `let currentView = 'operations';`
- Line 43: `const hash = ... || 'operations';`
- Line 62: `const hash = ... || 'operations';`
- Line 67: valid views list changes to `['operations', 'settings']`, fallback to `'operations'`

- [ ] **Step 4: Update switchView lifecycle hooks**

Replace the `HydraMonitor` and `HydraControl` lifecycle blocks (lines 81-92) with:

```javascript
        if (typeof HydraOperations !== 'undefined' && prev !== view) {
            if (view === 'operations') HydraOperations.onEnter();
            if (prev === 'operations') HydraOperations.onLeave();
        }
```

Keep the `HydraSettings` block unchanged.

- [ ] **Step 5: Update view class toggling**

Replace:
```javascript
        ['view-monitor', 'view-control', 'view-settings'].forEach(c =>
            document.body.classList.remove(c));
```
with:
```javascript
        ['view-operations', 'view-settings'].forEach(c =>
            document.body.classList.remove(c));
```

- [ ] **Step 6: Update pollers — merge monitor+control into operations**

Replace the `updatePollers()` function body (lines 140-164) with:

```javascript
    function updatePollers() {
        if (!pollers['stats']) {
            startPoller('stats', '/api/stats', 2000, data => {
                state.stats = data;
                updateTopBarStats(data);
            });
        }

        const isOps = currentView === 'operations';
        if (isOps && !pollers['tracks']) {
            startPoller('tracks', '/api/tracks', 1000, data => { state.tracks = data; });
            startPoller('target', '/api/target', 1000, data => { state.target = data; });
            startPoller('rf', '/api/rf/status', 2000, data => { state.rfStatus = data; });
            startPoller('detections', '/api/detections', 3000, data => { state.detections = data; });
        } else if (!isOps) {
            stopPoller('tracks');
            stopPoller('target');
            stopPoller('rf');
            stopPoller('detections');
        }
    }
```

- [ ] **Step 7: Update thumbnail click**

Replace:
```javascript
                window.location.hash = 'monitor';
```
with:
```javascript
                window.location.hash = 'operations';
```

- [ ] **Step 8: Update stream watcher element IDs**

In `initStreamWatcher()`, replace:
```javascript
                const lost = document.getElementById('monitor-stream-lost');
```
with:
```javascript
                const lost = document.getElementById('ops-stream-lost');
```

(Both occurrences — in the error handler and the load handler.)

- [ ] **Step 9: Commit**

```bash
git add hydra_detect/web/static/js/app.js
git commit -m "feat(ui): update app.js — operations routing, remove idle timer

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Update topbar.css — view selectors and stream positioning

**Files:**
- Modify: `hydra_detect/web/static/css/topbar.css`

- [ ] **Step 1: Read topbar.css**

Read the current file.

- [ ] **Step 2: Update MJPEG stream positioning**

Replace the three `body.view-*` stream rules (lines 137-158):

```css
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
```

with:

```css
body.view-operations #mjpeg-stream {
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
```

- [ ] **Step 3: Update view container selectors**

Replace:
```css
.view { display: none; }
body.view-monitor .view-monitor { display: block; }
body.view-control .view-control { display: flex; }
body.view-settings .view-settings { display: flex; }

.view-monitor,
.view-control,
.view-settings {
```

with:
```css
.view { display: none; }
body.view-operations .view-operations { display: flex; }
body.view-settings .view-settings { display: flex; }

.view-operations,
.view-settings {
```

- [ ] **Step 4: Update responsive media query**

Replace:
```css
    body.view-control #mjpeg-stream {
        width: 100%;
        height: 40vh;
    }
```

with:
```css
    body.view-operations #mjpeg-stream {
        width: 100%;
        height: 40vh;
    }
```

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/static/css/topbar.css
git commit -m "feat(ui): update topbar.css selectors for operations view

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Update panels.js — container ID, tier defaults, localStorage migration

**Files:**
- Modify: `hydra_detect/web/static/js/panels.js`

- [ ] **Step 1: Read panels.js**

Read the current file.

- [ ] **Step 2: Rename container ID references**

Replace all `'control-panels'` with `'operations-panels'` (3 occurrences: lines 25, 96, 121).

- [ ] **Step 3: Add tier defaults and localStorage migration**

In the `loadLayout()` function, after the `if (!raw) return;` early return (line 127), add localStorage migration logic. Also, when there's no saved layout, apply tier defaults.

Replace the `loadLayout()` function entirely with:

```javascript
    function loadLayout() {
        const container = document.getElementById('operations-panels');
        if (!container) return;

        // Migrate old localStorage keys
        migrateOldKeys();

        let layout;
        try {
            const raw = localStorage.getItem(storageKey());
            if (!raw) {
                applyTierDefaults(container);
                return;
            }
            layout = JSON.parse(raw);
        } catch (e) {
            applyTierDefaults(container);
            return;
        }

        if (!Array.isArray(layout) || layout.length === 0) {
            applyTierDefaults(container);
            return;
        }

        // Validate: only keep entries with known IDs
        const valid = layout.filter(item => item && KNOWN_IDS.includes(item.id));
        if (valid.length === 0) {
            applyTierDefaults(container);
            return;
        }

        // Reorder panels in the DOM
        const panelMap = {};
        container.querySelectorAll('.panel').forEach(p => {
            panelMap[p.dataset.panelId] = p;
        });

        // Append panels in saved order
        valid.forEach(item => {
            const panel = panelMap[item.id];
            if (!panel) return;
            panel.classList.toggle('collapsed', !!item.collapsed);
            panel.classList.toggle('hidden', item.visible === false);
            container.appendChild(panel);
            delete panelMap[item.id];
        });

        // Any panels not in saved layout get appended at end
        Object.values(panelMap).forEach(panel => {
            container.appendChild(panel);
        });

        syncVisibilityCheckboxes();
    }

    function migrateOldKeys() {
        // Clean up old control-view localStorage keys
        const oldKeys = ['hydra-panels-desktop', 'hydra-panels-compact'];
        // Only migrate if we have old keys AND no new keys
        const newKey = storageKey();
        try {
            if (localStorage.getItem(newKey)) return; // already migrated
            for (const key of oldKeys) {
                localStorage.removeItem(key);
            }
        } catch (e) {
            // localStorage unavailable
        }
    }

    function applyTierDefaults(container) {
        // Tier 1 (vehicle, target): expanded
        // Tier 2 (pipeline, rf): expanded
        // Tier 3 (detection, log): collapsed
        const tier3 = ['detection', 'log'];
        container.querySelectorAll('.panel').forEach(panel => {
            const id = panel.dataset.panelId;
            if (tier3.includes(id)) {
                panel.classList.add('collapsed');
            }
        });
    }
```

- [ ] **Step 4: Update saveLayout container reference**

In `saveLayout()`, the container reference is already handled by the rename in step 2.

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/static/js/panels.js
git commit -m "feat(ui): update panels.js — operations container, tier defaults, localStorage migration

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Update tests and verify

**Files:**
- Modify: `tests/test_web_api.py`

- [ ] **Step 1: Update SPA shell test assertions**

In `tests/test_web_api.py`, find the lines:
```python
        assert "view-monitor" in resp.text
        assert "view-control" in resp.text
```

Replace with:
```python
        assert "view-operations" in resp.text
```

- [ ] **Step 2: Update CSS assertion if present**

Search for any assertion checking for `monitor.css` or `control.css` and replace with `operations.css`.

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_web_api.py
git commit -m "test: update SPA shell assertions for operations view

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Final verification and cleanup

**Files:** None (verification only)

- [ ] **Step 1: Verify no stale references remain**

```bash
grep -r "view-monitor\|view-control\|HydraMonitor\|HydraControl\|control-panels\|monitor-loading\|monitor-stream-lost\|monitor-lock-indicator\|monitor-idle\|monitor\.html\|control\.html\|monitor\.js\|control\.js\|monitor\.css\|control\.css" hydra_detect/web/ tests/ --include="*.html" --include="*.js" --include="*.css" --include="*.py"
```

Expected: no matches (all references updated)

- [ ] **Step 2: Verify all files exist**

```bash
ls hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js hydra_detect/web/static/css/operations.css
```

Expected: all three files exist

- [ ] **Step 3: Verify deleted files are gone**

```bash
ls hydra_detect/web/templates/monitor.html hydra_detect/web/static/js/monitor.js hydra_detect/web/static/css/monitor.css hydra_detect/web/templates/control.html hydra_detect/web/static/js/control.js hydra_detect/web/static/css/control.css 2>&1
```

Expected: all "No such file or directory"

- [ ] **Step 4: Run full test suite one final time**

```bash
python -m pytest tests/ -q
```

Expected: all tests PASS

- [ ] **Step 5: Commit spec and plan docs**

```bash
git add docs/superpowers/
git commit -m "docs: add web UI overhaul spec and implementation plan

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```
