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
| `waypoint_export.py` | QGC WPL 110 waypoint export for detection-driven missions |
| `rtsp_server.py` | GStreamer RTSP H.264 output |
| `servo_tracker.py` | Pixel-lock servo controller (pan + strike) |
| `model_manifest.py` | Model hash verification, manifest, class introspection |
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
| `rf/rssi_protocol.py` | Protocol defining the RSSI client interface for RF hunt |
| `rf/rtl_power_client.py` | RTL-SDR raw power measurement client for FHSS radios |
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
- **Config fallbacks must match schema defaults** — when adding a new config key,
  ensure the `fallback=` in `pipeline.py` matches the `default=` in
  `config_schema.py`. Mismatches bypass schema validation.

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

## Codebase Stats (as of April 2026)

| Metric | Count |
|--------|-------|
| App LOC (`hydra_detect/`) | ~15,800 |
| Test LOC (`tests/`) | ~12,700 |
| Test files | 55 |
| App modules | 50 |
| Merged PRs | 58 |

**CODE_REVIEW_TRACKER.md** exists on the `check-branches-pr-status-Pmeg8` branch
(not yet merged to main). It contains a risk-ordered review plan with 10 chunks
populated, none reviewed yet.

**Orphan branches:** The three known orphans (`claude/compare-config-frameworks-3FuSP`,
`claude/implement-todo-item-KrrRT`, `feat/follow-mode-approach-controller`) have
been identified and are queued for deletion from the remote.

> **Process gap:** Zero human code reviews across 58 merged PRs. Codex
> auto-reviews ~7% of PRs via org-level GitHub App.

## Test Files

55 test files in `tests/`. Key coverage areas:
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
python -m pytest tests/ -v     # no --timeout flag (pytest-timeout not installed)

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

- **SPA structure:** `base.html` includes `ops.html` + `config.html` + `settings.html` via
  Jinja2 `{% include %}`. All three views always exist in DOM, shown/hidden via CSS.
  `review.html` is standalone — does not share `base.html`, `app.js`, or CSS.
- **3-page layout:** Ops (HUD with clickable bounding boxes), Config (mission
  tuning with video + panels), Settings (backend config with sliders/dropdowns).
  Route via hash: `#ops`, `#config`, `#settings`. Default: `#ops`.
  `#operations` is a backward-compatible alias for `#config`.
- **JS modules:** `ops.js` (HUD + canvas overlay), `config.js` (panels, evolved
  from operations.js), `settings.js` (schema-driven form), `app.js` (router +
  polling). Each has `onEnter()`/`onLeave()` lifecycle hooks.
- **Canvas bounding box overlay:** `ops.js` draws track bounding boxes on a
  `<canvas>` over the video `<img>`. Click hit-testing maps display coords to
  frame space (letterbox-aware). Context menu appears at click position with
  Follow/Strike/Drop/P-Lock/Loiter/Lock actions.
- **Schema-driven settings:** `GET /api/config/schema` exposes field metadata
  (type, min, max, choices, default). Settings.js auto-generates sliders for
  numeric ranges and dropdowns for enums.
- **CSS transitions + display:** Cannot transition `opacity` in the same class toggle
  that changes `display: none` → `display: flex`. Use JS to set `display` first,
  force reflow (`void el.offsetWidth`), then add class for opacity transition.
- **Event listeners in settings:** `HydraSettings.onEnter()` fires on every view
  switch. Use event delegation (`document.addEventListener`) for features that
  should only bind once — avoid stacking duplicate listeners.
- **Toast types:** `showToast(msg, type)` supports `error` (default/red),
  `info` (blue), `success` (green). CSS classes are in `base.css`.
- **YouTube embeds:** Use `youtube-nocookie.com` domain for reliability.

### Video Stream Architecture

The dashboard video uses **snapshot polling** (`GET /stream.jpg`) instead of MJPEG
multipart streaming. Each request returns a single JPEG frame as a regular
`Response`. The JS polls by setting `img.src = '/stream.jpg?t=<timestamp>'` on
each `load` event (~30 fps cap via 33ms `setTimeout`).

