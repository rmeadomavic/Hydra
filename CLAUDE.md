# Hydra Detect v2.0 — Claude Code Guidelines

## Project Context

Hydra Detect is a **real-time object detection and tracking payload** for uncrewed
vehicles (drones, boats, rovers) running ArduPilot. It runs on **NVIDIA Jetson
Orin Nano** hardware and communicates with ground control stations via MAVLink.

This is safety-critical software with real-time and hardware constraints. Changes
must preserve deterministic timing, bounded memory usage, and fail-safe behavior.

The system serves three audiences: **demo** (leadership showcases), **ops**
(field use by instructor + students), and **dev** (building + extending).

### Architecture Overview

```
Camera → Detector (YOLO) → ByteTrack Tracker → MAVLink Alerts
                                                      → Web Dashboard (FastAPI + MJPEG)
                                                      → RTSP Stream (GStreamer)
                                                      → TAK/CoT Markers (multicast + unicast)
                                                      → Detection Logger (JSONL/CSV)
                                                      → FPV OSD (statustext / named_value / MSP)
                                                      → MAVLink Video (telemetry thumbnails)
                                                      → Event Timeline (actions + vehicle track)

Kismet (WiFi/SDR) → RF Hunt Controller → RSSI Gradient Ascent → MAVLink Nav
```

- **Entry point:** `hydra_detect/__main__.py`
- **Orchestrator:** `hydra_detect/pipeline.py` — the main detect→track→alert loop
- **Config:** `config.ini` (INI format, all tunables live here)
- **Config schema:** `hydra_detect/config_schema.py` — typed validation for every key
- **Tests:** `pytest` — run with `python -m pytest tests/`
- **Docs:** `docs/*.md` — structured documentation, one file per topic

### Module Index

| Module | Purpose |
|--------|---------|
| `pipeline.py` | Main loop: detect, track, alert, repeat |
| `camera.py` | Thread-safe capture (USB, RTSP, file, V4L2, analog) |
| `tracker.py` | ByteTrack wrapper (TrackedObject, TrackingResult) |
| `overlay.py` | Bounding boxes, HUD, target lock rendering |
| `approach.py` | Follow, Drop, Strike, Pixel-Lock approach modes |
| `guidance.py` | Velocity-based visual servoing controller (pure math, extractable) |
| `autonomous.py` | Geofenced autonomous strike controller |
| `dogleg_rtl.py` | Tactical return path (drones) |
| `mission_profiles.py` | RECON / DELIVERY / STRIKE presets |
| `mavlink_io.py` | MAVLink connection, alerts, vehicle commands |
| `mavlink_video.py` | Detection thumbnails over telemetry radio |
| `geo_tracking.py` | CAMERA_TRACKING_GEO_STATUS for GCS map |
| `osd.py` | FPV OSD (statustext, named_value, msp_displayport) |
| `msp_displayport.py` | MSP v1 DisplayPort protocol for HDZero VTX |
| `detection_logger.py` | CSV/JSONL logging with background writer |
| `event_logger.py` | Mission event timeline (actions + vehicle track at 1 Hz) |
| `verify_log.py` | SHA-256 hash chain verification |
| `review_export.py` | Standalone HTML map report generator |
| `rtsp_server.py` | GStreamer RTSP H.264 output |
| `servo_tracker.py` | Pixel-lock servo controller (pan + strike) |
| `model_manifest.py` | Model hash verification and manifest |
| `config_schema.py` | Typed config validation with error messages |
| `system.py` | Jetson stats, power modes, model listing |
| `tls.py` | Self-signed TLS certificate generation |
| `profiles.py` | JSON mission profile loading |
| `detectors/base.py` | Abstract detector interface |
| `detectors/yolo_detector.py` | YOLOv8/v11 via ultralytics |
| `rf/hunt.py` | RF hunt state machine |
| `rf/kismet_client.py` | Kismet REST API client |
| `rf/kismet_manager.py` | Kismet subprocess manager |
| `rf/navigator.py` | Gradient ascent waypoint navigation |
| `rf/search.py` | Lawnmower and spiral pattern generators |
| `rf/signal.py` | RSSI filtering and gradient analysis |
| `tak/tak_output.py` | CoT multicast/unicast output thread |
| `tak/tak_input.py` | CoT command listener (GeoChat + custom types) |
| `tak/cot_builder.py` | Cursor-on-Target XML builder |
| `tak/type_mapping.py` | YOLO class to MIL-STD-2525 mapping |
| `web/server.py` | FastAPI REST API + MJPEG stream + HTML pages |
| `web/config_api.py` | Config read/write with file locking and safety |

