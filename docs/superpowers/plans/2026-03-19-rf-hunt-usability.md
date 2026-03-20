# RF Hunt Usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make RF hunt launchable from the web UI without pre-configuring Kismet, and add RSSI-over-time sparkline + GPS signal map visualizations.

**Architecture:** Two independent features sharing no code. Feature A adds lazy KismetManager creation in `_handle_rf_start()`. Feature B adds a 300-sample RSSI ring buffer on RFHuntController, a new API endpoint, and vanilla JS chart rendering (SVG sparkline + Canvas scatter plot).

**Tech Stack:** Python 3.10+, FastAPI, vanilla JavaScript (SVG + Canvas), collections.deque

---

## File Map

| File | Change |
|------|--------|
| `hydra_detect/rf/hunt.py` | Add `_rssi_history` deque, `_record_rssi()`, `get_rssi_history()` |
| `hydra_detect/pipeline.py` | Kismet auto-start in `_handle_rf_start()`, add `get_rf_rssi_history` callback |
| `hydra_detect/web/server.py` | Add `GET /api/rf/rssi_history` endpoint |
| `hydra_detect/web/templates/operations.html` | Add chart container divs |
| `hydra_detect/web/static/js/operations.js` | Add sparkline + scatter plot rendering + polling |
| `tests/test_rf_hunt.py` | Add `TestRssiHistory` tests |
| `tests/test_pipeline_callbacks.py` | Add `TestKismetAutoStart` tests |

---

### Task 1: RSSI History Data Layer

**Files:**
- Modify: `hydra_detect/rf/hunt.py:14-27` (imports), `hydra_detect/rf/hunt.py:135-143` (`__init__` tail), `hydra_detect/rf/hunt.py:145-157` (public API), `hydra_detect/rf/hunt.py:317-336` (`_do_search`), `hydra_detect/rf/hunt.py:365-412` (`_do_homing`)
- Test: `tests/test_rf_hunt.py`

- [ ] **Step 1: Write failing tests for `_record_rssi` and `get_rssi_history`**

Add to `tests/test_rf_hunt.py`:

```python
import time


class TestRssiHistory:
    def test_record_rssi_appends(self):
        ctrl = _make_controller()
        ctrl._record_rssi(-72.3)
        history = ctrl.get_rssi_history()
        assert len(history) == 1
        assert history[0]["rssi"] == -72.3
        assert history[0]["lat"] is not None
        assert "t" in history[0]

    def test_record_rssi_with_explicit_gps(self):
        ctrl = _make_controller()
        ctrl._record_rssi(-65.0, lat=35.123, lon=-80.987)
        history = ctrl.get_rssi_history()
        assert history[0]["lat"] == 35.123
        assert history[0]["lon"] == -80.987

    def test_ring_buffer_maxlen(self):
        ctrl = _make_controller()
        for i in range(301):
            ctrl._record_rssi(float(-100 + i))
        history = ctrl.get_rssi_history()
        assert len(history) == 300
        # Oldest (i=0, rssi=-100) should be dropped; first retained is i=1
        assert history[0]["rssi"] == -99.0

    def test_get_rssi_history_empty(self):
        ctrl = _make_controller()
        assert ctrl.get_rssi_history() == []

    def test_get_rssi_history_is_snapshot(self):
        """Returned list is a copy, not a reference to the deque."""
        ctrl = _make_controller()
        ctrl._record_rssi(-70.0)
        h1 = ctrl.get_rssi_history()
        ctrl._record_rssi(-60.0)
        h2 = ctrl.get_rssi_history()
        assert len(h1) == 1
        assert len(h2) == 2

    def test_record_rssi_uses_wall_clock(self):
        ctrl = _make_controller()
        before = time.time()
        ctrl._record_rssi(-70.0)
        after = time.time()
        t = ctrl.get_rssi_history()[0]["t"]
        assert before <= t <= after
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rf_hunt.py::TestRssiHistory -v`
Expected: FAIL — `_record_rssi` not defined