**Why not MJPEG?** Starlette's `BaseHTTPMiddleware` has a known architectural
bug where it wraps `StreamingResponse` bodies, causing infinite streams like
MJPEG to hang indefinitely (no headers, no data sent to client). See
[Starlette #1678](https://github.com/encode/starlette/issues/1678). The pure
ASGI middleware conversion is in place but MJPEG may still fail on certain
Starlette versions. The `/stream.mjpeg` endpoint is preserved as a fallback.

**Key constraints:**
- **Never use `BaseHTTPMiddleware`** with `StreamingResponse` — use pure ASGI
  middleware (intercept `http.response.start` via `send` wrapper)
- **Snapshot cache:** `/stream.jpg` caches the encoded JPEG for 33ms to avoid
  re-encoding on rapid polls (handles 500+ req/s with zero CPU waste)
- **Visibility pause:** JS stops polling when the browser tab is hidden
  (`visibilitychange` listener) to save Jetson CPU
- **Error backoff:** Exponential backoff on fetch errors (1s → 2s → 4s, cap 10s)
- **View-switch pause:** Polling pauses when leaving Operations view (CSS sets
  img to width:0/height:0, which aborts pending requests and fires error events).
  Resumes immediately when returning to Operations.
- `asyncio.Event` is **not thread-safe** across threads — never use it to signal
  between the pipeline thread and uvicorn's event loop. Use `threading.Event` or
  simple polling with `asyncio.sleep()` instead

### API Hardening Patterns

- **All POST endpoints** use `_parse_json(request)` helper which returns `None`
  on malformed input (returns 400, not 500). Never use bare `await request.json()`.
- **Same-origin auth bypass:** Requests from the built-in dashboard include
  `Sec-Fetch-Site: same-origin` or a matching `Origin` header. `_check_auth()`
  skips Bearer token validation for these — the dashboard works without a token
  while external API access (curl, scripts) still requires one.
- **Auth-free read endpoints:** `GET /api/config/full`, `GET /api/stream/quality`,
  `GET /api/stats`, `GET /api/tracks` — read-only data needed by the dashboard.
- **POST /api/stream/quality** is auth-free — it's a display preference (controls
  JPEG compression), not a vehicle control action.
- **Safety-critical callbacks** (`/api/abort`) must be wrapped in try/except —
  a callback crash must never prevent the instructor from getting a response.
- **`_auth_failures` dict** is a `defaultdict(list)` — `del` then re-access
  silently recreates the key. Always use a local variable for the filtered list,
  then decide whether to store or delete. Prune empty entries to prevent growth.
- **Log review endpoints** cap at 50k records to prevent OOM on large files.
- **`/api/export`** cleans up temp ZIP files via `BackgroundTask`.

### Overlay and Detection Pipeline

- **Bounding box coordinates must be clamped** to frame bounds before drawing.
  Targets near frame edges can have negative coords or exceed frame width/height,
  which crashes OpenCV or produces artifacts. See `overlay.py:_draw_single_track`.
- **MAVLink alerts are deduplicated by label** per frame. With 10 "person"
  detections, only one `alert_detection("person")` call fires instead of 10.
- **Track list uses DOM diffing** (not full rebuild) to prevent wrong-target-lock
  race conditions when tracks appear/disappear between polls.

### Security

- **No `innerHTML` sinks** in app.js, operations.js, or settings.js — all
  dynamic content uses `.textContent` or `.value` (safe against XSS).
- **`review_export.py`** generates standalone HTML reports. All user data
  (labels, track IDs, images) must be escaped via the `esc()` helper. The
  `json.dumps` output uses `.replace("</", "<\\/")` to prevent `</script>`
  breakout.
- **CSP blocks inline scripts** — all templates use external JS files
  (`app.js`, `operations.js`, `settings.js`, `control.js`, `instructor.js`,
  `setup.js`, `review-map.js`). `'unsafe-inline'` is removed from `script-src`.
  Never add inline `<script>` blocks to templates — create external files instead.
- **CSP allows `frame-src youtube-nocookie.com`** for the Power User easter egg.

## Model Manifest

- **Class introspection:** `model_manifest.py:extract_classes()` loads `.pt` files
  via ultralytics to auto-populate class names. `.engine`/`.onnx` inherit classes
  from matching `.pt` by stem name. Run
  `python -m hydra_detect.model_manifest models/` to regenerate.
- **Alert class categories** in `server.py` use case-insensitive matching via
  `_CATEGORY_LOOKUP` dict (built once at import). Categories: People, Ground
  Vehicles, Aircraft, Watercraft, Weapons/Threats, Equipment, Animals,
  Infrastructure. Unknown classes fall to "Other".

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

