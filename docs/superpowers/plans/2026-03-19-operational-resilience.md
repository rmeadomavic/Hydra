# Operational Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable GPS-less RF scanning, persistent log files with API access, and Docker-compatible Kismet management.

**Architecture:** Three independent features. (1) Scan-only mode adds a `_do_scan()` loop to `RFHuntController` that polls RSSI without navigation. Simulated GPS adds fallback coords in `MAVLinkIO.get_lat_lon()`. (2) `RotatingFileHandler` persists app logs to disk; a new `/api/logs` endpoint tails the file. (3) `KismetManager.start()` reordered to try HTTP connection before subprocess spawn, controlled by `kismet_auto_spawn` config flag.

**Tech Stack:** Python 3.10+, FastAPI, configparser, logging.handlers.RotatingFileHandler, threading

---

## File Structure

| File | Responsibility | Change |
|------|---------------|--------|
| `config.ini` | All tunables | Add `gps_required`, `sim_gps_lat`, `sim_gps_lon`, `app_log_file`, `app_log_level`, `kismet_auto_spawn` |
| `hydra_detect/rf/hunt.py` | RF hunt state machine | Add `gps_required` param, `SCANNING` state, `_do_scan()` method, expose flag in `get_status()` |
| `hydra_detect/mavlink_io.py` | MAVLink + GPS | Add `sim_gps_lat`/`sim_gps_lon` fallback in `get_lat_lon()` and `gps_fix_ok`, add `is_sim_gps` property |
| `hydra_detect/pipeline.py` | Orchestrator | Pass `gps_required` to hunt controller, add `RotatingFileHandler` setup |
| `hydra_detect/web/server.py` | Web API | Add `GET /api/logs` endpoint |
| `hydra_detect/web/static/js/operations.js` | Operations UI | Show "SCAN ONLY" pill, handle no-GPS signal map, show "(SIM)" GPS indicator |
| `hydra_detect/rf/kismet_manager.py` | Kismet lifecycle | Add `auto_spawn` flag, reorder `start()` to connect-first |
| `scripts/kismet.service` | Host systemd unit | New file — runs Kismet before hydra-detect |
| `tests/test_rf_hunt.py` | Hunt tests | Add scan-only tests |
| `tests/test_mavlink_sim_gps.py` | Sim GPS tests | New file |
| `tests/test_log_endpoint.py` | Log API tests | New file |
| `tests/test_rf_kismet_manager.py` | Kismet tests | Add connect-first tests |

---

### Task 1: Scan-Only Mode in RF Hunt Controller

**Files:**
- Modify: `hydra_detect/rf/hunt.py`
- Modify: `config.ini`
- Test: `tests/test_rf_hunt.py`

- [ ] **Step 1: Add config flag to config.ini**

Add `gps_required = true` to the `[rf_homing]` section, after `arrival_tolerance_m`:

```ini
gps_required = true
```

- [ ] **Step 2: Write failing tests for scan-only mode**

Add a new test class `TestScanOnlyMode` to `tests/test_rf_hunt.py`:

```python
class TestScanOnlyMode:
    """Scan-only mode: RSSI polling without GPS/navigation."""

    def test_scan_only_flag_stored(self):
        ctrl = _make_controller(gps_required=False)
        assert ctrl._gps_required is False

    def test_gps_required_defaults_true(self):
        ctrl = _make_controller()
        assert ctrl._gps_required is True

    @patch.object(RFHuntController, "_poll_rssi", return_value=-65.0)
    def test_scan_only_start_succeeds_without_gps(self, mock_poll):
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)  # No GPS
        ctrl = _make_controller(mav=mav, gps_required=False)
        with patch.object(ctrl._kismet, "check_connection", return_value=True):
            assert ctrl.start() is True
            assert ctrl.state == HuntState.SCANNING

    def test_scan_only_get_status_includes_flag(self):
        ctrl = _make_controller(gps_required=False)
        status = ctrl.get_status()
        assert status["gps_required"] is False

    @patch.object(RFHuntController, "_poll_rssi", return_value=-65.0)
    def test_do_scan_records_rssi(self, mock_poll):
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        ctrl = _make_controller(mav=mav, gps_required=False)
        ctrl._set_state(HuntState.SCANNING)
        ctrl._do_scan()
        history = ctrl.get_rssi_history()
        assert len(history) == 1
        assert history[0]["rssi"] == -65.0
        assert history[0]["lat"] is None

    @patch.object(RFHuntController, "_poll_rssi", return_value=None)
    def test_do_scan_tolerates_no_reading(self, mock_poll):
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        ctrl = _make_controller(mav=mav, gps_required=False)
        ctrl._set_state(HuntState.SCANNING)
        ctrl._do_scan()
        assert len(ctrl.get_rssi_history()) == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_rf_hunt.py::TestScanOnlyMode -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'gps_required'`

