# Hydra Detect v2.0

Real-time object detection and tracking system built for uncrewed vehicles — drones, boats, rovers, or anything running [ArduPilot](https://ardupilot.org/). Runs on NVIDIA Jetson or any Linux box with a camera and a MAVLink radio.

```
Camera ─> Detector (YOLO) ─> ByteTrack ─> MAVLink Alerts
                                                   ─> Target Lock / Strike
                                                   ─> FPV OSD (via FC OSD chip)
                                                   ─> Web Dashboard (MJPEG)
                                                   ─> Detection Logger
```

## What It Does

- **Detect** objects in real-time using YOLOv8
- **Track** objects across frames with persistent IDs (ByteTrack)
- **Alert** your GCS (Mission Planner / QGroundControl) via MAVLink STATUSTEXT with GPS coordinates
- **Keep in Frame** — lock a tracked target; the vehicle yaws to keep it centered in the camera
- **Strike** — command the vehicle to navigate toward a target's estimated position (GUIDED mode)
- **Log** every detection with timestamps, GPS, confidence scores, and optional image snapshots
- **FPV OSD** — show detection data on your FPV goggles via the flight controller's onboard OSD chip (Matek H743, SpeedyBee F405-Wing, etc.)
- **Stream** live annotated video to any browser over MJPEG

## Quick Start

```bash
# Clone and install
git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra
pip install -r requirements.txt

# Edit config for your setup
vim config.ini

# Run
python -m hydra_detect --config config.ini
```

Open **http://localhost:8080** in a browser for the operator dashboard.

### Docker (Jetson)

```bash
# Pull the base image first (~6 GB)
docker pull dustynv/l4t-pytorch:r36.4.0

# Build and run
docker build --network=host -t hydra-detect .
docker run --rm --runtime nvidia \
  --device /dev/video0 \
  --device /dev/ttyACM0 \
  -v $(pwd)/output_data:/data \
  -p 8080:8080 \
  hydra-detect
```

See [docs/jetson-setup-guide.md](docs/jetson-setup-guide.md) for the full
step-by-step setup guide.

### Jetson Orin Nano Super (MAXN SUPER) Preflight

Before field deployment, run a quick host sanity check on the Jetson:

```bash
./scripts/jetson_preflight.sh
```

Recommended one-time performance setup:

```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```

If preflight reports no failures, Hydra is generally ready to run on-device (camera/MAVLink wiring still needs to match your `config.ini`).

### systemd Service

```bash
sudo cp scripts/hydra-detect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-detect
```

## Web Dashboard

The dashboard runs on port 8080 and gives you:

| Section | What It Shows |
|---------|--------------|
| **Video Stream** | Live MJPEG feed with bounding boxes, track IDs, and target lock overlays |
| **Pipeline Stats** | FPS, inference time, active tracks, total detections, detector engine |
| **Vehicle Link** | MAVLink connection status, GPS fix, position (MGRS or lat/lon) |
| **Target Control** | Active track list, Keep in Frame / Strike / Release buttons |
| **Detection Config** | Live-editable confidence threshold |
| **Detection Log** | Scrolling feed of recent detections with timestamps and coordinates |

### Vehicle Commands

**LOITER / HOLD** — Immediately commands the vehicle to hold position. Uses `LOITER` mode for drones, `HOLD` for rovers/boats.

**KEEP IN FRAME** — Select a tracked object, click Keep in Frame. The pipeline sends `CONDITION_YAW` corrections each frame to keep the target centered in the camera. Works on any ArduPilot vehicle type.

**STRIKE** — Select a target, click Strike, confirm in the popup. The system:
1. Estimates the target's GPS position from vehicle heading + camera offset
2. Switches the vehicle to `GUIDED` mode
3. Sends a waypoint via `SET_POSITION_TARGET_GLOBAL_INT`
4. Continues yaw tracking during approach
5. Sends STATUSTEXT alerts to your GCS

The operator always retains override via Mission Planner or any GCS.

### FPV OSD Overlay

If your flight controller has an onboard analog OSD chip (MAX7456 / AT7456E), Hydra can send detection telemetry directly to it over MAVLink. This composites text onto your analog FPV feed with sub-millisecond latency — no extra hardware, no video passthrough, no added delay.

**What shows on your goggles:**
```
T:3 12fps 35ms LK#5TRK
```
Track count, pipeline FPS, inference time, and locked target status.

**Compatible flight controllers:** Matek H743, SpeedyBee F405-Wing, or any ArduPilot FC with AT7456E/MAX7456. **Not compatible** with Pixhawk 6C (no OSD chip) — use the web dashboard overlay instead.

**Two modes:**

| Mode | Setup | What You Get |
|------|-------|-------------|
| `statustext` | Just enable in config — no FC changes needed | Detection info in the OSD message panel |
| `named_value` | Copy `scripts/hydra_osd.lua` to FC SD card, enable Lua scripting | Richer display with stale-link warnings |

**Quick setup (statustext mode):**
```ini
[osd]
enabled = true
mode = statustext
```

**Lua script setup (named_value mode):**
1. Copy `scripts/hydra_osd.lua` to `APM/scripts/` on the FC SD card
2. Set ArduPilot parameters: `SCR_ENABLE=1`, `SCR_HEAP_SIZE=65536`, `OSD_TYPE=1`, `OSD1_ENABLE=1`
3. Set in `config.ini`:
   ```ini
   [osd]
   enabled = true
   mode = named_value
   ```
4. Reboot the FC

**Using HDZero (digital FPV)?** See [docs/hdzero-osd-setup.md](docs/hdzero-osd-setup.md) for MSP DisplayPort wiring, ArduPilot parameters, and troubleshooting.

## Configuration

All settings are in `config.ini`. Here's what each section controls:

### [camera]
| Key | Default | Description |
|-----|---------|-------------|
| `source` | `0` | Camera source — device index (`0`), RTSP URL, GStreamer pipeline, or file path |
| `width` | `640` | Capture width in pixels |
| `height` | `480` | Capture height in pixels |
| `fps` | `30` | Target frame rate |
| `hfov_deg` | `60.0` | Horizontal field of view in degrees (used for target bearing estimation) |

### [detector]
| Key | Default | Description |
|-----|---------|-------------|
| `yolo_model` | `yolov8n.pt` | YOLO model file (downloaded automatically on first run) |
| `yolo_confidence` | `0.45` | YOLO confidence threshold |
| `yolo_classes` | *(all)* | Comma-separated COCO class IDs to filter (empty = all) |

### [tracker]
| Key | Default | Description |
|-----|---------|-------------|
| `track_thresh` | `0.5` | Minimum confidence to initiate a track |
| `track_buffer` | `30` | Frames to keep a lost track alive |
| `match_thresh` | `0.8` | IoU threshold for matching detections to tracks |

### [mavlink]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable MAVLink connection |
| `connection_string` | `/dev/ttyACM0` | Serial device or `udp:127.0.0.1:14550` |
| `baud` | `115200` | Serial baud rate |
| `source_system` | `1` | MAVLink system ID |
| `min_gps_fix` | `3` | Minimum GPS fix type (3 = 3D fix) |
| `alert_statustext` | `true` | Send detection alerts as STATUSTEXT |
| `alert_interval_sec` | `5.0` | Minimum seconds between alerts for the same label |
| `severity` | `2` | MAVLink severity level (0=Emergency, 7=Debug) |
| `auto_loiter_on_detect` | `false` | Auto-switch to LOITER on detection |
| `guided_roi_on_detect` | `false` | Auto-set gimbal ROI on detection |
| `strike_distance_m` | `20.0` | How far ahead (metres) to project the strike waypoint |

### [web]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable web dashboard |
| `host` | `0.0.0.0` | Bind address |
| `port` | `8080` | HTTP port |
| `mjpeg_quality` | `70` | JPEG quality for the video stream (1-100) |

### [osd]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable FPV OSD overlay (requires MAVLink and FC with OSD chip) |
| `mode` | `statustext` | OSD mode: `statustext` (simple) or `named_value` (needs Lua script on FC) |
| `update_interval` | `0.2` | Seconds between OSD updates (lower = more responsive, more MAVLink traffic) |

### [logging]
| Key | Default | Description |
|-----|---------|-------------|
| `log_dir` | `/data/logs` | Directory for detection log files |
| `log_format` | `jsonl` | Log format: `csv` or `jsonl` |
| `save_images` | `true` | Save full-frame JPEG snapshots on detection |
| `image_dir` | `/data/images` | Directory for snapshot images |
| `image_quality` | `90` | JPEG quality for snapshots |
| `save_crops` | `false` | Save cropped object images |
| `crop_dir` | `/data/crops` | Directory for crop images |

## Project Structure

```
Hydra/
  config.ini                          # All settings
  requirements.txt                    # Python dependencies
  Dockerfile                          # Jetson container build
  scripts/
    hydra-detect.service              # systemd unit file
    jetson_preflight.sh               # Jetson hardware preflight checks
    hydra_osd.lua                     # ArduPilot Lua script for FPV OSD

  hydra_detect/
    __init__.py
    __main__.py                       # Entry point (python -m hydra_detect)
    pipeline.py                       # Main orchestrator — ties everything together
    camera.py                         # Thread-safe capture with auto-reconnect
    tracker.py                        # ByteTrack multi-object tracker
    overlay.py                        # Bounding box + HUD + target lock renderer
    osd.py                            # FPV OSD overlay via MAVLink (FC OSD chip)
    mavlink_io.py                     # MAVLink connection, alerts, vehicle commands
    detection_logger.py               # CSV/JSONL logging with image snapshots

    detectors/
      __init__.py
      base.py                         # Abstract detector interface
      yolo_detector.py                # YOLOv8/v11 via ultralytics
      nanoowl_detector.py             # (archived) NanoOWL / OWL-ViT open-vocabulary

    web/
      __init__.py
      server.py                       # FastAPI server — REST API + MJPEG stream
      templates/
        index.html                    # Operator dashboard (single-page app)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Operator dashboard |
| `GET` | `/stream.mjpeg` | Live MJPEG video stream |
| `GET` | `/api/stats` | Pipeline statistics (FPS, tracks, GPS, etc.) |
| `GET` | `/api/config` | Current runtime configuration |
| `POST` | `/api/config/prompts` | Update detection prompts |
| `POST` | `/api/config/threshold` | Update confidence threshold |
| `GET` | `/api/detections` | Recent detection log entries |
| `GET` | `/api/tracks` | Currently active tracked objects |
| `GET` | `/api/target` | Current target lock state |
| `POST` | `/api/target/lock` | Lock a track for keep-in-frame (`{"track_id": 5}`) |
| `POST` | `/api/target/unlock` | Release target lock |
| `POST` | `/api/target/strike` | Strike command (`{"track_id": 5, "confirm": true}`) |
| `POST` | `/api/vehicle/loiter` | Command LOITER / HOLD |

## Platform Notes

**Drone (ArduCopter)** — Uses `LOITER` mode for hold, `CONDITION_YAW` for tracking, `GUIDED` for strike. Best with a gimbal for ROI targeting.

**Boat (ArduRover in boat mode)** — Uses `HOLD` mode, yaw commands steer the rudder, `GUIDED` drives toward the target. Set `strike_distance_m` appropriately for water speed.

**Rover (ArduRover)** — Same as boat. Yaw commands turn the vehicle. Ensure clear path before strike.

**Any ArduPilot vehicle** — If it supports `GUIDED` mode and `CONDITION_YAW`, it works. The system auto-detects whether to use `LOITER` or `HOLD` from the vehicle's mode mapping.

## Dependencies

- Python 3.10+
- OpenCV (headless)
- [ultralytics](https://github.com/ultralytics/ultralytics) (YOLO)
- [supervision](https://github.com/roboflow/supervision) (ByteTrack)
- [pymavlink](https://github.com/ArduPilot/pymavlink) + pyserial
- FastAPI + uvicorn
- Optional: `mgrs` for military grid coordinates
