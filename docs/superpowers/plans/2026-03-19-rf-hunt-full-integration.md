# RF Hunt Full Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire rtl_power as a first-class RSSI source alongside Kismet, improve the web UI panel with RSSI chart and state timeline, add a Leaflet map for search pattern and signal visualization, and update docs + Docker.

**Architecture:** 4 independent layers delivered as separate commits. Layer 1 refactors the hunt controller to accept an injected RSSI client (Protocol-based), wires rtl_power into the pipeline and web UI. Layers 2-3 enhance the operations panel with charts and a Leaflet map. Layer 4 is docs and Docker cleanup.

**Tech Stack:** Python 3.10+, FastAPI, Leaflet.js (bundled), rtl_power (rtl-sdr package), canvas-based charts (no library)

**Spec:** `docs/superpowers/specs/2026-03-19-rf-hunt-full-integration-design.md`

---

## Layer 1: rtl_power as First-Class Source

### Task 1: RSSI Client Protocol

**Files:**
- Create: `hydra_detect/rf/rssi_protocol.py`

- [ ] **Step 1: Create the Protocol class**

```python
# hydra_detect/rf/rssi_protocol.py
"""Protocol defining the RSSI client interface for RF hunt."""

from __future__ import annotations

from typing import Protocol


class RSSIClient(Protocol):
    """Interface that KismetClient and RtlPowerClient both satisfy."""

    def check_connection(self) -> bool: ...

    def get_rssi(
        self,
        *,
        mode: str = "wifi",
        bssid: str | None = None,
        freq_mhz: float | None = None,
    ) -> float | None: ...

    def reset_auth(self) -> None: ...

    def close(self) -> None: ...
```

- [ ] **Step 2: Verify both clients satisfy the protocol**

Run: `python -c "from hydra_detect.rf.rssi_protocol import RSSIClient; from hydra_detect.rf.kismet_client import KismetClient; from hydra_detect.rf.rtl_power_client import RtlPowerClient; c1: RSSIClient = KismetClient(); c2: RSSIClient = RtlPowerClient(); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add hydra_detect/rf/rssi_protocol.py
git commit -m "feat(rf): add RSSIClient protocol for hunt source abstraction"
```

---

### Task 2: Refactor hunt controller to accept injected RSSI client

**Files:**
- Modify: `hydra_detect/rf/hunt.py`
- Modify: `tests/test_rf_hunt.py`

- [ ] **Step 1: Update hunt.py constructor**

In `hydra_detect/rf/hunt.py`:

Replace the constructor parameters (lines 79-143). Remove `kismet_host`, `kismet_user`, `kismet_pass` params. Add `rssi_client` param. Remove the internal `KismetClient(...)` creation. Rename all `self._kismet` to `self._rssi_client`. Add `self._consecutive_poll_failures = 0` and `self._max_poll_failures = 10`. Add `self._state_history: deque = deque(maxlen=10)` and `self._start_time = 0.0`.

Key changes to the constructor:

```python
def __init__(
    self,
    mavlink,
    *,
    rssi_client,  # RSSIClient -- KismetClient or RtlPowerClient
    # Target specification
    mode: str = "wifi",
    target_bssid: str | None = None,
    target_freq_mhz: float | None = None,
    # Search pattern
    search_pattern: str = "lawnmower",
    search_area_m: float = 100.0,
    search_spacing_m: float = 20.0,
    search_alt_m: float = 15.0,
    # RSSI thresholds
    rssi_threshold_dbm: float = -80.0,
    rssi_converge_dbm: float = -40.0,
    rssi_window: int = 10,
    # Gradient
    gradient_step_m: float = 5.0,
    gradient_rotation_deg: float = 45.0,
    # Timing
    poll_interval_sec: float = 0.5,
    arrival_tolerance_m: float = 3.0,
    # Callbacks
    on_state_change: Callable[[HuntState], None] | None = None,
    kismet_manager: KismetManager | None = None,
):
```

In the body, replace:
```python
self._kismet = KismetClient(
    host=kismet_host, user=kismet_user, password=kismet_pass,
)
```
with:
```python
self._rssi_client = rssi_client
```