## SORCC Course Context

**SORCC** (Special Operations Robotics Capabilities Course) is a 6-week IQT
program at a military training facility. 15 students in 5 teams of 3,
active-duty SOF operators. They are technically capable and mission-focused
but most have never touched a Jetson, config file, or terminal before this course.

### Platforms Per Team
- 5" and 10" FPV quadcopters (ArduCopter)
- Heewing T1 Ranger fixed wing (ArduPlane)
- Traxxas Stampede UGV (ArduRover)
- Bonzai Enforcer 48" USV (ArduRover boat mode)

### How Students Use Hydra (Weeks 4-5)
1. Receive a pre-configured Jetson Orin Nano
2. Power on — Hydra auto-starts
3. Open web dashboard on laptop/tablet
4. Select mission profile (RECON/DELIVERY/STRIKE)
5. Fly/drive/sail while Hydra detects and tracks targets
6. Review detection logs after the sortie

### Design Implications
- **Students never need SSH** — dashboard is the only interface
- **Config.ini is the user interface** — all student-facing options live there with sane defaults
- **Errors must be plain English** — "Camera: not found on /dev/video0 — check USB"
  not Python tracebacks
- **Field conditions:** battery power, 50-100m WiFi, vibration, water, night ops
- **3 instructors** (lead/dev, platform SME, docs)
- **20 potential Hydra instances** during CULEX (5 teams × 4 platforms)

### Vocabulary
- SORCC = "sork" (spoken as a word)
- IQT = Initial Qualification Training
- STX = Situational Training Exercise
- CULEX = Culminating Exercise
- EENT = End of Evening Nautical Twilight (night ops begin)
- Use "uncrewed" not "unmanned"

## Jetson Deployment Constraints

### Memory (4–8 GB shared CPU/GPU RAM)
- Never load multiple large models simultaneously
- Prefer in-place numpy/OpenCV operations over copies
- Avoid unbounded caches or queues — always use fixed-size ring buffers
- Profile with `tegrastats` or `jtop` before and after changes

### CUDA / TensorRT
- Model inference must stay on GPU; avoid unnecessary `.cpu()` or `.numpy()` calls
  that trigger device-to-host transfers
- Do not add `torch.cuda.synchronize()` in hot paths — it kills throughput
- Base Docker image: `dustynv/l4t-pytorch:r36.4.0` (CUDA OpenCV, PyTorch, TensorRT)

### GPIO / Serial / Peripherals
- MAVLink connects via serial (`/dev/ttyACM0`) or UDP — never assume a specific
  device path; use `config.ini` settings
- Camera source can be USB (`/dev/video0`), RTSP, GStreamer, or file — the
  `camera.py` module handles all of these
- The systemd service (`scripts/hydra-detect.service`) manages device access and
  Docker bind mounts

### Real-Time Requirements
- The main detection loop in `pipeline.py` must sustain ≥5 FPS on Jetson
- Do not add blocking I/O (network calls, disk sync) in the hot loop
- Background threads handle MAVLink heartbeats and GPS polling — these must not
  starve the detection thread
- Use `threading.Lock` (not `asyncio`) for shared state — the pipeline is
  thread-based, not async

## Code Review Process

Follow the **discover → review → fix** workflow:

### 1. Discover
- Identify all files affected by the change
- Map dependencies: which modules import what you're changing?
- Check `config.ini` for related configuration knobs
- Run `python -m pytest tests/` to establish a green baseline

### 2. Review
- **Thread safety:** Any shared state accessed from multiple threads? Uses Lock?
- **Memory:** Does this allocate in the hot loop? Bounded collections?
- **Fail-safe:** What happens if this component crashes? Does the vehicle stay safe?
- **Input validation:** Web endpoints validate all user input? Lengths bounded?
- **Hardware:** Will this work on Jetson (ARM64, limited RAM, CUDA)?

### 3. Fix
- Make minimal, focused changes — one concern per commit
- Run `python -m pytest tests/` after every change
- Run `flake8 hydra_detect/ tests/` for style
- Run `mypy hydra_detect/` for type errors
- Test with a real camera source if touching `camera.py` or `pipeline.py`

## Coding Standards

- **Python 3.10+** — use modern type hints (`X | None`, not `Optional[X]`)
- All modules use `from __future__ import annotations`
- Use dataclasses for data containers (see `detectors/base.py`, `tracker.py`)
- Prefer composition over inheritance
- Keep the detector interface (`detectors/base.py`) stable — new detectors
  implement `BaseDetector`
