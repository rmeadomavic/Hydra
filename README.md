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

- Detect → track → alert pipeline (GPU-accelerated). Informal bench runs on
  Jetson Orin Nano have measured ≥5 FPS; results depend on the model, input,
  and active capabilities.
- Four-tab operator dashboard: `#ops`, `#tak`, `#config`, `#settings`.
  Autonomy controls live inside Config; system diagnostics inside Settings.
- Shipped vehicle control modes: Follow, Cue, and Pixel-Lock, plus an
  always-available safety override abort. Drop is in development and blocked
  until the ARMED gate is implemented.
- Five-gate autonomy evaluation stack (geofence / vehicle_mode /
  operator_lock / gps_fresh / cooldown) with shipped dry-run and shadow
  modes. Live command execution is gated and remains in development pending
  completion of ARMED gating.
- RF collection and status integration are available; autonomous RF Hunt and
  live RF homing navigation remain blocked pending the ARMED gate.
- TAK/CoT output + HMAC-verified GeoChat input + peer roster +
  audit roll-up.
- Full post-mission review: hash-chained JSONL detection logs, event
  timeline, map replay, QGC waypoint export.

## Quick start

```bash
# 1. Clone
git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra

# 2. Run the Jetson setup (checks prerequisites, builds, configures, and
#    offers to launch the container)
bash scripts/hydra-setup.sh

# 3. To launch directly after setup, if you declined its launch prompt
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

## View in browser

Hydra's RTSP stream (`rtsp://<jetson-ip>:8554/hydra`) is republished
as low-latency WebRTC by an optional `mediamtx` sidecar in
`docker-compose.yml`. Informal Jetson Orin Nano bench runs measured
sub-200 ms browser latency; browser, network, camera, and encoding settings
affect the result, and no client install is required.
Bring it up with `docker compose up`, then open
`http://<jetson-ip>:8889/cam/whep` in Chrome/Edge. Disable with
`HYDRA_STREAMING_MTX=off` (the sidecar is profile-gated).

## Dev loop (no rebuild)

For UI work (templates, CSS, JS, or FastAPI route tweaks) you don't need
to rebuild the image. `compose.dev.yml` bind-mounts `hydra_detect/` into
a second container on port **8081** and runs uvicorn with `--reload`:

```bash
make build         # once — uses the existing Dockerfile
make dev           # docker compose up on compose.dev.yml (:8081)
# edit hydra_detect/web/templates/*.html or static/js/*.js
# refresh http://<jetson-ip>:8081/ — changes are live
make dev-down      # tear it down when finished
```

The prod systemd container keeps running on :8080 the whole time. Dev
mode runs the FastAPI shell only — no camera, no YOLO, no MAVLink — so
use :8080 for end-to-end testing and :8081 for fast iteration on the
dashboard.

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
| [Over-the-air updates](docs/ota.md) | `/etc/hydra/channel`, systemd timer, version surface on `/api/health` (#152) |

## Vehicle compatibility

| Feature | Drone | USV | UGV | Fixed-wing |
|---------|:-----:|:---:|:---:|:----------:|
| Detection + TAK | ✓ | ✓ | ✓ | ✓ |
| Follow | ✓ | ✓ | ✓ | — |
| Drop (in development; ARMED-gated) | ~ | ~ | ~ | — |
| Strike | ✓ | ✓ | ✓ | — |
| Dry-run / shadow autonomy | ✓ | ✓ | ✓ | — |
| Live autonomy (ARMED-gated) | ~ | ~ | ~ | — |
| Yaw control | CONDITION_YAW | Rudder | Steering | — |
| Hold mode | LOITER | HOLD | HOLD | LOITER |
| Autonomous RF Hunt / homing navigation (ARMED-gated) | ~ | ~ | ~ | ~ |

`✓` supported · `~` limited · `—` not supported. Fixed-wing is detection + TAK marking only (15-25 m/s flight speed) — see [`vehicle.fw`](docs/configuration.md#fixed-wing-profile-is-detection--tak-only).

## Dependencies

Python 3.10+, OpenCV (CUDA in Docker), ultralytics, supervision,
pymavlink, FastAPI + uvicorn. Optional: Kismet, GStreamer, mgrs,
requests. Base image: `dustynv/l4t-pytorch:r36.4.0`.