Add after `self._lock`:
```python
self._consecutive_poll_failures = 0
self._max_poll_failures = 10
self._state_history: deque[tuple[str, float]] = deque(maxlen=10)
self._start_time = 0.0
```

- [ ] **Step 2: Rename all `self._kismet` references to `self._rssi_client`**

In `_poll_rssi` (line 292-315), `start` (line 195), `stop` (line 253), `_run_loop` (line 289) -- replace every `self._kismet` with `self._rssi_client`.

Remove the `from .kismet_client import KismetClient` import (line 21) since we no longer create it internally.

- [ ] **Step 3: Add poll failure counter to `_poll_rssi`**

Update `_poll_rssi` to track consecutive failures:

```python
def _poll_rssi(self) -> float | None:
    """Poll RSSI source, restarting Kismet once on failure if available."""
    rssi = self._rssi_client.get_rssi(
        mode=self._mode,
        bssid=self._target_bssid,
        freq_mhz=self._target_freq_mhz,
    )
    if rssi is not None:
        self._consecutive_poll_failures = 0
        return rssi

    self._consecutive_poll_failures += 1
    if self._consecutive_poll_failures >= self._max_poll_failures:
        logger.error(
            "RSSI source unresponsive (%d consecutive failures) -- aborting",
            self._consecutive_poll_failures,
        )
        self._set_state(HuntState.ABORTED)
        self._mavlink.send_statustext("RF HUNT: Source lost", severity=3)
        return None

    # Try Kismet restart if available
    if self._kismet_manager is None:
        return None
    if not self._rssi_client.check_connection():
        logger.warning("Kismet connection lost -- attempting restart")
        if self._kismet_manager.restart(stop_event=self._stop_evt):
            self._rssi_client.reset_auth()
            return self._rssi_client.get_rssi(
                mode=self._mode,
                bssid=self._target_bssid,
                freq_mhz=self._target_freq_mhz,
            )
        logger.error("Kismet restart failed")
    return None
```

- [ ] **Step 4: Add state history tracking to `_set_state`**

In `_set_state`, after setting the state, append to history:

```python
def _set_state(self, new_state: HuntState) -> None:
    with self._lock:
        old = self._state
        self._state = new_state
    if old != new_state:
        logger.info("RF Hunt: %s -> %s", old.value, new_state.value)
        elapsed = time.monotonic() - self._start_time if self._start_time else 0.0
        self._state_history.append((new_state.value, round(elapsed, 1)))
        if self._on_state_change:
            try:
                self._on_state_change(new_state)
            except (TypeError, ValueError) as exc:
                logger.warning("State change callback error: %s", exc)
```

Add `import time` if not already present. Set `self._start_time = time.monotonic()` in `start()` before launching the thread.

- [ ] **Step 5: Add `last_rssi` and `state_history` to `get_status()`**

```python
def get_status(self) -> dict:
    best_rssi = self._navigator.get_best_rssi()
    best_pos = self._navigator.get_best_position()
    sample_count = self._navigator.get_sample_count()
    with self._lock:
        return {
            "state": self._state.value,
            "mode": self._mode,
            "target": self._target_bssid or f"{self._target_freq_mhz} MHz",
            "best_rssi": round(best_rssi, 1),
            "best_lat": round(best_pos[0], 7),
            "best_lon": round(best_pos[1], 7),
            "samples": sample_count,
            "wp_progress": f"{self._wp_index}/{len(self._waypoints)}",
            "last_rssi": round(self._last_rssi, 1),
            "state_history": list(self._state_history),
        }
```

- [ ] **Step 6: Update `_do_search` to track `_last_rssi` for all readings**

In `_do_search`, after `smoothed = self._filter.add(rssi)`, add:
```python
self._last_rssi = smoothed
```
(Currently only set when transitioning to HOMING. We need it for every reading so the chart shows data during search.)

- [ ] **Step 7: Update tests**

In `tests/test_rf_hunt.py`, update `_make_controller()` to pass `rssi_client=` instead of relying on internal KismetClient creation:

```python
from unittest.mock import MagicMock

def _make_controller(mav=None, **overrides):
    if mav is None:
        mav = _make_mavlink()
    mock_client = MagicMock()
    mock_client.check_connection.return_value = True
    mock_client.get_rssi.return_value = None
    mock_client.close = MagicMock()
    mock_client.reset_auth = MagicMock()
    defaults = dict(
        rssi_client=mock_client,
        mode="wifi",
        target_bssid="AA:BB:CC:DD:EE:FF",
        search_area_m=50.0,
        search_spacing_m=10.0,
        search_alt_m=15.0,
        rssi_threshold_dbm=-80.0,
        rssi_converge_dbm=-40.0,
        poll_interval_sec=0.01,
        arrival_tolerance_m=3.0,
    )
    defaults.update(overrides)
    return RFHuntController(mav, **defaults)
```

Update all test methods that manually set `ctrl._kismet = MagicMock()` to instead set `ctrl._rssi_client = MagicMock()`. Search for `_kismet` in the test file and replace with `_rssi_client`.

Also update `TestHuntKismetClient` class (line 252-261) -- this test verified the internal KismetClient host/user/pass, which no longer applies. Replace with a test that verifies the injected client is stored:

```python
class TestHuntRSSIClient:
    def test_injected_client_stored(self):
        client = MagicMock()
        ctrl = _make_controller(rssi_client=client)
        assert ctrl._rssi_client is client
```

- [ ] **Step 8: Run tests**

Run: `python -m pytest tests/test_rf_hunt.py -v --tb=short`
Expected: All pass

- [ ] **Step 9: Run full RF test suite**

