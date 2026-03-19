# Kismet Managed Subprocess Integration

**Date:** 2026-03-18
**Status:** Approved
**Scope:** Auto-start Kismet as a managed subprocess when rf_homing is enabled

## Problem

Kismet is installed on the Jetson (via `hydra-setup.sh`) and Hydra has a full RF
homing engine (`rf/hunt.py`, `rf/kismet_client.py`, etc.) with 89 passing tests.
But nothing actually **starts** Kismet. The pipeline creates a `KismetClient`,
calls `check_connection()`, gets False, logs a warning, and silently skips RF
homing. The user has never seen it "do" anything.

Additionally, 12 `.kismet` capture files (~17MB) are accumulating in the repo
root with no gitignore.

## Solution

Add a `KismetManager` class that owns the Kismet process lifecycle as a managed
subprocess. When `rf_homing.enabled = true`, the pipeline starts Kismet
automatically, monitors its health, and stops it on shutdown.

## Design

### KismetManager (`hydra_detect/rf/kismet_manager.py`)

```python
class KismetManager:
    def __init__(self, *, source, capture_dir, host, log_dir):
        ...

    def start(self, timeout_sec=15.0) -> bool:
        """Spawn Kismet subprocess, wait for REST API to respond."""

    def stop(self, timeout_sec=5.0) -> None:
        """SIGTERM, wait, SIGKILL fallback."""

    def is_healthy(self) -> bool:
        """Check process alive + REST API responds."""

    def restart(self, stop_event: threading.Event | None = None) -> bool:
        """Stop + start. Checks stop_event between phases."""

    @property
    def pid(self) -> int | None: ...

    @property
    def we_own_process(self) -> bool:
        """True if we spawned Kismet, False if we adopted a pre-existing one."""
```

**Pre-existing process detection:** Before spawning, `start()` hits
`GET /system/status.json` on the configured host. If Kismet is already running:
- Adopt the existing process (set `we_own_process = False`)
- Log an info message: "Kismet already running, adopting existing instance"
- `stop()` will NOT kill a process we didn't start — only detaches

If no existing Kismet is found, spawn a new one (`we_own_process = True`).

**Kismet binary detection:** `start()` uses `shutil.which("kismet")` before
attempting `Popen`. If not found, catches `FileNotFoundError`, logs a clear
error, and returns `False`.

**Subprocess command:**
```
kismet -c {source} --no-ncurses --override log_prefix={capture_dir}/Kismet --daemonize false
```

- `--daemonize false` — Hydra owns the process, not init
- `--override log_prefix=` — directs `.kismet` capture files to the configured
  directory (Kismet's `--log-prefix` only sets the name prefix; `--override`
  sets the full path prefix)
- `--no-ncurses` — no TUI (headless)
- stdout/stderr captured to `{log_dir}/kismet.log` (opened in `'w'` truncate
  mode on each `start()` call to prevent unbounded growth across restarts)

**Directory creation:** `start()` calls `os.makedirs(capture_dir, exist_ok=True)`
and `os.makedirs(log_dir, exist_ok=True)` before spawning.

**Health check:** `GET /system/status.json` on the configured host. Called from
the hunt loop's existing poll cycle (every 0.5s), not a separate thread.

**Restart policy:** If Kismet dies during a hunt, restart once and
re-authenticate the `KismetClient`. If restart fails, abort the hunt (fail-safe).

**Restart and cooperative cancellation:** `restart()` accepts an optional
`stop_event: threading.Event`. Between `stop()` and `start()`, it checks
`stop_event.is_set()` — if the hunt is being cancelled, it bails out
immediately instead of blocking for up to 20 seconds. The hunt controller
passes its `_stop_evt` when calling restart.

### Pipeline integration (`hydra_detect/pipeline.py`)

Current flow:
```
if rf_homing.enabled:
    KismetClient(host, user, pass) → check_connection() → if fail, skip
```

New flow:
```
if rf_homing.enabled:
    KismetManager(source, capture_dir, host, log_dir) → start(timeout=15s)
    → if start fails: log error, continue without RF homing
    KismetClient(host, user, pass) → proceeds as before
    RFHuntController gets reference to KismetManager for mid-hunt restarts

on shutdown:
    RFHuntController.stop() → KismetManager.stop()
```