- [ ] **Step 3: Implement `_record_rssi()` and `get_rssi_history()`**

In `hydra_detect/rf/hunt.py`:

1. Add `import time` to the imports (after `import threading`).

2. In `__init__`, after `self._lock = threading.Lock()` (line 143), add:

```python
        self._rssi_history: deque[dict] = deque(maxlen=300)
```

Also add `from collections import deque` to imports.

3. After `get_status()` (in the public API section), add:

```python
    def get_rssi_history(self) -> list[dict]:
        """Return RSSI history for visualization (thread-safe)."""
        with self._lock:
            return list(self._rssi_history)
```

4. In the private methods section, add:

```python
    def _record_rssi(
        self, rssi: float,
        lat: float | None = None, lon: float | None = None,
    ) -> None:
        """Append an RSSI reading to the history ring buffer."""
        if lat is None or lon is None:
            lat, lon, _ = self._mavlink.get_lat_lon()
        with self._lock:
            self._rssi_history.append({
                "t": time.time(),
                "rssi": round(rssi, 1),
                "lat": round(lat, 7) if lat is not None else None,
                "lon": round(lon, 7) if lon is not None else None,
            })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rf_hunt.py::TestRssiHistory -v`
Expected: All 6 PASS

- [ ] **Step 5: Integrate `_record_rssi` calls in `_do_search` and `_do_homing`**

In `_do_search()` (line 321), inside `if rssi is not None:` block, add `self._record_rssi(rssi)` as the **first line** (before the threshold check):

```python
    def _do_search(self) -> None:
        """Fly search pattern while polling for target signal."""
        rssi = self._poll_rssi()
        if rssi is not None:
            self._record_rssi(rssi)  # <-- NEW: record before threshold check
            smoothed = self._filter.add(rssi)
            # ... rest unchanged ...
```

In `_do_homing()` (line 377), after the GPS read `lat, lon, alt = self._mavlink.get_lat_lon()`, add `self._record_rssi(rssi, lat=lat, lon=lon)` passing the already-read GPS:

```python
        smoothed = self._filter.add(rssi)
        lat, lon, alt = self._mavlink.get_lat_lon()
        if lat is None:
            return

        self._record_rssi(rssi, lat=lat, lon=lon)  # <-- NEW: reuse GPS read
        self._navigator.record(smoothed, lat, lon, alt or 0)
```

- [ ] **Step 6: Run full RF hunt test suite**

Run: `python -m pytest tests/test_rf_hunt.py -v`
Expected: All existing + new tests PASS

- [ ] **Step 7: Commit**

```bash
git add hydra_detect/rf/hunt.py tests/test_rf_hunt.py
git commit -m "feat: add RSSI history ring buffer to RFHuntController"
```

---

### Task 2: Kismet Auto-Start

**Files:**
- Modify: `hydra_detect/pipeline.py:955-987` (`_handle_rf_start`)
- Test: `tests/test_pipeline_callbacks.py`

- [ ] **Step 1: Write failing tests for Kismet auto-start**

Add to `tests/test_pipeline_callbacks.py`:

```python
from hydra_detect.rf.kismet_manager import KismetManager


class TestKismetAutoStart:
    def test_auto_start_creates_kismet_manager(self):
        """When _kismet_manager is None, _handle_rf_start creates one."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._rf_hunt = None
        p._kismet_manager = None

        with patch.object(KismetManager, "__init__", return_value=None) as mock_init, \
             patch.object(KismetManager, "start", return_value=True), \
             patch("hydra_detect.pipeline.RFHuntController") as mock_ctrl:
            mock_ctrl.return_value.start.return_value = True
            result = p._handle_rf_start({"mode": "wifi"})

        assert result is True
        assert p._kismet_manager is not None

    def test_auto_start_failure_returns_false(self):
        """When Kismet auto-start fails, return False and reset manager."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._rf_hunt = None
        p._kismet_manager = None

        with patch.object(KismetManager, "__init__", return_value=None), \
             patch.object(KismetManager, "start", return_value=False):
            result = p._handle_rf_start({"mode": "wifi"})

        assert result is False
        assert p._kismet_manager is None

    def test_existing_kismet_manager_not_replaced(self):
        """When _kismet_manager already exists, don't create a new one."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._rf_hunt = None
        existing_mgr = MagicMock()
        p._kismet_manager = existing_mgr

        with patch("hydra_detect.pipeline.RFHuntController") as mock_ctrl:
            mock_ctrl.return_value.start.return_value = True
            p._handle_rf_start({"mode": "wifi"})

        assert p._kismet_manager is existing_mgr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline_callbacks.py::TestKismetAutoStart -v`
Expected: FAIL — auto-start logic not yet implemented

- [ ] **Step 3: Implement Kismet auto-start in `_handle_rf_start`**

In `hydra_detect/pipeline.py`, in `_handle_rf_start()`, after the `if self._rf_hunt is not None: self._rf_hunt.stop()` block (line 963) and before building the new controller (line 965), add:

```python
        # Auto-start Kismet if no manager exists
        if self._kismet_manager is None:
            self._kismet_manager = KismetManager(
                source=self._cfg.get("rf_homing", "kismet_source", fallback="rtl433-0"),
                capture_dir=self._cfg.get("rf_homing", "kismet_capture_dir", fallback="./output_data/kismet"),
                host=self._cfg.get("rf_homing", "kismet_host", fallback="http://localhost:2501"),
                user=self._cfg.get("rf_homing", "kismet_user", fallback="kismet"),
                password=self._cfg.get("rf_homing", "kismet_pass", fallback="kismet"),
                log_dir=self._cfg.get("logging", "log_dir", fallback="./output_data/logs"),
                max_capture_mb=self._cfg.getfloat("rf_homing", "kismet_max_capture_mb", fallback=100.0),
            )
            if not self._kismet_manager.start():
                logger.error("Kismet auto-start failed — RF hunt aborted")
                self._kismet_manager = None
                return False
            logger.info("Kismet auto-started for RF hunt")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline_callbacks.py::TestKismetAutoStart -v`
Expected: All 3 PASS

- [ ] **Step 5: Run full pipeline callback test suite**

Run: `python -m pytest tests/test_pipeline_callbacks.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/pipeline.py tests/test_pipeline_callbacks.py
git commit -m "feat: auto-start Kismet on RF hunt if no manager exists"
```

---

### Task 3: RSSI History API Endpoint

**Files:**
- Modify: `hydra_detect/web/server.py:575-583` (RF Hunt section)
- Modify: `hydra_detect/pipeline.py:438-468` (callbacks), `hydra_detect/pipeline.py:949-953` (RF status section)

- [ ] **Step 1: Add the API endpoint in `server.py`**

In `hydra_detect/web/server.py`, after the `GET /api/rf/status` endpoint (line 583), add:

```python
@app.get("/api/rf/rssi_history")
async def api_rf_rssi_history():
    """Return RSSI history for visualization."""
    cb = stream_state.get_callback("get_rf_rssi_history")
    if cb:
        return cb()
    return []
```

- [ ] **Step 2: Add the pipeline callback method**

In `hydra_detect/pipeline.py`, after `_get_rf_status()` (around line 953), add:

```python
    def _get_rf_rssi_history(self) -> list[dict]:
        """Return RSSI history for the web API."""
        if self._rf_hunt is not None:
            return self._rf_hunt.get_rssi_history()
        return []
```

- [ ] **Step 3: Wire the callback in `set_callbacks`**

In `hydra_detect/pipeline.py`, in the `stream_state.set_callbacks(...)` block (around line 457), add after `get_rf_status`:

```python
                get_rf_rssi_history=self._get_rf_rssi_history,
```

- [ ] **Step 4: Test the endpoint manually**

Run: `python -m pytest tests/test_rf_hunt.py tests/test_pipeline_callbacks.py -v`
Expected: All tests still pass (no regression)

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/server.py hydra_detect/pipeline.py
git commit -m "feat: add GET /api/rf/rssi_history endpoint"
```

---

### Task 4: RSSI Sparkline Chart (SVG)

**Files:**
- Modify: `hydra_detect/web/templates/operations.html:211-232` (RF status panel)
- Modify: `hydra_detect/web/static/js/operations.js:533-587` (RF panel update)

- [ ] **Step 1: Add chart container in operations.html**

In `operations.html`, inside `ctrl-rf-status-panel` (after the signal bar div at line 231, before the closing `</div>` at line 232), add:

```html
                <!-- RSSI Sparkline Chart -->
                <div id="ctrl-rf-rssi-chart" style="margin-top:var(--gap-sm); height:120px; width:100%;"></div>
```

- [ ] **Step 2: Add sparkline rendering function in operations.js**

In `operations.js`, before the `updateRFPanel()` function (before line 533), add the rendering function:

```javascript
    // ── RF Visualization ──
    function renderRssiSparkline(data, thresholds) {
        const container = document.getElementById('ctrl-rf-rssi-chart');
        if (!container || !data || data.length < 2) {
            if (container) {
                while (container.firstChild) container.removeChild(container.firstChild);
            }
            return;
        }

        const W = container.clientWidth || 300;
        const H = container.clientHeight || 120;
        const PAD = { top: 10, right: 10, bottom: 20, left: 40 };
        const plotW = W - PAD.left - PAD.right;
        const plotH = H - PAD.top - PAD.bottom;

        // Y range: RSSI in dBm
        const yMin = -100, yMax = -20;
        const yScale = (v) => PAD.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
        // X range: time
        const tMin = data[0].t, tMax = data[data.length - 1].t;
        const tSpan = Math.max(tMax - tMin, 1);
        const xScale = (t) => PAD.left + ((t - tMin) / tSpan) * plotW;

        // Build polyline points
        const points = data.map(d =>
            xScale(d.t).toFixed(1) + ',' + yScale(d.rssi).toFixed(1)
        ).join(' ');

        // Determine line color based on trend
        const recent = data.slice(-10);
        let trend = 'var(--color-warn)';
        if (recent.length >= 2) {
            const diff = recent[recent.length - 1].rssi - recent[0].rssi;
            if (diff > 3) trend = 'var(--color-ok)';
            else if (diff < -3) trend = 'var(--color-danger)';
        }

        // Threshold lines
        const detectTh = thresholds.detect || -80;
        const convergeTh = thresholds.converge || -40;

        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('width', W);
        svg.setAttribute('height', H);
        svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

        // Helper to create SVG elements
        function svgEl(tag, attrs) {
            const el = document.createElementNS(ns, tag);
            for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
            return el;
        }

        // Background
        svg.appendChild(svgEl('rect', {
            x: PAD.left, y: PAD.top, width: plotW, height: plotH,
            fill: 'rgba(0,0,0,0.2)', rx: '2'
        }));

        // Threshold dashed lines
        [
            { val: detectTh, label: 'det ' + detectTh },
            { val: convergeTh, label: 'conv ' + convergeTh }
        ].forEach(function(th) {
            const y = yScale(th.val);
            if (y >= PAD.top && y <= PAD.top + plotH) {
                svg.appendChild(svgEl('line', {
                    x1: PAD.left, y1: y, x2: PAD.left + plotW, y2: y,
                    stroke: 'rgba(255,255,255,0.3)', 'stroke-dasharray': '4,3', 'stroke-width': '1'
                }));
                const text = document.createElementNS(ns, 'text');
                text.setAttribute('x', PAD.left + 3);
                text.setAttribute('y', y - 3);
                text.setAttribute('fill', 'rgba(255,255,255,0.5)');
                text.setAttribute('font-size', '9');
                text.textContent = th.label;
                svg.appendChild(text);
            }
        });

        // Data polyline
        svg.appendChild(svgEl('polyline', {
            points: points, fill: 'none', stroke: trend, 'stroke-width': '1.5'
        }));

        // Y-axis labels
        [-100, -80, -60, -40, -20].forEach(function(v) {
            const text = document.createElementNS(ns, 'text');
            text.setAttribute('x', PAD.left - 3);
            text.setAttribute('y', yScale(v) + 3);
            text.setAttribute('fill', 'rgba(255,255,255,0.4)');
            text.setAttribute('font-size', '9');
            text.setAttribute('text-anchor', 'end');
            text.textContent = v;
            svg.appendChild(text);
        });

        // X-axis label
        var xLabel = document.createElementNS(ns, 'text');
        xLabel.setAttribute('x', PAD.left + plotW);
        xLabel.setAttribute('y', H - 3);
        xLabel.setAttribute('fill', 'rgba(255,255,255,0.4)');
        xLabel.setAttribute('font-size', '9');
        xLabel.setAttribute('text-anchor', 'end');
        xLabel.textContent = 'now';
        svg.appendChild(xLabel);

        // Replace container content
        while (container.firstChild) container.removeChild(container.firstChild);
        container.appendChild(svg);
    }