- [ ] **Step 4: Implement scan-only mode in hunt.py**

In `RFHuntController.__init__()`, add `gps_required` parameter after `kismet_manager`:

```python
        gps_required: bool = True,
```

Store it:
```python
        self._gps_required = gps_required
```

Add `SCANNING` to the `HuntState` enum (after `IDLE`):
```python
    SCANNING = "scanning"
```

Add `_do_scan()` method after `_record_rssi()`:

```python
    def _do_scan(self) -> None:
        """Poll RSSI without navigation — scan-only mode."""
        rssi = self._poll_rssi()
        if rssi is not None:
            smoothed = self._filter.add(rssi)
            self._record_rssi(rssi)
            logger.info("[SCAN] Signal: %.1f dBm (avg %.1f)", rssi, smoothed)
```

Modify `start()` to skip GPS check and waypoint generation when `gps_required=False`:

Replace lines 207-229 (from `# Get current position...` through `self._wp_index = 0`) with:

```python
        if self._gps_required:
            # Get current position for search pattern center
            lat, lon, alt = self._mavlink.get_lat_lon()
            if lat is None or lon is None:
                logger.error("RF hunt requires GPS fix — aborting")
                return False

            # Generate search pattern
            if self._search_pattern == "spiral":
                self._waypoints = generate_spiral(
                    lat, lon,
                    max_radius_m=self._search_area_m / 2,
                    spacing_m=self._search_spacing_m,
                    alt=self._search_alt_m,
                )
            else:
                self._waypoints = generate_lawnmower(
                    lat, lon,
                    width_m=self._search_area_m,
                    height_m=self._search_area_m,
                    spacing_m=self._search_spacing_m,
                    alt=self._search_alt_m,
                )
            self._wp_index = 0
```

Change the initial state set after the GPS/waypoint block:

```python
        initial_state = HuntState.SCANNING if not self._gps_required else HuntState.SEARCHING
        self._set_state(initial_state)
```

In `_run_loop()`, add the SCANNING state handler before the SEARCHING check:

```python
                if state == HuntState.SCANNING:
                    self._do_scan()
                elif state == HuntState.SEARCHING:
```

In `get_status()`, add `gps_required` to the returned dict:

```python
                "gps_required": self._gps_required,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_rf_hunt.py -v`
Expected: All PASS (including existing tests — `gps_required=True` is the default)

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/rf/hunt.py config.ini tests/test_rf_hunt.py
git commit -m "feat: add scan-only mode for GPS-less RF hunt"
```

---

### Task 2: Simulated GPS Fallback in MAVLinkIO

**Files:**
- Modify: `hydra_detect/mavlink_io.py`
- Modify: `config.ini`
- Test: `tests/test_mavlink_sim_gps.py`

- [ ] **Step 1: Add config flags to config.ini**

Add to `[mavlink]` section after `geo_tracking = true`:

```ini
sim_gps_lat =
sim_gps_lon =
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_mavlink_sim_gps.py`:

```python
"""Tests for simulated GPS fallback in MAVLinkIO."""

from __future__ import annotations

from hydra_detect.mavlink_io import MAVLinkIO


def _make_mav(*, sim_lat=None, sim_lon=None, min_gps_fix=3):
    """Build MAVLinkIO with sim GPS config but no real connection."""
    mav = MAVLinkIO(
        connection_string="udp:127.0.0.1:14550",
        min_gps_fix=min_gps_fix,
        sim_gps_lat=sim_lat,
        sim_gps_lon=sim_lon,
    )
    return mav