- **MAVLink public API:** Use `mavlink.send_raw_message(msg)` and
  `mavlink.send_param_set(param_id, value)` for outbound messages — never
  access `_mav` or `_send_lock` directly. Use `mavlink.connected` property
  instead of `self._mav._mav is None`.
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

**Approach mode safety invariant:** Always confirm `set_mode("GUIDED")` succeeds
before committing to an approach mode. Return False on failure — never leave the
controller in an active mode without GUIDED. `abort()` restores
`_pre_approach_mode` (not hardcoded LOITER).

**RF navigator `best_position`** can be `None` (no samples recorded). Always
check `sample_count > 0` before commanding `guided_to` with the position.

## Deployment

- **Always run in Docker** — bare `python -m hydra_detect` lacks CUDA (1.5 FPS
  vs 7+ FPS with GPU). Use Docker or systemd service for all runs.
- **`output_data/` ownership:** Docker creates root-owned files. Run
  `sudo chown -R sorcc:sorcc output_data/` if bare-Python run fails with
  PermissionError on `hydra.log`.
- **Deploy timing:** After `systemctl restart hydra-detect`, allow ~35 seconds
  for YOLO model load before health check responds. Verify with
  `curl -s http://localhost:8080/api/health`.
- **Code is baked into Docker** at build time (`COPY hydra_detect/` in Dockerfile).
  A `git pull` on the host does NOT update the running container.
- **`/api/restart`** only restarts the pipeline loop — does NOT restart the Python
  process. Code changes to `server.py` or JS files are NOT picked up.
- **Container name:** The systemd service creates a container named `hydra-detect`
  (not `hydra`). Always use `sudo docker rm -f hydra-detect` if needed.
- **Deploy script:** `scripts/deploy.sh [branch]` — stashes local changes, pulls,
  builds, restarts, and verifies with a health check.
- **No auto-deploy:** No watchtower, no GitHub webhooks, no CI/CD deploy step.
  All deploys are manual via SSH.
- **Local config.ini changes** on the Jetson will block `git pull` — always
  `git stash` first.
- **CI:** `.github/workflows/ci.yml` runs lint + tests on push to `main` and
  `claude/*` branches.

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

## Companion Project

**Argus** (`/home/sorcc/argus`, `github.com/rmeadomavic/Argus`) is the
RF survey payload on Raspberry Pi — companion to Hydra. Patterns adopted from it:
config schema validation, event logger, TAK/CoT export, config API. Patterns
worth porting: response caching with stale fallback, waypoint export (QGC WPL 110).

## SORCC Ecosystem

This project is part of the SORCC ecosystem alongside
[Argus](https://github.com/rmeadomavic/Argus) (RF survey payload on
Raspberry Pi).

- Use consistent terminology: **uncrewed** (not unmanned), **sortie** (not
  mission/run), **CULEX** (culminating exercise), **STX** (situational training
  exercise), **EENT** (night ops), **platform** (not vehicle, when referring to
  the full system)
- Documentation tone: technical but accessible — SOF operators are smart and
  mission-focused but may not have software backgrounds
- Shared config pattern: INI with `.factory` defaults (`config.ini` is the user
  interface; `config.ini.factory` ships known-good defaults for factory reset)
- Shared code patterns: config schema validation, event logger, TAK/CoT export,
  config API, detection/survey logging with hash-chain verification
- Cross-references: link to sibling projects where relevant — Hydra handles
  detection/tracking/engagement, Argus handles RF survey/SIGINT, both feed
  TAK for a common operating picture
- Shared UI standards: dark ops-center theme, data-dense layouts, defense-grade
  polish (see UI/UX Design Standards below)

## UI/UX Design Standards

Both Hydra and Argus dashboards target **defense-grade polish** — think
Palantir Foundry / Anduril Lattice, not startup landing pages. Every element
must be functional, not decorative:

- **Consistent dark theme** and design tokens shared between projects
- **Data-dense layouts** — maximize info per screen for rapid operator scanning
- **Real data only** — no placeholder content, no gratuitous animations
- **Useful graphics** — signal charts, FPS trends, detection heatmaps that aid decisions
- **Ops-center aesthetic** — typography and spacing optimized for stress and low light
- **Mobile-first** — large touch targets for gloved hands, works on tablets in the field
