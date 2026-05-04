---
name: hydra
description: >
  Provides full context about Hydra Detect v2.0 — a real-time object detection
  and tracking payload for uncrewed vehicles on NVIDIA Jetson. Use whenever the
  user mentions Hydra, detection pipelines, Jetson deployment, MAVLink integration,
  or uncrewed vehicle payloads. Also captures new ideas and notes for the project
  backlog.
user-invocable: true
disable-model-invocation: false
argument-hint: "[topic or new idea to capture]"
---

# Hydra Detect v2.0

## What It Is

**Hydra Detect** is a real-time object detection and tracking payload for
uncrewed vehicles (drones, boats, rovers) running ArduPilot. It runs on
**NVIDIA Jetson Orin Nano** hardware and communicates with ground control
stations via MAVLink.

**Owner:** rmeadomavic
**Repo:** https://github.com/rmeadomavic/Hydra
**Hardware:** Jetson Orin Nano 8GB, Pixhawk 6C, RFD 900x telemetry (Net ID 8),
HDZero FPV (Freestyle V2 VTX + Nano 90 cam + Monitor + Goggles 1 + Goggles 2),
USB cameras (Logitech C270/C920)
**GCS:** Mission Planner (primary), QGroundControl on Steam Deck (testing)
**Video architecture:** USB webcam → Jetson (detection), HDZero Nano 90 → FPV
(pilot view + OSD). Freestyle V2 is fully digital — no analog tap for Jetson.
OSD via MSP DisplayPort: Pixhawk UART TX → VTX RX pad.

## Architecture

```
Camera Source --> YOLO Detector --> ByteTrack Tracker --> MAVLink Alerts
                                                      --> FastAPI Web Dashboard (port 8080)
                                                      --> Detection Logger (JSONL/CSV + images)
```

**Key files:**
- Entry point: `hydra_detect/__main__.py`
- Main loop: `hydra_detect/pipeline.py` (detect -> track -> alert, >=5 FPS)
- Config: `config.ini` (all tunables)
- MAVLink: `hydra_detect/mavlink_io.py` (heartbeat, GPS, alerts, commands)
- Camera: `hydra_detect/camera.py` (USB, RTSP, GStreamer, file)
- Tracker: `hydra_detect/tracker.py` (ByteTrack via supervision)
- OSD: `hydra_detect/osd.py` + `scripts/hydra_osd.lua` (FC-side)
- Web: `hydra_detect/web/server.py` (FastAPI + MJPEG)
- RF Hunt: `hydra_detect/rf/hunt.py` (Kismet + SDR RSSI homing)
- Autonomous: `hydra_detect/autonomous.py` (geofence + strike logic)
- Tests: `python -m pytest tests/`

## Current Goals & Direction

1. **Field-ready deployment** — move from bench testing to real vehicle ops
2. **UART connection** — replace USB-C bench link with GPIO UART to Pixhawk TELEM2
3. **HDZero video pipeline** — use FPV link as detection camera source
4. **QGC on Steam Deck** — portable GCS as alternative to Mission Planner laptop
5. **SDR integration** — Kismet + RTL-SDR for RF hunt / spectrum awareness
6. **OSD in goggles** — detection data overlaid on FPV feed
7. **Autonomous strike safety** — validate all safeguards on real hardware
8. **Performance profiling** — thermal, memory, FPS across power modes

## Audiences

Hydra serves three audiences — tag features and ideas by which they serve:

- **Demo** — leadership/brass. Visual impact, capability showcase. (live video,
  RTSP, web dashboard, signal map, recording, TAK/CoT)
- **Ops** — instructor + students in the field. Reliable, safe, practical.
  (MAVLink alerts, auto-loiter, OSD, RF homing, geofence, detection logs)
- **Dev** — building and extending. Maintainable, testable, debuggable.
  (/jetson-check, test suite, Docker, sim GPS, debug logging, ML fine-tuning)

## SORCC Course Context

Hydra is built for **SORCC** (Special Operations Robotics Capability Course) —
a 6-week SOF training program. 15 students in 5 teams operate drones, rovers,
boats, and fixed-wing aircraft with Hydra payloads. Students interact only via
the web dashboard (never SSH). Errors must be plain English. Up to 20 Hydra
instances run simultaneously during exercises. See `CLAUDE.md` "SORCC Course
Context" section for full details including platforms, workflow, vocabulary, and
design implications.

## Constraints (Safety-Critical)

- **Memory:** 4-8 GB shared CPU/GPU. Fixed-size ring buffers only. No unbounded caches.
- **Real-time:** Main loop >=5 FPS. No blocking I/O in hot path.
- **Threading:** `threading.Lock` only (not asyncio). Background threads must not starve detector.
- **GPU:** Inference stays on GPU. No `.cpu()` or `.numpy()` in hot paths.
- **Fail-safe:** Vehicle must stay safe if any component crashes.
- **Docker:** Base image `dustynv/l4t-pytorch:r36.4.0`, `--runtime nvidia` required.

## MAVLink Protocol

- **Alerts:** STATUSTEXT (50 char max, per-label throttled)
- **OSD data:** NAMED_VALUE_FLOAT/INT (for Lua script on FC)
- **Commands:** MAV_CMD_USER_1 (31010)=Lock, USER_2 (31011)=Strike, USER_3 (31012)=Unlock
- **GPS:** GLOBAL_POSITION_INT polled at 2 Hz
- **Connection:** Serial or UDP, configured in config.ini

## Hardware Setup Docs

- `docs/jetson-setup-guide.md` — Docker build & run on Jetson
- `docs/jetson-initial-setup.md` — JetPack flashing
- `docs/pixhawk-setup.md` — Pixhawk 6C config, serial ports, RFD 900x radios
- `docs/hdzero-osd-setup.md` — OSD wiring for HDZero + ArduPilot
- `docs/jetson-hardware-testing-checklist.md` — Current testing plan & backlog

## Capturing New Ideas

If the user mentions something new about Hydra — a feature idea, a bug they
noticed, a hardware change, a testing need, or anything worth remembering —
note it clearly and suggest adding it to one of these places:

1. **`docs/jetson-hardware-testing-checklist.md`** — for testing tasks and
   hardware validation items
2. **Todoist project "Hydra Jetson Hardware Testing"** — for actionable tasks
   with priorities and labels
3. **GitHub issue** — for bugs or feature requests that need tracking
4. **`CLAUDE.md`** — for permanent coding guidelines and constraints

Always tag ideas by audience (demo/ops/dev) and ask:
"Want me to add this to the testing checklist / Todoist / an issue?"

## Live Debugging

Use `/jetson-logs` to fetch live application logs from the running instance.
The API at `GET /api/logs?lines=N&level=LEVEL` tails `hydra.log` with
structured output. Use this proactively when debugging hardware issues, runtime
errors, or unexpected behavior after config changes.

## Quick Reference

```bash
# Run
python -m hydra_detect --config config.ini

# Test
python -m pytest tests/ -v

# Lint + types
flake8 hydra_detect/ tests/
mypy hydra_detect/

# Docker (on Jetson)
docker build -t hydra-detect .
docker run --rm --privileged --runtime nvidia \
  --device /dev/video0 --device /dev/ttyACM0 \
  -v $(pwd)/models:/models -p 8080:8080 \
  hydra-detect:latest

# Monitor Jetson
tegrastats
```
