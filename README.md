# Hydra Detect v2.0

![CI](https://github.com/rmeadomavic/Hydra/actions/workflows/ci.yml/badge.svg)
![Platform](https://img.shields.io/badge/Platform-Jetson_Orin_Nano-2d3a2e?style=flat-square&labelColor=1a1a1a)
![Vehicle](https://img.shields.io/badge/Vehicle-ArduPilot-2d3a2e?style=flat-square&labelColor=1a1a1a)
![Detection](https://img.shields.io/badge/Detection-YOLOv8-2d3a2e?style=flat-square&labelColor=1a1a1a)

Real-time object detection and tracking payload for uncrewed vehicles
running ArduPilot. Runs on NVIDIA Jetson Orin Nano. Processes a camera
feed through YOLO + ByteTrack, pushes detection data to the GCS over
MAVLink, emits CoT to TAK, and serves an operator dashboard on port
8080. No firmware changes. Drones, boats, rovers, fixed-wing.

## What ships

- Detect → track → alert pipeline (≥5 FPS on Orin Nano, GPU-accelerated).
- Six-tab operator dashboard: `#ops`, `#tak`, `#autonomy`, `#systems`,
  `#config`, `#settings`.
- Vehicle control modes: Follow, Drop, Strike, Pixel-Lock, plus an
  always-available instructor abort.
- Five-gate autonomy stack (geofence / vehicle_mode / operator_lock /
  gps_fresh / cooldown) with dry-run, shadow, and live modes.
- TAK/CoT output + HMAC-verified GeoChat input + peer roster +
  audit roll-up.
- Full post-mission review: hash-chained JSONL detection logs, event
  timeline, map replay, QGC waypoint export.

## Quick start

```bash
# 1. Clone and build
git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra
docker build --network=host -t hydra-detect .

# 2. Run under systemd (golden-image Jetsons)
sudo systemctl enable --now hydra-detect
sudo systemctl status hydra-detect

# 3. Or run directly
docker run --rm --privileged --runtime nvidia --network host \
  -v $(pwd)/config.ini:/app/config.ini \
  -v $(pwd)/models:/models \
  -v $(pwd)/output_data:/data \
  hydra-detect

# 4. Open the dashboard
# http://<jetson-ip>:8080/
```

Edit `config.ini` to set camera source, MAVLink connection, callsign,
and TAK endpoint before the first run. First-boot operators can use
the `/setup` wizard instead of editing the file by hand.

## Docs

| Guide | Description |
|-------|-------------|
| [Dashboard user guide](docs/dashboard-user-guide.md) | Operator walkthrough of each tab and every control |
| [Architecture](docs/architecture.md) | Data flow, module ownership, FastAPI ↔ pipeline contract |
| [API reference](docs/api-reference.md) | Every endpoint, auth, body, response shape |
| [Configuration](docs/configuration.md) | Every `config.ini` key |
| [Autonomous operations](docs/autonomous-operations.md) | Five gates, geofence, two-stage arm |
| [Vehicle control](docs/vehicle-control.md) | Follow / Drop / Strike / Pixel-Lock mechanics |
| [RF homing](docs/rf-homing.md) | Kismet, gradient ascent, state machine |
| [TAK integration](docs/tak-integration.md) | CoT output, GeoChat, callsign routing |
| [FPV OSD](docs/fpv-osd.md) | Three OSD modes, wiring, FC setup |
| [Post-mission review](docs/post-mission-review.md) | Logs, verification, map replay, export |
| [Deployment](docs/deployment.md) | systemd, Docker, TLS, multi-Jetson fleet |
| [Development](docs/development.md) | Project layout, testing, extending |
| [Preservation rules](docs/preservation-rules.md) | Hidden features + brand invariants — read before deleting anything unfamiliar |

## Vehicle compatibility

| Feature | Drone | USV | UGV | Fixed-wing |
|---------|:-----:|:---:|:---:|:----------:|
| Follow | ✓ | ✓ | ✓ | ~ |
| Drop | ✓ | ✓ | ✓ | ~ |
| Strike | ✓ | ✓ | ✓ | — |
| Autonomy | ✓ | ✓ | ✓ | — |
| Yaw control | CONDITION_YAW | Rudder | Steering | — |
| Hold mode | LOITER | HOLD | HOLD | LOITER |
| RF homing | ✓ | ✓ | ✓ | ✓ |

`✓` supported · `~` limited · `—` not supported.

## Dependencies

Python 3.10+, OpenCV (CUDA in Docker), ultralytics, supervision,
pymavlink, FastAPI + uvicorn. Optional: Kismet, GStreamer, mgrs,
requests. Base image: `dustynv/l4t-pytorch:r36.4.0`.