class TestSimGpsDisabled:
    def test_no_sim_returns_none(self):
        mav = _make_mav()
        lat, lon, alt = mav.get_lat_lon()
        assert lat is None

    def test_is_sim_gps_false_by_default(self):
        mav = _make_mav()
        assert mav.is_sim_gps is False


class TestSimGpsFallback:
    def test_sim_gps_returns_coords_when_no_fix(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        # No real GPS data set — should fall back to sim
        lat, lon, alt = mav.get_lat_lon()
        assert lat == 34.05
        assert lon == -118.25
        assert alt == 30.0  # default sim altitude

    def test_sim_gps_fix_ok(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        assert mav.gps_fix_ok is True

    def test_is_sim_gps_true_when_using_sim(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        # Trigger the sim path
        mav.get_lat_lon()
        assert mav.is_sim_gps is True

    def test_real_gps_takes_priority(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=-118.25)
        # Simulate real GPS data arriving
        with mav._gps_lock:
            mav._gps["lat"] = int(40.7128 * 1e7)
            mav._gps["lon"] = int(-74.006 * 1e7)
            mav._gps["alt"] = int(10.0 * 1000)
            mav._gps["fix"] = 3
        lat, lon, alt = mav.get_lat_lon()
        assert abs(lat - 40.7128) < 0.001
        assert mav.is_sim_gps is False

    def test_sim_requires_both_coords(self):
        mav = _make_mav(sim_lat=34.05, sim_lon=None)
        lat, lon, alt = mav.get_lat_lon()
        assert lat is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_mavlink_sim_gps.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'sim_gps_lat'`

- [ ] **Step 4: Implement simulated GPS in mavlink_io.py**

Add `sim_gps_lat` and `sim_gps_lon` parameters to `__init__()`:

```python
        sim_gps_lat: float | None = None,
        sim_gps_lon: float | None = None,
```

Store them:
```python
        self._sim_gps_lat = sim_gps_lat
        self._sim_gps_lon = sim_gps_lon
        self._sim_gps_alt = 30.0  # default sim altitude in metres
        self._is_sim_gps = False
```

Add property after `gps_fix_ok`:

```python
    @property
    def is_sim_gps(self) -> bool:
        """True if currently using simulated GPS coordinates."""
        return self._is_sim_gps
```

Modify `get_lat_lon()` to add sim GPS fallback:

```python
    def get_lat_lon(self) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Return (lat, lon, alt) in decimal degrees / metres, or Nones."""
        with self._gps_lock:
            if self._gps["fix"] >= self._min_gps_fix and self._gps["lat"] is not None:
                self._is_sim_gps = False
                return (
                    self._gps["lat"] / 1e7,
                    self._gps["lon"] / 1e7,
                    self._gps["alt"] / 1000,
                )
        # Fallback to simulated GPS if configured
        if self._sim_gps_lat is not None and self._sim_gps_lon is not None:
            self._is_sim_gps = True
            return (self._sim_gps_lat, self._sim_gps_lon, self._sim_gps_alt)
        self._is_sim_gps = False
        return None, None, None
```

Modify `gps_fix_ok` property:

```python
    @property
    def gps_fix_ok(self) -> bool:
        with self._gps_lock:
            if self._gps["fix"] >= self._min_gps_fix:
                return True
        # Sim GPS counts as "ok"
        if self._sim_gps_lat is not None and self._sim_gps_lon is not None:
            return True
        return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_mavlink_sim_gps.py -v`
Expected: All PASS

Run: `python -m pytest tests/ -v` to verify no regressions.

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/mavlink_io.py config.ini tests/test_mavlink_sim_gps.py
git commit -m "feat: add simulated GPS fallback for indoor bench testing"
```

---

### Task 3: Pipeline Integration — Pass gps_required and sim GPS to Subsystems

**Files:**
- Modify: `hydra_detect/pipeline.py`

- [ ] **Step 1: Wire gps_required into _handle_rf_start()**

In `pipeline.py` `_handle_rf_start()`, read the config flag and pass it to `RFHuntController`:

After line 1012 (`kismet_manager=self._kismet_manager,`), add:

```python
            gps_required=self._cfg.getboolean("rf_homing", "gps_required", fallback=True),
```

- [ ] **Step 2: Wire sim GPS into MAVLinkIO construction**

Find where `MAVLinkIO` is constructed in `pipeline.py` `__init__()`. Add the sim GPS params:

```python
            sim_gps_lat=self._cfg.getfloat("mavlink", "sim_gps_lat", fallback=None) if self._cfg.get("mavlink", "sim_gps_lat", fallback="") else None,
            sim_gps_lon=self._cfg.getfloat("mavlink", "sim_gps_lon", fallback=None) if self._cfg.get("mavlink", "sim_gps_lon", fallback="") else None,
```

Note: `configparser.getfloat()` raises ValueError on empty string, so we check for empty first.

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add hydra_detect/pipeline.py
git commit -m "feat: wire gps_required and sim_gps config to subsystems"
```

---

### Task 4: Live Log File Persistence

**Files:**
- Modify: `hydra_detect/pipeline.py`
- Modify: `config.ini`
- Test: `tests/test_log_endpoint.py` (partial — file handler test)

- [ ] **Step 1: Add config flags to config.ini**

Add to `[logging]` section after `max_log_files = 20`:

```ini
app_log_file = true
app_log_level = INFO
```

- [ ] **Step 2: Write failing test for log file setup**

Add to `tests/test_log_endpoint.py` (new file):

```python
"""Tests for live log file persistence and API endpoint."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path


class TestLogFileSetup:
    def test_rotating_file_handler_creates_log(self):
        """Verify RotatingFileHandler writes to the expected path."""
        from logging.handlers import RotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "hydra.log"
            handler = RotatingFileHandler(
                str(log_path), maxBytes=5 * 1024 * 1024, backupCount=3,
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
            ))
            test_logger = logging.getLogger("test.log.setup")
            test_logger.addHandler(handler)
            test_logger.setLevel(logging.INFO)
            test_logger.info("Test log message")
            handler.flush()

            assert log_path.exists()
            content = log_path.read_text()
            assert "Test log message" in content
            assert "INFO" in content

            test_logger.removeHandler(handler)
            handler.close()
```

- [ ] **Step 3: Run test to verify it passes** (this tests stdlib, so it should pass immediately)

Run: `python -m pytest tests/test_log_endpoint.py::TestLogFileSetup -v`
Expected: PASS

- [ ] **Step 4: Add RotatingFileHandler to pipeline.py**

In `Pipeline.start()`, after the `logging.basicConfig(...)` call (line 378-381), add:

```python
        # Persistent log file for remote debugging access
        if self._cfg.getboolean("logging", "app_log_file", fallback=True):
            from logging.handlers import RotatingFileHandler
            log_dir = Path(self._cfg.get("logging", "log_dir", fallback="./output_data/logs"))
            log_dir.mkdir(parents=True, exist_ok=True)
            app_log_level = getattr(
                logging,
                self._cfg.get("logging", "app_log_level", fallback="INFO").upper(),
                logging.INFO,
            )
            file_handler = RotatingFileHandler(
                str(log_dir / "hydra.log"),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
            )
            file_handler.setLevel(app_log_level)
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
            ))
            logging.getLogger().addHandler(file_handler)
            logger.info("App log file enabled: %s", log_dir / "hydra.log")
