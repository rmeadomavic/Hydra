# Operational Resilience — GPS-less Mode, Live Logs, Kismet Docker Fix

## Problem

Three issues block field and indoor usability:

1. **RF Hunt fails indoors** — `hunt.py` hard-aborts at line 208 if GPS fix < 3. No way to demo RSSI scanning without a GPS fix.
2. **No log access for debugging** — Application logs go to Docker stdout/journal only. Claude Code cannot query them during sessions, making remote debugging blind.
3. **Kismet subprocess fails in Docker** — `KismetManager.start()` tries `subprocess.Popen("kismet ...")` inside the container where the binary doesn't exist. The adopt-existing-process logic runs second, so auto-start always fails in Docker.

## Feature 1: GPS-less RF Hunt

### Scan-Only Mode

New config flag in `[rf_homing]`:
```ini
gps_required = true    ; set false for indoor/bench demos
```

When `gps_required = false`:
- `RFHuntController` skips waypoint generation and vehicle navigation
- RSSI polling loop runs continuously via Kismet/SDR
- RSSI history ring buffer records entries with `lat=None, lon=None`
- State machine: IDLE → SCANNING (no SEARCHING/HOMING/CONVERGED states)
- Web UI shows "SCAN ONLY" badge on the RF panel
- Sparkline renders normally (x-axis is time, not position)
- Signal map canvas shows "NO GPS" message instead of scatter plot

**hunt.py changes:**
- Add `self._gps_required` flag from config in `__init__`
- New `_do_scan()` method: poll RSSI, record to history, no navigation
- `_run_loop()`: if not `_gps_required`, call `_do_scan()` instead of `_do_search()`/`_do_homing()`
- Skip the GPS fix check at line 208 when `gps_required = false`

**operations.js changes:**
- RF panel: show "SCAN ONLY" pill when status reports `gps_required: false`
- Signal map: render "NO GPS — RSSI ONLY" text when no lat/lon in history data

### Simulated GPS

New config flags in `[mavlink]`:
```ini
sim_gps_lat =          ; leave empty to disable
sim_gps_lon =
```