```

- [ ] **Step 3: Add RSSI history fetch and rendering to `updateRFPanel()`**

In `operations.js`, inside `updateRFPanel()`, after the signal bar update block (after line 585), add the history fetch:

```javascript
            // Fetch and render RSSI history
            if (isActive) {
                fetch('/api/rf/rssi_history')
                    .then(function(r) { return r.json(); })
                    .then(function(historyData) {
                        const rf = HydraApp.state.rfStatus || {};
                        renderRssiSparkline(historyData, {
                            detect: rf.rssi_threshold || -80,
                            converge: rf.rssi_converge || -40
                        });
                        renderSignalMap(historyData);
                    })
                    .catch(function() {});
            }
```

- [ ] **Step 4: Verify no JS errors with manual testing**

Run: `python -m pytest tests/ -v --ignore=tests/test_web_server.py`
Expected: All tests pass (no Python regressions)

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js
git commit -m "feat: add RSSI sparkline SVG chart to RF panel"
```

---

### Task 5: GPS Signal Map (Canvas)

**Files:**
- Modify: `hydra_detect/web/templates/operations.html` (after sparkline container)
- Modify: `hydra_detect/web/static/js/operations.js` (add render function)

- [ ] **Step 1: Add canvas container in operations.html**

In `operations.html`, after the sparkline div added in Task 4, add:

```html
                <!-- GPS Signal Map -->
                <canvas id="ctrl-rf-signal-map" style="margin-top:var(--gap-sm); width:100%; height:200px;"></canvas>
```

- [ ] **Step 2: Add scatter plot rendering function in operations.js**

In `operations.js`, after the `renderRssiSparkline` function, add:

```javascript
    function renderSignalMap(data) {
        const canvas = document.getElementById('ctrl-rf-signal-map');
        if (!canvas || !data || data.length < 1) return;
        const ctx = canvas.getContext('2d');

        // Set actual pixel size to match display size
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * (window.devicePixelRatio || 1);
        canvas.height = rect.height * (window.devicePixelRatio || 1);
        ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
        const W = rect.width, H = rect.height;

        ctx.clearRect(0, 0, W, H);

        // Filter points with valid GPS
        const gpsData = data.filter(function(d) { return d.lat != null && d.lon != null; });
        if (gpsData.length === 0) return;

        // Bounding box with padding
        let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
        gpsData.forEach(function(d) {
            if (d.lat < minLat) minLat = d.lat;
            if (d.lat > maxLat) maxLat = d.lat;
            if (d.lon < minLon) minLon = d.lon;
            if (d.lon > maxLon) maxLon = d.lon;
        });

        // Add padding (10% or minimum spread)
        const latSpan = Math.max(maxLat - minLat, 0.00005);
        const lonSpan = Math.max(maxLon - minLon, 0.00005);
        const pad = 0.1;
        minLat -= latSpan * pad; maxLat += latSpan * pad;
        minLon -= lonSpan * pad; maxLon += lonSpan * pad;

        const PAD = 15;
        const plotW = W - 2 * PAD, plotH = H - 2 * PAD;
        function toX(lon) { return PAD + ((lon - minLon) / (maxLon - minLon)) * plotW; }
        function toY(lat) { return PAD + plotH - ((lat - minLat) / (maxLat - minLat)) * plotH; }

        // Background
        ctx.fillStyle = 'rgba(0,0,0,0.2)';
        ctx.fillRect(PAD, PAD, plotW, plotH);

        // Get thresholds from current RF status
        var rf = HydraApp.state.rfStatus || {};
        var detectTh = rf.rssi_threshold || -80;
        var convergeTh = rf.rssi_converge || -40;

        // Track best reading
        var bestIdx = 0;
        gpsData.forEach(function(d, i) {
            if (d.rssi > gpsData[bestIdx].rssi) bestIdx = i;
        });

        // Draw dots (older = more transparent)
        gpsData.forEach(function(d, i) {
            var alpha = 0.3 + 0.7 * (i / (gpsData.length - 1 || 1));
            var color;
            if (d.rssi >= convergeTh) color = 'rgba(74,124,46,' + alpha + ')';
            else if (d.rssi >= detectTh) color = 'rgba(234,179,8,' + alpha + ')';
            else color = 'rgba(197,48,48,' + alpha + ')';

            ctx.beginPath();
            ctx.arc(toX(d.lon), toY(d.lat), 4, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
        });

        // Current position triangle (last point)
        var last = gpsData[gpsData.length - 1];
        var cx = toX(last.lon), cy = toY(last.lat);
        ctx.beginPath();
        ctx.moveTo(cx, cy - 7);
        ctx.lineTo(cx - 5, cy + 4);
        ctx.lineTo(cx + 5, cy + 4);
        ctx.closePath();
        ctx.fillStyle = '#fff';
        ctx.fill();

        // Best position star
        if (bestIdx !== gpsData.length - 1) {
            var best = gpsData[bestIdx];
            var bx = toX(best.lon), by = toY(best.lat);
            ctx.beginPath();
            for (var j = 0; j < 5; j++) {
                var angle = -Math.PI / 2 + j * (2 * Math.PI / 5);
                var r = j % 2 === 0 ? 6 : 3;
                var method = j === 0 ? 'moveTo' : 'lineTo';
                ctx[method](bx + r * Math.cos(angle), by + r * Math.sin(angle));
            }
            ctx.closePath();
            ctx.fillStyle = '#ffd700';
            ctx.fill();
        }
    }
```

- [ ] **Step 3: Verify the `renderSignalMap` call is already wired**

The fetch in Task 4 Step 3 already calls `renderSignalMap(historyData)`. Verify no additional wiring needed.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js
git commit -m "feat: add GPS signal map canvas to RF panel"
```

---

### Task 6: Full Test Suite + Lint

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Run linter**

Run: `flake8 hydra_detect/ tests/ --max-line-length=120`
Expected: No new errors

- [ ] **Step 3: Run type checker**

Run: `mypy hydra_detect/rf/hunt.py hydra_detect/pipeline.py hydra_detect/web/server.py --ignore-missing-imports`
Expected: No new errors

- [ ] **Step 4: Verify commit history**

Run: `git log --oneline -5`
Expected: 4 new commits (RSSI buffer, Kismet auto-start, API endpoint, sparkline, signal map)