```

Add `from pathlib import Path` to the imports if not already present.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/pipeline.py config.ini tests/test_log_endpoint.py
git commit -m "feat: add RotatingFileHandler for persistent app logs"
```

---

### Task 5: Log API Endpoint

**Files:**
- Modify: `hydra_detect/web/server.py`
- Test: `tests/test_log_endpoint.py`

- [ ] **Step 1: Write failing tests for log parsing and endpoint**

Add to `tests/test_log_endpoint.py`:

```python
import re


LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[(?P<module>[^\]]+)\] "
    r"(?P<level>\w+): "
    r"(?P<message>.*)$"
)

LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def parse_log_line(line: str) -> dict | None:
    """Parse a single log line into structured fields."""
    m = LOG_LINE_RE.match(line.strip())
    if not m:
        return None
    return {
        "timestamp": m.group("timestamp"),
        "level": m.group("level"),
        "module": m.group("module"),
        "message": m.group("message"),
    }


class TestLogParsing:
    def test_parse_info_line(self):
        line = "2026-03-19 14:23:01,123 [hydra_detect.pipeline] INFO: Pipeline started"
        result = parse_log_line(line)
        assert result is not None
        assert result["level"] == "INFO"
        assert result["module"] == "hydra_detect.pipeline"
        assert result["message"] == "Pipeline started"

    def test_parse_warning_line(self):
        line = "2026-03-19 14:23:02,456 [hydra_detect.rf.hunt] WARNING: Kismet connection lost"
        result = parse_log_line(line)
        assert result["level"] == "WARNING"

    def test_parse_garbage_returns_none(self):
        assert parse_log_line("not a log line") is None

    def test_level_filter(self):
        lines = [
            "2026-03-19 14:23:01,000 [mod] INFO: info msg",
            "2026-03-19 14:23:02,000 [mod] WARNING: warn msg",
            "2026-03-19 14:23:03,000 [mod] ERROR: error msg",
        ]
        min_level = "WARNING"
        min_ord = LEVEL_ORDER.get(min_level, 0)
        filtered = []
        for line in lines:
            parsed = parse_log_line(line)
            if parsed and LEVEL_ORDER.get(parsed["level"], 0) >= min_ord:
                filtered.append(parsed)
        assert len(filtered) == 2
        assert filtered[0]["level"] == "WARNING"
        assert filtered[1]["level"] == "ERROR"

    def test_tail_lines_limit(self):
        from collections import deque
        lines = [f"2026-03-19 14:23:{i:02d},000 [mod] INFO: msg {i}" for i in range(20)]
        tail = deque(lines, maxlen=5)
        assert len(tail) == 5
        assert "msg 19" in tail[-1]
```