- Web endpoints in `web/server.py` require bearer token auth for control actions
- Never commit secrets or API tokens — use `config.ini` (gitignored values)

## Config Sections

All tunables live in `config.ini`. Sections: `[camera]`, `[detector]`, `[tracker]`,
`[mavlink]`, `[alerts]`, `[web]`, `[osd]`, `[autonomous]`, `[approach]`, `[drop]`,
`[rf_homing]`, `[servo_tracking]`, `[logging]`, `[watchdog]`, `[rtsp]`,
`[mavlink_video]`, `[guidance]`, `[tak]`, `[vehicle.drone]`, `[vehicle.usv]`, `[vehicle.ugv]`,
`[vehicle.fw]`.

Full reference: `docs/configuration.md`. Schema: `hydra_detect/config_schema.py`.

## API Endpoints (Summary)

70+ endpoints in `web/server.py`. Key groups:

- **Health**: `GET /api/health`, `GET /api/preflight`
- **Stream**: `GET /stream.mjpeg`, `GET/POST /api/stream/quality`
- **Stats/Tracks**: `GET /api/stats`, `GET /api/tracks`, `GET /api/detections`
- **Target**: `GET /api/target`, `POST /api/target/lock`, `POST /api/target/unlock`, `POST /api/target/strike`
- **Approach**: `GET /api/approach/status`, `POST /api/approach/follow/{id}`, `POST /api/approach/drop/{id}`, `POST /api/approach/strike/{id}`, `POST /api/approach/pixel_lock/{id}`, `POST /api/approach/abort`
- **Vehicle**: `POST /api/vehicle/loiter`, `POST /api/vehicle/mode`, `POST /api/abort` (unauthenticated)
- **Config**: `GET/POST /api/config`, `GET/POST /api/config/full`, `POST /api/config/prompts`, `POST /api/config/threshold`, `GET/POST /api/config/alert-classes`, `POST /api/config/restore-backup`, `POST /api/config/factory-reset`, `GET /api/config/export`, `POST /api/config/import`
- **Camera/Models**: `GET /api/camera/sources`, `POST /api/camera/switch`, `GET /api/models`, `POST /api/models/switch`
- **Profiles**: `GET /api/profiles`, `POST /api/profiles/switch`, `GET /api/mission-profiles`
- **TAK**: `GET /api/tak/status`, `POST /api/tak/toggle`, `GET/POST/DELETE /api/tak/targets`
- **RF**: `GET /api/rf/status`, `GET /api/rf/rssi_history`, `POST /api/rf/start`, `POST /api/rf/stop`
- **RTSP**: `GET /api/rtsp/status`, `POST /api/rtsp/toggle`
- **MAVLink Video**: `GET /api/mavlink-video/status`, `POST /api/mavlink-video/toggle`, `POST /api/mavlink-video/tune`
- **Events/Mission**: `GET /api/events`, `GET /api/events/status`, `POST /api/mission/start`, `POST /api/mission/end`
- **Review**: `GET /api/review/logs`, `GET /api/review/log/{file}`, `GET /api/review/events/{file}`, `GET /api/review/images/{file}`, `GET /api/export`
- **System**: `GET /api/system/power-modes`, `POST /api/system/power-mode`, `GET /api/logs`, `POST /api/restart`, `POST /api/pipeline/stop`, `POST /api/pipeline/pause`
- **Setup**: `GET /api/setup/devices`, `POST /api/setup/save`
- **Pages**: `GET /` (dashboard), `GET /control`, `GET /instructor`, `GET /review`, `GET /setup`

Full reference: `docs/api-reference.md`.

## Test Files

50+ test files in `tests/`. Key coverage areas:
- Autonomous controller: `test_autonomous.py`, `test_drop_strike.py`
- Guidance: `test_guidance.py`
- Config: `test_config_schema.py`, `test_config_api.py`, `test_config_freeze.py`
- RF: `test_rf_hunt.py`, `test_rf_navigator.py`, `test_rf_search.py`, `test_rf_signal.py`, `test_rf_geofence.py`, `test_rf_kismet.py`, `test_rf_web_api.py`
- TAK: `test_tak.py`, `test_tak_input.py`, `test_tak_security.py`, `test_tak_unicast_manifest.py`
- Web: `test_web_api.py`, `test_preflight_ui.py`, `test_instructor_ops.py`, `test_dashboard_resilience.py`
- Safety: `test_safety_hardening.py`, `test_mavlink_safety.py`, `test_camera_loss.py`, `test_chain_of_custody.py`
- Pipeline: `test_pipeline_callbacks.py`, `test_sitl_mode.py`, `test_vehicle_config.py`