When both are set to valid coordinates:
- `mavlink_io.get_lat_lon()` returns `(sim_lat, sim_lon, 30.0)` with fix=3 when real GPS is unavailable
- `mavlink_io.gps_fix_ok` returns True
- Full RF hunt pipeline exercises (waypoints generated, vehicle doesn't move)
- Web UI shows GPS position as the simulated coords with a "(SIM)" indicator
- Simulated GPS is a fallback — real GPS data takes priority when available

**mavlink_io.py changes:**
- Read `sim_gps_lat`/`sim_gps_lon` from config in `__init__`
- In `get_lat_lon()`: if real fix < `min_gps_fix` AND sim coords configured, return sim coords
- In `gps_fix_ok`: same fallback logic
- Add `is_sim_gps` property so UI can show the indicator

## Feature 2: Live Log Access

### Log File Persistence

Add a `RotatingFileHandler` to the Python root logger in `pipeline.py`:

```python
file_handler = RotatingFileHandler(
    log_dir / "hydra.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB
    backupCount=3,
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
))
logging.getLogger().addHandler(file_handler)
```

- Output: `output_data/logs/hydra.log` (+ `.1`, `.2`, `.3` backups)
- Already bind-mounted to host via `-v output_data:/data`
- Accessible via SSH: `cat ~/Hydra/output_data/logs/hydra.log`
- Same format as existing stdout output

**config.ini addition:**
```ini
[logging]
app_log_file = true        ; enable/disable file logging
app_log_level = INFO       ; minimum level for file output
```

### API Endpoint

New endpoint in `server.py`:

```
GET /api/logs?lines=100&level=INFO
```

**Behavior:**
- Reads the last N lines from `hydra.log` (default 50, max 500)
- Optional `level` filter: only return lines at or above the specified level
- Returns JSON array:
  ```json
  [
    {"timestamp": "2026-03-19 14:23:01", "level": "WARNING", "module": "rf.hunt", "message": "Kismet connection lost, restarting..."},
    ...
  ]
  ```
- No auth required (read-only, same as `/api/status`)
- Parses log lines using the known format string; unparseable lines returned with `level: "RAW"`

**server.py changes:**
- Add `api_logs()` endpoint after existing review endpoints
- Read log file path from config API's `get_config_path()` parent / `log_dir`
- Use `collections.deque(maxlen=lines)` to efficiently tail the file

## Feature 3: Kismet Docker Architecture

### Smart Connect-First in KismetManager

Reorder `KismetManager.start()` to try HTTP connection before subprocess spawn:

```
start() flow:
1. Try check_connection() to kismet_host
2. If connected → adopt (we_own_process = False), return True
3. If not connected → check config: kismet_auto_spawn
4. If auto_spawn = true AND shutil.which("kismet") → spawn subprocess (current behavior)
5. If auto_spawn = false OR binary missing → log error, return False
```

**New config flag:**
```ini
[rf_homing]
kismet_auto_spawn = true   ; set false for Docker deployments where Kismet runs on host
```

**kismet_manager.py changes:**
- Read `kismet_auto_spawn` from config in `__init__`
- Reorder `start()`: connection check first, subprocess second
- Clear log messages: "Connected to existing Kismet at {host}" vs "Spawning Kismet subprocess" vs "Kismet not reachable and auto_spawn disabled"

### Host Systemd Service

New file: `scripts/kismet.service`

```ini
[Unit]
Description=Kismet Wireless Monitor
Before=hydra-detect.service
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/kismet -c rtl433-0 --no-ncurses
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**hydra-setup.sh changes:**
- Copy `kismet.service` to `/etc/systemd/system/`
- `systemctl enable kismet`
- Set `kismet_auto_spawn = false` in config.ini for Docker deployments

**deploy-jetson skill update:**
- Add `systemctl is-active kismet` to validation checks

## Files Changed

| File | Change |
|------|--------|
| `hydra_detect/rf/hunt.py` | Scan-only mode, `gps_required` flag, `_do_scan()` method |
| `hydra_detect/mavlink_io.py` | Simulated GPS fallback, `is_sim_gps` property |
| `hydra_detect/pipeline.py` | RotatingFileHandler setup, pass `gps_required` to hunt controller |
| `hydra_detect/web/server.py` | `GET /api/logs` endpoint |
| `hydra_detect/web/static/js/operations.js` | "SCAN ONLY" badge, signal map no-GPS state |
| `hydra_detect/rf/kismet_manager.py` | Connect-first logic, `kismet_auto_spawn` flag |
| `scripts/kismet.service` | New — host Kismet systemd unit |
| `scripts/hydra-setup.sh` | Install kismet.service, set config defaults |
| `config.ini` | New flags: `gps_required`, `sim_gps_lat/lon`, `app_log_file`, `kismet_auto_spawn` |
| `tests/` | Tests for scan-only mode, sim GPS, log endpoint, connect-first logic |

## Testing Strategy

- **Scan-only mode**: Unit test `_do_scan()` with mocked Kismet client returning RSSI values. Verify no GPS calls made, history recorded without coords.
- **Simulated GPS**: Unit test `mavlink_io.get_lat_lon()` returns sim coords when real fix unavailable. Verify real GPS takes priority.
- **Log endpoint**: Unit test parses log file, respects line limit and level filter. Integration test with actual RotatingFileHandler.
- **Connect-first Kismet**: Unit test `start()` with mocked HTTP connection — verify subprocess not spawned when connection succeeds. Verify `auto_spawn=false` prevents spawn attempt.
- **Indoor demo**: Manual test — start RF hunt indoors with `gps_required=false`, verify sparkline shows RSSI data, no abort errors.