- [ ] **Step 2: Run tests to verify they pass** (pure parsing logic)

Run: `python -m pytest tests/test_log_endpoint.py::TestLogParsing -v`
Expected: All PASS

- [ ] **Step 3: Implement GET /api/logs endpoint in server.py**

Add after the `/api/review/logs` endpoint (around line 840):

```python
@app.get("/api/logs")
async def api_app_logs(lines: int = 50, level: str = "INFO"):
    """Tail the application log file for remote debugging."""
    import re
    from collections import deque

    lines = max(1, min(lines, 500))
    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    min_ord = level_order.get(level.upper(), 1)

    log_re = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
        r"\[([^\]]+)\] "
        r"(\w+): "
        r"(.*)$"
    )

    # Find the log file
    cb = stream_state.get_callback("get_log_dir")
    log_dir = cb() if cb else "/data/logs"
    log_path = Path(log_dir) / "hydra.log"

    if not log_path.exists():
        return []

    result = deque(maxlen=lines)
    try:
        with open(log_path, "r") as f:
            for raw_line in f:
                m = log_re.match(raw_line.strip())
                if m:
                    entry_level = m.group(3)
                    if level_order.get(entry_level, 0) >= min_ord:
                        result.append({
                            "timestamp": m.group(1),
                            "level": entry_level,
                            "module": m.group(2),
                            "message": m.group(4),
                        })
                elif raw_line.strip():
                    result.append({
                        "timestamp": "",
                        "level": "RAW",
                        "module": "",
                        "message": raw_line.strip(),
                    })
    except OSError:
        return []

    return list(result)
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/server.py tests/test_log_endpoint.py
git commit -m "feat: add GET /api/logs endpoint for remote log access"
```

---

### Task 6: Kismet Connect-First Logic

**Files:**
- Modify: `hydra_detect/rf/kismet_manager.py`
- Modify: `config.ini`
- Test: `tests/test_rf_kismet_manager.py`

- [ ] **Step 1: Add config flag to config.ini**

Add to `[rf_homing]` section after `kismet_max_capture_mb`:

```ini
kismet_auto_spawn = true
```

