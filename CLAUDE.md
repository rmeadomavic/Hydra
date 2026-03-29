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
                                                      → Web Dashboard (FastAPI)
                                                      → Detection Logger
```

- **Entry point:** `hydra_detect/__main__.py`
- **Orchestrator:** `hydra_detect/pipeline.py` — the main detect→track→alert loop
- **Config:** `config.ini` (INI format, all tunables live here)
- **Tests:** `pytest` — run with `python -m pytest tests/`

## SORCC Course Context

**SORCC** (Special Operations Robotics Capabilities Course) is a 6-week IQT
program at Oak Grove, NC. 15 students in 5 teams of 3, active-duty SOF
(SF, Rangers, SMU support). They are technically capable and mission-focused
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
- **3 instructors:** Kyle (lead/dev), Charles (platform SME), Vinnie (docs)
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
curl -s 'http://100.109.160.122:8080/api/logs?lines=100&level=WARNING'
```

Use this proactively when diagnosing runtime issues on the Jetson — it provides
real-time context that `journalctl` or Docker logs cannot (structured, filtered,
and accessible without SSH).

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