Run: `python -m pytest tests/test_rf_*.py -v --tb=short`
Expected: All pass (integration tests may skip if no dongle -- that's OK)

- [ ] **Step 10: Commit**

```bash
git add hydra_detect/rf/hunt.py tests/test_rf_hunt.py
git commit -m "refactor(rf): inject RSSI client into hunt controller

Remove hardcoded KismetClient creation from RFHuntController. Accept
rssi_client parameter so either KismetClient or RtlPowerClient can
be used. Add poll failure counter, state history tracking, and
last_rssi to status response."
```

---

### Task 3: Unit tests for RtlPowerClient

**Files:**
- Create: `tests/test_rf_rtl_power.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for RtlPowerClient -- mock subprocess to avoid real SDR."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import subprocess

from hydra_detect.rf.rtl_power_client import RtlPowerClient


class TestRtlPowerInterface:
    """Verify RtlPowerClient satisfies the RSSIClient protocol."""

    def test_has_check_connection(self):
        client = RtlPowerClient()
        assert callable(client.check_connection)

    def test_has_get_rssi(self):
        client = RtlPowerClient()
        assert callable(client.get_rssi)

    def test_has_reset_auth(self):
        client = RtlPowerClient()
        client.reset_auth()  # no-op, should not raise

    def test_has_close(self):
        client = RtlPowerClient()
        client.close()  # no-op, should not raise

    def test_context_manager(self):
        with RtlPowerClient() as client:
            assert client is not None


class TestRtlPowerGetRSSI:
    def test_returns_none_without_freq(self):
        client = RtlPowerClient()
        assert client.get_rssi(mode="sdr", freq_mhz=None) is None

    @patch("hydra_detect.rf.rtl_power_client._start_rtl_power")
    def test_returns_peak_from_scan(self, mock_start):
        proc = MagicMock()
        proc.stdout = iter([
            "2026-03-19, 12:00:00, 910000000, 915000000, 100000, 8192, -5.0, -3.2, 2.5, -1.0\n",
            "2026-03-19, 12:00:00, 915000000, 920000000, 100000, 8192, -2.0, 6.7, 1.0, -4.0\n",
        ])
        proc.wait.return_value = 0
        mock_start.return_value = proc

        client = RtlPowerClient(tolerance_mhz=5.0)
        rssi = client.get_rssi(mode="sdr", freq_mhz=915.0)
        assert rssi == 6.7  # peak across both lines

    @patch("hydra_detect.rf.rtl_power_client._start_rtl_power")
    def test_returns_none_on_empty_output(self, mock_start):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = 0
        mock_start.return_value = proc

        client = RtlPowerClient()
        assert client.get_rssi(mode="sdr", freq_mhz=433.0) is None

    @patch("hydra_detect.rf.rtl_power_client._start_rtl_power")
    def test_handles_timeout(self, mock_start):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="rtl_power", timeout=5)
        proc.pid = 12345
        mock_start.return_value = proc

        client = RtlPowerClient()
        assert client.get_rssi(mode="sdr", freq_mhz=915.0) is None


class TestRtlPowerCheckConnection:
    @patch("hydra_detect.rf.rtl_power_client.shutil.which", return_value=None)
    def test_fails_if_binary_missing(self, mock_which):
        client = RtlPowerClient()
        assert client.check_connection() is False

    @patch("hydra_detect.rf.rtl_power_client._start_rtl_power")
    @patch("hydra_detect.rf.rtl_power_client.shutil.which", return_value="/usr/bin/rtl_power")
    def test_succeeds_when_scan_returns_data(self, mock_which, mock_start):
        proc = MagicMock()
        proc.stdout = iter([
            "2026-03-19, 12:00:00, 430000000, 440000000, 100000, 8192, -10.0, -8.5\n",
        ])
        proc.wait.return_value = 0
        mock_start.return_value = proc

        client = RtlPowerClient()
        assert client.check_connection() is True
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_rf_rtl_power.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_rf_rtl_power.py
git commit -m "test(rf): add unit tests for RtlPowerClient"
```

---

### Task 4: Wire rtl_power into pipeline and web API

**Files:**
- Modify: `hydra_detect/pipeline.py:222-269`
- Modify: `hydra_detect/web/server.py:628-635` (validation ranges)
- Modify: `config.ini:70-91`

- [ ] **Step 1: Add new config fields to `config.ini`**

Add after the existing `[rf_homing]` section's last line:

```ini
rssi_source = kismet
rtl_power_tolerance_mhz = 5.0
```

- [ ] **Step 2: Refactor pipeline RF init block**

In `hydra_detect/pipeline.py`, replace the RF init block (lines 225-267) with source-aware logic. Add import at top: `from hydra_detect.rf.rtl_power_client import RtlPowerClient`

The new init block creates either `RtlPowerClient` or `KismetClient` based on `rssi_source` config, enforces `poll_interval >= 2.0` for rtl_power, and passes `rssi_client=` to `RFHuntController`. See spec section 1.3 for the full code.

- [ ] **Step 3: Update `_handle_rf_start` to support source switching**

In `pipeline.py`, update `_handle_rf_start` to accept `rssi_source` param and handle dongle mutex: stop KismetManager before starting rtl_power and vice versa. See spec section 1.3 for the dongle mutex logic.

- [ ] **Step 4: Update `_get_rf_status` to include source**

```python
def _get_rf_status(self) -> dict:
    if self._rf_hunt is not None:
        status = self._rf_hunt.get_status()
        status["rssi_source"] = getattr(self, "_rssi_source", "kismet")
        return status
    return {"state": "unavailable"}
```

- [ ] **Step 5: Widen validation ranges in `server.py`**

In `hydra_detect/web/server.py`, change the threshold validation (line 632-633):

```python
("rssi_threshold_dbm", -100.0, 30.0),
("rssi_converge_dbm", -90.0, 30.0),
```

Also add `rssi_source` validation near the top of the RF start handler:

```python
rssi_source = body.get("rssi_source")
if rssi_source is not None and rssi_source not in ("kismet", "rtl_power"):
    return JSONResponse({"error": "rssi_source must be 'kismet' or 'rtl_power'"}, 400)
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_rf_*.py -v --tb=short`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add hydra_detect/pipeline.py hydra_detect/web/server.py config.ini
git commit -m "feat(rf): wire rtl_power into pipeline with source switching

Pipeline reads rssi_source from config, creates appropriate client.
Web API accepts rssi_source in /api/rf/start for per-hunt selection.
Dongle mutex: stops Kismet before starting rtl_power and vice versa.
Poll interval enforced >= 2s for rtl_power."
```

---

### Task 5: Web UI source selector

**Files:**
- Modify: `hydra_detect/web/templates/operations.html`
- Modify: `hydra_detect/web/static/js/operations.js`

- [ ] **Step 1: Add source dropdown to operations.html**

In the RF Hunt config section of `operations.html`, add a Source dropdown before the Mode dropdown:

```html
<div class="field">
    <label>Source</label>
    <select id="ctrl-rf-source" class="input-sm">
        <option value="kismet">Kismet (protocol decode)</option>
        <option value="rtl_power">rtl_power (raw signal)</option>
    </select>
</div>
```

Add `id` attributes to the threshold labels: `id="rf-thresh-label"` and `id="rf-converge-label"`.

Add source badge next to state badge: `<span id="rf-source-badge" class="badge badge-blue"></span>`

- [ ] **Step 2: Add source-aware JS to operations.js**

Add a `rfSourceChanged()` function near the existing `rfModeChanged()` that swaps labels ("dBm" vs "dB"), default values, and slider min/max ranges when the source dropdown changes.

Register: `addChange('ctrl-rf-source', () => rfSourceChanged());`

Update `rfStart()` to include `body.rssi_source = document.getElementById('ctrl-rf-source').value;`

Update `updateRFPanel()` to set the source badge text and color from `data.rssi_source`.

- [ ] **Step 3: Test manually**

Start Hydra with `rssi_source = rtl_power` in config, open web UI, verify source dropdown, label swapping, and badge display.

- [ ] **Step 4: Commit**

```bash
git add hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js
git commit -m "feat(ui): add RSSI source selector to RF hunt panel

Source dropdown lets operator choose Kismet or rtl_power per-hunt.
Labels and default thresholds swap based on source selection.
Source badge shows active source during hunt."
```

---

### Task 6: Update integration tests for new constructor

**Files:**
- Modify: `tests/test_rf_integration.py`

- [ ] **Step 1: Update integration test hunt controller creation**

In `tests/test_rf_integration.py`, update `TestHuntControllerWithKismet` to pass `rssi_client=` to `RFHuntController`. Create a `KismetClient` instance and pass it as `rssi_client`. Apply to all test methods that create `RFHuntController`.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/test_rf_*.py -v --tb=short`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_rf_integration.py
git commit -m "test(rf): update integration tests for injected RSSI client"
```

---

## Layer 2: Improved RF Panel

### Task 7: RSSI sparkline chart, progress bar, and state timeline

**Files:**
- Modify: `hydra_detect/web/templates/operations.html`
- Modify: `hydra_detect/web/static/js/operations.js`

- [ ] **Step 1: Add HTML elements**

In `operations.html` RF status section, add:
- Canvas element for RSSI sparkline: `<canvas id="rf-rssi-chart" width="300" height="60">`
- Progress bar div with inner fill bar and overlay text
- State timeline div for rendering state history badges

All initially hidden (`style="display:none;"`) and shown when a hunt is active.

- [ ] **Step 2: Implement sparkline drawing in JS**

Add `rfRssiHistory` array (max 60 entries) and `drawRFSparkline(rssi, threshold, converge)` function that draws:
- Green RSSI line
- Dashed yellow threshold line
- Dashed red converge line
- Auto-scaling Y axis

- [ ] **Step 3: Wire into `updateRFPanel()`**

On each poll, push `data.last_rssi` into history array, call `drawRFSparkline()`. Update progress bar width from `data.wp_progress`. Render `data.state_history` as colored badges in the timeline div using `textContent` for safe rendering.

- [ ] **Step 4: Test manually and commit**

```bash
git add hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js
git commit -m "feat(ui): add RSSI sparkline, progress bar, and state timeline to RF panel"
```

---

## Layer 3: Leaflet Map

### Task 8: Efficient sample access and waypoints endpoint

**Files:**
- Modify: `hydra_detect/rf/navigator.py`
- Modify: `hydra_detect/pipeline.py`
- Modify: `hydra_detect/web/server.py`

- [ ] **Step 1: Add `get_recent_samples` to navigator**

```python
def get_recent_samples(self, n: int = 200) -> list[RSSISample]:
    """Return the last *n* samples (thread-safe)."""
    with self._lock:
        if len(self.samples) <= n:
            return list(self.samples)
        return list(self.samples)[-n:]
```

- [ ] **Step 2: Add `GET /api/rf/waypoints` endpoint**

New endpoint in `server.py` that returns the search pattern waypoints as `[[lat, lon], ...]`. Wire via a `get_rf_waypoints` callback from pipeline.

- [ ] **Step 3: Add `recent_samples` and vehicle position to status**

In `pipeline.py` `_get_rf_status`, append `recent_samples` (last 200, lat/lon/rssi dicts) and `vehicle_lat`/`vehicle_lon` from MAVLink GPS.

- [ ] **Step 4: Test and commit**

```bash
git add hydra_detect/rf/navigator.py hydra_detect/pipeline.py hydra_detect/web/server.py
git commit -m "feat(rf): add waypoints endpoint and recent_samples to status for map"
```

---

### Task 9: Bundle Leaflet and build the map

**Files:**
- Create: `hydra_detect/web/static/js/vendor/leaflet.min.js`
- Create: `hydra_detect/web/static/css/vendor/leaflet.min.css`
- Modify: `hydra_detect/web/templates/operations.html`
- Modify: `hydra_detect/web/static/js/operations.js`

- [ ] **Step 1: Download and bundle Leaflet**

```bash
mkdir -p hydra_detect/web/static/js/vendor hydra_detect/web/static/css/vendor
curl -sL https://unpkg.com/leaflet@1.9.4/dist/leaflet.js -o hydra_detect/web/static/js/vendor/leaflet.min.js
curl -sL https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o hydra_detect/web/static/css/vendor/leaflet.min.css
```

- [ ] **Step 2: Add map container and script includes**

In `operations.html`, add Leaflet CSS/JS includes and a `<div id="rf-map">` container (300px tall, initially hidden).

- [ ] **Step 3: Implement map JS**

In `operations.js`, implement:
- `initRFMap(lat, lon)`: Initialize Leaflet map with OSM tiles. On tile errors > 5, fall back to coordinate grid (no tiles).
- `updateRFMap(data)`: Update vehicle marker (blue dot), best position marker (with tooltip), RSSI sample dots (colored circles: green/yellow/red by strength).
- `loadRFWaypoints()`: Fetch `GET /api/rf/waypoints` once on hunt start, draw as dashed blue polyline.

Wire `updateRFMap()` and `loadRFWaypoints()` into `updateRFPanel()`. Reset waypoints flag on hunt end.

- [ ] **Step 4: Test manually and commit**

```bash
git add hydra_detect/web/static/js/vendor/ hydra_detect/web/static/css/vendor/ \
        hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js
git commit -m "feat(ui): add Leaflet map with search pattern, vehicle, and signal heatmap

Leaflet bundled locally for offline use. Map shows search pattern as
dashed polyline, vehicle position as blue dot, RSSI samples as colored
circles, and best position as labeled marker. Falls back to coordinate
grid when tiles cannot load."
```

---

## Layer 4: Docs + Docker

### Task 10: Documentation and Docker

**Files:**
- Modify: `docs/features/rf-homing.mdx`
- Create: `docs/guides/rf-hunt-testing.md`
- Modify: `Dockerfile`

- [ ] **Step 1: Update rf-homing.mdx**

Add "RSSI Source" section explaining Kismet vs rtl_power, when to use each, and a threshold guidance table. Update config reference with `rssi_source` and `rtl_power_tolerance_mhz`.

- [ ] **Step 2: Create testing guide**

Write `docs/guides/rf-hunt-testing.md` with step-by-step: verify dongle, power scan, demo, integration tests, pipeline config. Include troubleshooting for permissions, port conflicts, Kismet auth, dongle busy.

- [ ] **Step 3: Update Dockerfile**

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends rtl-sdr \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 4: Commit**

```bash
git add docs/features/rf-homing.mdx docs/guides/rf-hunt-testing.md Dockerfile
git commit -m "docs: add rtl_power source docs, testing guide, Docker RTL-SDR support"
```

---

## Final Verification

### Task 11: Full test suite and cleanup

- [ ] **Step 1: Run complete test suite**

```bash
python -m pytest tests/ -v --tb=short
```

- [ ] **Step 2: Run linter**

```bash
flake8 hydra_detect/rf/ tests/test_rf_*.py
```

- [ ] **Step 3: Run type checker**

```bash
mypy hydra_detect/rf/
```

- [ ] **Step 4: Fix any issues and commit**

- [ ] **Step 5: Verify demo still works**

```bash
python scripts/rf_hunt_demo.py --freq 915 --converge 5
```