- [ ] **Step 2: Write failing tests**

Add a new test class to `tests/test_rf_kismet_manager.py`:

```python
class TestConnectFirst:
    """start() should try HTTP connection before subprocess spawn."""

    @patch("hydra_detect.rf.kismet_manager.requests.get")
    def test_connect_first_adopts_existing(self, mock_get):
        """If Kismet is already running, adopt without checking shutil.which."""
        response = MagicMock()
        response.status_code = 200
        mock_get.return_value = response

        mgr = KismetManager(auto_spawn=False)
        assert mgr.start() is True
        assert mgr.we_own_process is False

    @patch("hydra_detect.rf.kismet_manager.requests.get",
           side_effect=Exception("refused"))
    def test_auto_spawn_false_does_not_spawn(self, mock_get):
        """auto_spawn=False should not attempt subprocess.Popen."""
        mgr = KismetManager(auto_spawn=False)
        result = mgr.start()
        assert result is False
        assert mgr.pid is None

    @patch("hydra_detect.rf.kismet_manager.requests.get",
           side_effect=Exception("refused"))
    @patch("hydra_detect.rf.kismet_manager.shutil.which", return_value=None)
    def test_auto_spawn_true_but_no_binary(self, mock_which, mock_get):
        """auto_spawn=True but binary missing should fail gracefully."""
        mgr = KismetManager(auto_spawn=True)
        result = mgr.start()
        assert result is False

    def test_auto_spawn_defaults_true(self):
        mgr = KismetManager()
        assert mgr._auto_spawn is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_rf_kismet_manager.py::TestConnectFirst -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'auto_spawn'`

- [ ] **Step 4: Implement connect-first logic in kismet_manager.py**

Add `auto_spawn` parameter to `__init__()`:

```python
        auto_spawn: bool = True,
```

Store it:
```python
        self._auto_spawn = auto_spawn
```

Replace the `start()` method with connect-first logic:

```python
    def start(self, timeout_sec: float = 15.0) -> bool:
        """Start Kismet or adopt an existing instance.

        Flow:
        1. Try HTTP connection — adopt if Kismet is already running
        2. If not running and auto_spawn enabled, spawn subprocess
        3. If auto_spawn disabled or binary missing, fail gracefully
        """
        # Step 1: Try to connect to existing Kismet
        if self._check_api():
            logger.info("Connected to existing Kismet at %s", self._host)
            self._we_own_process = False
            return True

        # Step 2: Check if we should try spawning
        if not self._auto_spawn:
            logger.error(
                "Kismet not reachable at %s and auto_spawn is disabled. "
                "Start Kismet on the host first (systemctl start kismet)",
                self._host,
            )
            return False

        if shutil.which("kismet") is None:
            logger.error(
                "Kismet not reachable and binary not found in PATH. "
                "Run hydra-setup.sh to install Kismet, or start it on the host."
            )
            return False

        # Step 3: Spawn subprocess (existing logic)
        os.makedirs(self._capture_dir, exist_ok=True)
        os.makedirs(self._log_dir, exist_ok=True)
        self._enforce_capture_limit()

        cmd = [
            "kismet",
            "-c", self._source,
            "--no-ncurses",
            "--log-prefix", os.path.abspath(self._capture_dir),
        ]

        log_path = os.path.join(self._log_dir, "kismet.log")
        try:
            self._log_file = open(log_path, "w")
        except OSError as exc:
            logger.error("Cannot open Kismet log file %s: %s", log_path, exc)
            return False

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError:
            logger.error("Kismet binary not found despite which() check")
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            return False
        except OSError as exc:
            logger.error("Failed to start Kismet: %s", exc)
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            return False

        self._we_own_process = True
        logger.info("Kismet spawned (PID %d), waiting for API...", self._process.pid)

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                logger.error(
                    "Kismet exited during startup (code %d). Check %s",
                    self._process.returncode, log_path,
                )
                self._cleanup_process()
                return False
            if self._check_api():
                logger.info("Kismet API ready at %s (PID %d)", self._host, self._process.pid)
                return True
            time.sleep(0.5)

        logger.error("Kismet API not ready after %.0fs — killing", timeout_sec)
        self.stop(timeout_sec=3.0)
        return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_rf_kismet_manager.py -v`