## Common Commands

```bash
# Run the application
python -m hydra_detect --config config.ini

# Run tests
python -m pytest tests/ -v

# Lint
flake8 hydra_detect/ tests/

# Type check
mypy hydra_detect/

# Build Docker image (on Jetson)
docker build --no-cache -t hydra-detect .

# Monitor Jetson resources
tegrastats
```

## Web Frontend (SPA)

- **SPA structure:** `base.html` includes `operations.html` + `settings.html` via
  Jinja2 `{% include %}`. Both views always exist in DOM, shown/hidden via CSS.
  `review.html` is standalone — does not share `base.html`, `app.js`, or CSS.
- **CSS transitions + display:** Cannot transition `opacity` in the same class toggle
  that changes `display: none` → `display: flex`. Use JS to set `display` first,
  force reflow (`void el.offsetWidth`), then add class for opacity transition.
- **Event listeners in settings:** `HydraSettings.onEnter()` fires on every view
  switch. Use event delegation (`document.addEventListener`) for features that
  should only bind once — avoid stacking duplicate listeners.
- **Toast types:** `showToast(msg, type)` supports `error` (default/red),
  `info` (blue), `success` (green). CSS classes are in `base.css`.
- **YouTube embeds:** Use `youtube-nocookie.com` domain for reliability.

## Hardware Environment

- **Architecture:** Jetson Orin Nano is ARM64/aarch64 — always check architecture
  compatibility before suggesting packages or tools
- **Packages:** Snap packages have known kernel compatibility issues on Jetson —
  prefer `apt` or `pip` installs when possible
- **Permissions:** Use udev rules for persistent `/dev/tty*` permissions, never
  `chmod` (resets on replug/reboot)
- **Models:** ML models belong in the `models/` directory, not the project root —
  always verify download destinations match what the code expects

## Serial / MAVLink Conventions

- `SERIAL5` = TELEM3 on this Pixhawk 6C setup
- HDZero DisplayPort protocol = **42** (not 33)
- ArduPilot does **NOT** support `ENCAPSULATED_DATA` messages
- Always verify serial port mappings against the `/hydra` skill or
  `docs/pixhawk-setup.md` (if it exists) before changing ArduPilot parameters
- **Lua OSD script (`hydra_osd.lua`):** The script on the Pixhawk SD card
  (`APM/scripts/hydra_osd.lua`) sends STATUSTEXT at 5 Hz, flooding the GCS log.
  To disable: set `SCR_ENABLE = 0` in ArduPilot params, or delete the script
  from the SD card. Only used when OSD mode is `named_value` (Lua-based).

## Live Logs API

Hydra exposes `GET /api/logs?lines=N&level=LEVEL` which tails
`output_data/logs/hydra.log` — a RotatingFileHandler capturing all Python
logging output (5 MB rotation, 3 backups). Use `/jetson-logs` skill or:

```bash
curl -s 'http://<JETSON_IP>:8080/api/logs?lines=100&level=WARNING'
```

Use this proactively when diagnosing runtime issues on the Jetson — it provides
real-time context that `journalctl` or Docker logs cannot (structured, filtered,
and accessible without SSH).

## Adding New Approach Modes

Pattern for adding a new engagement mode (e.g. pixel_lock, follow, drop, strike):
1. Add enum value to `ApproachMode` in `approach.py`
2. Add `start_*()` and `_update_*()` methods to `ApproachController`
3. Add `_handle_*_command()` to `pipeline.py`, register in `set_callbacks()`
4. Add `POST /api/approach/*` endpoint in `web/server.py`
5. Add config section to `config.ini` + schema in `config_schema.py`
6. Keep vehicle control logic separate from math — see `guidance.py` pattern

## Debugging Rules

- When facing unfamiliar system issues (snap, kernel modules, hardware protocols):
  **research first, fix second**
- Search project docs, git history, and reference materials before attempting any fix
- If your first two approaches fail, **STOP and ask the user** — they likely know
  the answer or can point to docs
- When spawning external processes (rtl_power, Kismet, etc.), always implement
  proper cleanup with `try/finally` or `atexit` handlers to prevent orphaned processes
- Before spawning a subprocess, check for existing instances (`pgrep`, `fuser`)
  to avoid dual-instance problems