- `KismetManager` is stored as `self._kismet_manager` on the pipeline
- `KismetManager.start()` blocks until the REST API responds (polls every 0.5s,
  up to 15s timeout)
- If Kismet fails to start (no SDR dongle, permission error, not installed),
  logs a clear error and continues without RF homing — does not crash the
  detection pipeline
- Existing `KismetClient` code is unchanged
- Both the pipeline init and `_handle_rf_start` (web-initiated hunts) use the
  same `self._kismet_manager` instance — the manager is owned by the pipeline,
  not by individual hunt controllers
- `host` parameter: both `KismetManager` and `KismetClient` read from the same
  `kismet_host` config value to ensure they target the same Kismet instance

### RFHuntController changes (`hydra_detect/rf/hunt.py`)

- Accept optional `kismet_manager: KismetManager | None` parameter
- In `_poll_rssi()`: if `KismetClient` returns connection error and manager
  exists, call `manager.restart(stop_event=self._stop_evt)` then retry once
- `_handle_rf_start` in pipeline passes `self._kismet_manager` to each new
  `RFHuntController` — ensures web-initiated hunts also get restart capability
- No other changes to the state machine

### Config changes (`config.ini`)

New fields in `[rf_homing]`:
```ini
kismet_source = rtl433-0
kismet_capture_dir = ./output_data/kismet
```

All existing fields unchanged.

### Gitignore additions

```
*.kismet
*.kismet-journal
output_data/kismet/
```

### Cleanup

- Delete or move the 12 `.kismet` files in repo root (test artifacts, ~17MB,
  owned by root)

## Files to create

| File | Purpose |
|------|---------|
| `hydra_detect/rf/kismet_manager.py` | KismetManager class (~150 lines) |
| `tests/test_rf_kismet_manager.py` | Unit tests for manager |

## Files to modify

| File | Change |
|------|--------|
| `hydra_detect/pipeline.py` | Create KismetManager before KismetClient; pass to RFHuntController; pass to `_handle_rf_start`; stop on shutdown |
| `hydra_detect/rf/hunt.py` | Accept KismetManager, restart on connection loss with stop_event |
| `hydra_detect/rf/__init__.py` | Export KismetManager |
| `config.ini` | Add `kismet_source`, `kismet_capture_dir` |
| `.gitignore` | Add `*.kismet`, `*.kismet-journal`, `output_data/kismet/` |

## Testing

### Unit tests (`tests/test_rf_kismet_manager.py`)

- Manager starts subprocess with correct args (mock `subprocess.Popen`)
- Manager uses `--override log_prefix=` with correct capture dir
- Manager creates capture_dir and log_dir if absent
- Health check returns False when process is dead
- Restart logic works (stop + start)
- Restart bails out if stop_event is set between phases
- Stop sends SIGTERM, then SIGKILL after timeout
- Stop does NOT kill process when `we_own_process = False`
- Manager handles "Kismet not installed" gracefully (`shutil.which` returns None)
- Manager adopts pre-existing Kismet instance
- Log file opened in truncate mode on each start

### Existing test updates

- `test_rf_hunt.py` — hunt controller accepts KismetManager, calls restart on
  connection loss, passes stop_event
- `test_rf_web_api.py` — verify web-initiated hunt receives KismetManager
- Pipeline init — verify KismetManager.start() called before KismetClient

### Manual field test (Crossfire 915 MHz)

1. Plug in RTL-SDR dongle
2. Set `rf_homing.enabled = true`, `mode = sdr`, `target_freq_mhz = 915.0`
3. Start Hydra — verify Kismet auto-starts (`ps aux | grep kismet`)
4. Power on Crossfire TX
5. Check `/api/rf/status` — should show signal detected, RSSI readings
6. Stop Hydra — verify Kismet process is gone

## Out of scope

- Web UI panel for RF hunt (separate task)
- Elasticsearch indexing of RF samples (future, per AI-WIDS patterns)
- WiFi monitor-mode source support (future)
- Multi-SDR dongle support (future)