Expected: All PASS

Run: `python -m pytest tests/ -v` to verify no regressions.

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/rf/kismet_manager.py config.ini tests/test_rf_kismet_manager.py
git commit -m "feat: Kismet connect-first logic with auto_spawn config flag"
```

---

### Task 7: Kismet Host Systemd Service

**Files:**
- Create: `scripts/kismet.service`

- [ ] **Step 1: Create the service file**

Create `scripts/kismet.service`:

```ini
[Unit]
Description=Kismet Wireless Monitor (Hydra RF Hunt)
Before=hydra-detect.service
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/kismet -c rtl433-0 --no-ncurses
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Wire auto_spawn flag in pipeline.py**

In `pipeline.py` `_handle_rf_start()`, pass `auto_spawn` to `KismetManager`:

Find the `KismetManager(` constructor call (around line 977) and add:

```python
                auto_spawn=self._cfg.getboolean("rf_homing", "kismet_auto_spawn", fallback=True),
```

Also find the `KismetManager` construction in `__init__()` (if RF homing is enabled at startup) and add the same parameter.

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/kismet.service hydra_detect/pipeline.py
git commit -m "feat: add Kismet host systemd service and wire auto_spawn config"
```

---

### Task 8: Web UI Updates — Scan-Only Badge and No-GPS Signal Map

**Files:**
- Modify: `hydra_detect/web/static/js/operations.js`

- [ ] **Step 1: Add SCANNING to RF state labels and colors**

Find the `RF_STATE_COLORS` and `RF_STATE_LABELS` objects in `operations.js` and add:

```javascript
// In RF_STATE_COLORS:
scanning: 'scanning',

// In RF_STATE_LABELS:
scanning: 'SCAN ONLY',
```

Add CSS for the scanning badge color. In `operations.css` or inline:

The badge class `scanning` should use a distinct color. Add to the `updateRFPanel` function — after the converged color block:

```javascript
            if (state === 'scanning') {
                badge.style.background = '#1e3a5f';
                badge.style.color = '#93c5fd';
            }
```

- [ ] **Step 2: Update isActive check to include 'scanning'**

Change:
```javascript
const isActive = ['searching', 'homing', 'lost'].includes(state);
```
To:
```javascript
const isActive = ['searching', 'homing', 'lost', 'scanning'].includes(state);
```

- [ ] **Step 3: Update renderSignalMap to handle no-GPS data**

In the `renderSignalMap(data)` function, add a check at the top after getting the canvas context:

```javascript
        // Check if any data points have GPS coordinates
        var hasGps = data.some(function(d) { return d.lat != null && d.lon != null; });
        if (!hasGps) {
            ctx.fillStyle = '#555';
            ctx.font = '12px "Barlow Condensed", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('NO GPS \u2014 RSSI ONLY', w / 2, h / 2);
            return;
        }
```

- [ ] **Step 4: Add sim GPS indicator to Vehicle Telemetry panel**

In the `updateVehiclePanel()` function (or wherever GPS position is rendered), check for `is_sim_gps` in the telemetry data and show "(SIM)" suffix:

Find where `ctrl-gps-pos` is set and add:

```javascript
        var posText = /* existing position text */;
        if (telem.is_sim_gps) {
            posText += ' (SIM)';
        }
```

This requires adding `is_sim_gps` to the telemetry API response. In `pipeline.py`, where `get_telemetry()` result is built for the web, add:

```python
            "is_sim_gps": self._mavlink.is_sim_gps if self._mavlink else False,
```

- [ ] **Step 5: Test manually** (UI changes are visual — verify in browser)

Start the pipeline with `gps_required = false` in config.ini. Verify:
- RF panel shows "SCAN ONLY" blue badge
- Signal map shows "NO GPS — RSSI ONLY" when no GPS data present
- Sparkline still renders RSSI readings

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/web/static/js/operations.js hydra_detect/pipeline.py
git commit -m "feat: web UI scan-only badge, no-GPS signal map, sim GPS indicator"
```
