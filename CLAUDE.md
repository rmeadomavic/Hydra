# Hydra Detect v2.0 — Claude Code Guidelines

## Project Context

Hydra Detect is a **real-time object detection and tracking payload** for uncrewed
vehicles (drones, boats, rovers) running ArduPilot. It runs on **NVIDIA Jetson
Orin Nano** hardware and communicates with ground control stations via MAVLink.

This is safety-critical software with real-time and hardware constraints. Changes
must preserve deterministic timing, bounded memory usage, and fail-safe behavior.

### Architecture Overview

```
Camera → Detector (YOLO/NanoOWL) → ByteTrack Tracker → MAVLink Alerts
                                                      → Web Dashboard (FastAPI)
                                                      → Detection Logger
```

- **Entry point:** `hydra_detect/__main__.py`
- **Orchestrator:** `hydra_detect/pipeline.py` — the main detect→track→alert loop
- **Config:** `config.ini` (INI format, all tunables live here)
- **Tests:** `pytest` — run with `python -m pytest tests/`

## Jetson Deployment Constraints

### Memory (4–8 GB shared CPU/GPU RAM)
- Never load multiple large models simultaneously
- Prefer in-place numpy/OpenCV operations over copies
- Avoid unbounded caches or queues — always use fixed-size ring buffers
- Profile with `tegrastats` or `jtop` before and after changes

### CUDA / TensorRT
- NanoOWL uses TensorRT engines compiled for specific Jetson hardware — these
  are NOT portable across devices
- Model inference must stay on GPU; avoid unnecessary `.cpu()` or `.numpy()` calls
  that trigger device-to-host transfers
- Do not add `torch.cuda.synchronize()` in hot paths — it kills throughput
- The `nanoowl` import may fail on non-Jetson machines; always handle ImportError
  with a fallback path (see `nanoowl_detector.py`)

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
- Never commit secrets or API tokens — do not place secrets in tracked
  `config.ini`; instead create an ignored local config file (for example,
  `config.local.ini` copied from `config.ini`) and run with
  `python -m hydra_detect --config config.local.ini`

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
docker build -t hydra-detect .

# Monitor Jetson resources
tegrastats
```
