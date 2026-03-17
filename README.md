# Hydra Detect v2.0

Hydra is a real-time object detection and tracking system for uncrewed vehicles running ArduPilot. It runs on an NVIDIA Jetson as a companion computer, processes a camera feed through YOLO and ByteTrack, and pushes detection data to your ground control station over MAVLink.

No firmware changes required. If your vehicle supports GUIDED mode and you can plug in a camera, Hydra will work with it. Drones, boats, rovers.

```
Camera -> Detector (YOLO) -> ByteTrack -> MAVLink Alerts
                                               -> Target Lock / Strike
                                               -> Autonomous Strike Controller
                                               -> FPV OSD (via FC OSD chip)
                                               -> Web Dashboard (MJPEG)
                                               -> Detection Logger

Kismet (WiFi/SDR) -> RF Hunt Controller -> RSSI Gradient Ascent -> MAVLink Nav
```

## What It Does

- YOLOv8 object detection, real-time on-device via CUDA/TensorRT
- ByteTrack multi-object tracking with persistent IDs across frames
- MAVLink STATUSTEXT alerts with GPS coordinates sent to Mission Planner or QGroundControl
- Target lock: vehicle yaws to keep a tracked object centered in the camera
- Strike mode: navigates the vehicle toward a target's estimated GPS position in GUIDED mode
- Autonomous strike controller with geofencing, class whitelists, and cooldown timers
- RF source localization via Kismet RSSI and gradient ascent navigation
- FPV OSD overlay through the flight controller's OSD chip (MAX7456/AT7456E or MSP DisplayPort)
- Post-mission review with detection markers on OpenStreetMap
- Web dashboard with live MJPEG stream, pipeline stats, and vehicle controls
- Detection logging in CSV or JSONL with timestamps, GPS, confidence, and optional image snapshots

## Getting Started

### Automated Setup (Recommended)

After flashing JetPack ([guide](docs/jetson-initial-setup.md)), run: `bash scripts/hydra-setup.sh` — it handles everything interactively.

### Docker (Manual)

The recommended path is Docker on a Jetson. The base image is large (~6 GB) but it ships with CUDA, PyTorch, and TensorRT ready to go.

```bash
# Grab the base image (one-time download)
docker pull dustynv/l4t-pytorch:r36.4.0

# Clone the repo
git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra

# Build and run
docker build --network=host -t hydra-detect .
docker run --rm --privileged --runtime nvidia \
  -v $(pwd)/config.ini:/app/config.ini:ro \
  -v /usr/sbin/nvpmodel:/usr/sbin/nvpmodel:ro \
  -v /usr/bin/jetson_clocks:/usr/bin/jetson_clocks:ro \
  -v /etc/nvpmodel.conf:/etc/nvpmodel.conf:ro \
  -v /etc/nvpmodel:/etc/nvpmodel:ro \
  -v /var/lib/nvpmodel:/var/lib/nvpmodel \
  -v $(pwd)/models:/models \
  -v $(pwd)/output_data:/data \
  -p 8080:8080 \
  hydra-detect
```

Open **http://localhost:8080** in a browser. That's the operator dashboard.

For a full walkthrough starting from a bare Jetson, see the [Jetson flash guide](docs/setup/jetson-flash.mdx) followed by the [Docker install guide](docs/setup/jetson-docker.mdx).

### Bare Metal (Alternative)

If you prefer running outside Docker:

```bash
sudo apt update && sudo apt install -y python3-pip
sudo usermod -aG dialout $USER  # log out/in after this

git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra
sudo pip3 install -r requirements.txt

mkdir -p models
wget -P models https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s.pt

nano config.ini  # set camera source and MAVLink connection
sudo python3 -m hydra_detect --config config.ini
```

### Preflight Check (Jetson Orin Nano)

Before heading to the field, run the preflight script:

```bash
./scripts/jetson_preflight.sh
```

For best performance, run these once after boot:

```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```

### Auto-Start on Boot

```bash
sudo cp scripts/hydra-detect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-detect
```

## The Dashboard

The web dashboard runs on port 8080 and serves as the primary operator interface.

| Section | What It Shows |
|---------|--------------|
| **Video Stream** | Live MJPEG feed with bounding boxes, track IDs, and target lock overlays |
| **Pipeline Stats** | FPS, inference time, active tracks, total detections, detector engine |
| **Vehicle Link** | MAVLink connection status, GPS fix, position (MGRS or lat/lon) |
| **Target Control** | Active track list with Keep in Frame / Strike / Release buttons |
| **Detection Config** | Confidence threshold slider, adjustable without restarting |
| **Detection Log** | Scrolling feed of recent detections with timestamps and coordinates |

### Vehicle Control

**Hold Position** tells the vehicle to stop where it is. Uses `LOITER` for drones, `HOLD` for rovers and boats. The system auto-detects the correct mode.

**Keep in Frame** locks onto a tracked object and sends yaw corrections every frame to keep it centered in the camera. Works on any ArduPilot vehicle.

**Strike** navigates the vehicle toward a target's estimated GPS position. Requires confirmation via the dashboard popup. The sequence:

1. Hydra estimates target GPS from vehicle heading and camera offset
2. Switches the vehicle to GUIDED mode
3. Sends a waypoint via `SET_POSITION_TARGET_GLOBAL_INT`
4. Continues tracking and yawing during the approach
5. Sends STATUSTEXT alerts to your GCS throughout

You always have override through Mission Planner or any other GCS. Changing the flight mode from the GCS immediately overrides Hydra.

### FPV OSD Overlay

If your flight controller has an onboard analog OSD chip (MAX7456/AT7456E), Hydra can push detection telemetry onto your FPV feed over MAVLink. No extra hardware, no video passthrough, no added latency.

What it looks like on your goggles:

```
T:3 12fps 35ms LK#5TRK
```

That reads as: 3 active tracks, 12 FPS pipeline speed, 35ms inference time, target lock active on track #5.

Two modes are available:

| Mode | Setup | What You Get |
|------|-------|-------------|
| `statustext` | Config flag only | Detection info in the OSD message panel |
| `named_value` | Lua script on FC | Dedicated display with stale-link warnings |

**Statustext (simple):**
```ini
[osd]
enabled = true
mode = statustext
```

**Named value (requires Lua script):**
1. Copy `scripts/hydra_osd.lua` to `APM/scripts/` on the FC's SD card
2. Set these ArduPilot parameters: `SCR_ENABLE=1`, `SCR_HEAP_SIZE=65536`, `OSD_TYPE=1`, `OSD1_ENABLE=1`
3. Set `[osd] mode = named_value` in config.ini
4. Reboot the FC

**Works with:** Matek H743, SpeedyBee F405-Wing, or any FC with an AT7456E/MAX7456 chip.
**Does not work with:** Pixhawk 6C (no OSD chip). Use the web dashboard or HDZero MSP DisplayPort instead.

Running HDZero? See the [HDZero OSD setup guide](docs/setup/hdzero-osd.mdx) for MSP DisplayPort wiring and parameters.

### Autonomous Strike

The autonomous controller can auto-engage targets when all qualification criteria are met simultaneously. Off by default.

All five criteria must pass:
1. Controller enabled in config.ini
2. Vehicle in an allowed mode (default: AUTO only)
3. Vehicle GPS inside the configured geofence (circle or polygon)
4. No strike in cooldown period
5. A tracked target matches: class in whitelist, confidence above threshold, tracked for N consecutive frames

```ini
[autonomous]
enabled = true
geofence_lat = 34.05
geofence_lon = -118.25
geofence_radius_m = 200.0
min_confidence = 0.85
min_track_frames = 5
allowed_classes = mine, buoy
strike_cooldown_sec = 30.0
allowed_vehicle_modes = AUTO
```

All autonomous actions are logged to `hydra.audit` for accountability.

### RF Homing

Hydra can autonomously locate RF signal sources using RSSI gradient ascent. Requires [Kismet](https://www.kismetwireless.net/) running on the companion computer with a monitor-mode WiFi adapter or RTL-SDR dongle.

The RF hunt runs as a background thread with four states: IDLE, SEARCHING, HOMING, CONVERGED.

- **WiFi mode** hunts a specific BSSID (MAC address)
- **SDR mode** hunts a specific frequency

```ini
[rf_homing]
enabled = true
mode = wifi
target_bssid = AA:BB:CC:DD:EE:FF
kismet_host = http://localhost:2501
search_pattern = lawnmower
search_area_m = 100.0
rssi_threshold_dbm = -80.0
rssi_converge_dbm = -40.0
```

The web dashboard includes a full RF hunt interface for configuring parameters, starting/stopping hunts, and monitoring RSSI in real time.

### Post-Mission Review

After a mission, export detection data to a standalone HTML map:

```bash
python -m hydra_detect.review_export /data/logs/detections.jsonl -o report.html
```

Or view directly from the web dashboard at `/review`.

## Configuration

Everything lives in `config.ini`. Full reference below.

### [camera]
| Key | Default | Description |
|-----|---------|-------------|
| `source` | `auto` | Camera source. `auto` picks the first webcam. Also accepts device index (`0`, `2`), RTSP URL, GStreamer pipeline, or file path |
| `width` | `640` | Capture width in pixels |
| `height` | `480` | Capture height in pixels |
| `fps` | `30` | Target frame rate |
| `hfov_deg` | `60.0` | Horizontal field of view in degrees (used to estimate target bearing) |

### [detector]
| Key | Default | Description |
|-----|---------|-------------|
| `yolo_model` | `yolov8n.pt` | YOLO model file (auto-downloads on first run) |
| `yolo_confidence` | `0.45` | Confidence threshold. Lower catches more, higher reduces false positives |
| `yolo_classes` | *(all)* | Comma-separated COCO class IDs to detect (empty = everything) |

### [tracker]
| Key | Default | Description |
|-----|---------|-------------|
| `track_thresh` | `0.5` | Minimum confidence to start a new track |
| `track_buffer` | `30` | Frames to keep a lost track alive before dropping it |
| `match_thresh` | `0.8` | IoU threshold for matching detections to existing tracks |

### [mavlink]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Turn MAVLink on or off |
| `connection_string` | `/dev/ttyACM0` | Serial device or `udp:127.0.0.1:14550` |
| `baud` | `115200` | Serial baud rate |
| `source_system` | `1` | MAVLink system ID |
| `min_gps_fix` | `3` | Minimum GPS fix type required (3 = 3D fix) |
| `alert_statustext` | `true` | Send detection alerts as STATUSTEXT messages |
| `alert_interval_sec` | `5.0` | Minimum seconds between repeat alerts for the same object class |
| `severity` | `2` | MAVLink severity level (0=Emergency through 7=Debug) |
| `auto_loiter_on_detect` | `false` | Automatically switch to LOITER when something is detected |
| `guided_roi_on_detect` | `false` | Automatically point the gimbal at detections |
| `strike_distance_m` | `20.0` | How far ahead (metres) to project the strike waypoint |

### [web]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Turn the web dashboard on or off |
| `host` | `0.0.0.0` | Bind address |
| `port` | `8080` | HTTP port |
| `mjpeg_quality` | `70` | JPEG quality for the video stream (1-100) |

### [osd]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Turn FPV OSD overlay on or off (requires MAVLink + FC with OSD chip) |
| `mode` | `statustext` | `statustext` (simple) or `named_value` (requires Lua script on FC) |
| `update_interval` | `0.2` | Seconds between OSD updates. Lower is snappier but chattier on the MAVLink bus |

### [autonomous]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable autonomous strike controller |
| `geofence_lat` | `0.0` | Circle geofence center latitude |
| `geofence_lon` | `0.0` | Circle geofence center longitude |
| `geofence_radius_m` | `100.0` | Circle geofence radius in metres |
| `geofence_polygon` | *(empty)* | Polygon geofence as `lat,lon;lat,lon;...` (overrides circle) |
| `min_confidence` | `0.85` | Minimum detection confidence for auto-strike |
| `min_track_frames` | `5` | Consecutive frames a target must be tracked |
| `allowed_classes` | *(all)* | Comma-separated class labels allowed for auto-strike |
| `strike_cooldown_sec` | `30.0` | Seconds between autonomous strikes |
| `allowed_vehicle_modes` | `AUTO` | Vehicle must be in one of these modes |

### [rf_homing]
| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable RF source localization |
| `mode` | `wifi` | `wifi` (hunt by BSSID) or `sdr` (hunt by frequency) |
| `target_bssid` | *(empty)* | MAC address to locate (WiFi mode) |
| `target_freq_mhz` | `915.0` | Frequency in MHz to locate (SDR mode) |
| `kismet_host` | `http://localhost:2501` | Kismet REST API URL |
| `kismet_user` | `kismet` | Kismet username |
| `kismet_pass` | `kismet` | Kismet password |
| `search_pattern` | `lawnmower` | Search pattern: `lawnmower` or `spiral` |
| `search_area_m` | `100.0` | Search area size in metres |
| `search_spacing_m` | `20.0` | Grid spacing between search legs |
| `search_alt_m` | `15.0` | Search altitude in metres |
| `rssi_threshold_dbm` | `-80.0` | RSSI level to switch from search to homing |
| `rssi_converge_dbm` | `-40.0` | RSSI level to declare source found |
| `gradient_step_m` | `5.0` | Step size for gradient ascent |
| `poll_interval_sec` | `0.5` | RSSI polling interval |
| `arrival_tolerance_m` | `3.0` | Distance to consider a waypoint reached |

### [logging]
| Key | Default | Description |
|-----|---------|-------------|
| `log_dir` | `/data/logs` | Where detection logs go |
| `log_format` | `jsonl` | Log format: `csv` or `jsonl` |
| `save_images` | `true` | Save full-frame JPEG snapshots when something is detected |
| `image_dir` | `/data/images` | Where snapshots go |
| `image_quality` | `90` | JPEG quality for snapshots |
| `save_crops` | `false` | Save cropped images of detected objects |
| `crop_dir` | `/data/crops` | Where cropped images go |

## Project Layout

```
Hydra/
  config.ini                          # All settings
  requirements.txt                    # Python dependencies
  Dockerfile                          # Jetson container build
  scripts/
    hydra-detect.service              # systemd unit file
    jetson_preflight.sh               # Hardware sanity checks
    hydra_osd.lua                     # ArduPilot Lua script for FPV OSD
    setup_headless.sh                 # Headless field boot configuration
    setup_tailscale.sh                # Tailscale remote access setup
    hydra_sync.sh                     # One-command code sync to Jetsons

  hydra_detect/
    __init__.py
    __main__.py                       # Entry point (python -m hydra_detect)
    pipeline.py                       # Main loop: detect, track, alert, repeat
    camera.py                         # Thread-safe capture with auto-reconnect
    tracker.py                        # ByteTrack multi-object tracker
    overlay.py                        # Bounding boxes, HUD, target lock rendering
    osd.py                            # FPV OSD overlay via MAVLink
    mavlink_io.py                     # MAVLink connection, alerts, vehicle commands
    detection_logger.py               # CSV/JSONL logging with image snapshots
    autonomous.py                     # Geofenced autonomous strike controller
    review_export.py                  # Post-mission review: CLI + standalone HTML export

    detectors/
      __init__.py
      base.py                         # Abstract detector interface
      yolo_detector.py                # YOLOv8/v11 via ultralytics

    rf/
      __init__.py
      hunt.py                         # RF hunt state machine (IDLE->SEARCHING->HOMING->CONVERGED)
      kismet_client.py                # Kismet REST API client for RSSI polling
      navigator.py                    # Waypoint navigation for search patterns
      search.py                       # Lawnmower and spiral search pattern generators
      signal.py                       # RSSI filtering and gradient analysis

    web/
      __init__.py
      server.py                       # FastAPI: REST API + MJPEG stream
      templates/
        index.html                    # Operator dashboard (includes RF hunt UI)
        review.html                   # Post-mission review map
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Operator dashboard |
| `GET` | `/stream.mjpeg` | Live MJPEG video stream |
| `GET` | `/api/stats` | Pipeline stats (FPS, tracks, GPS, etc.) |
| `GET` | `/api/config` | Current runtime configuration |
| `POST` | `/api/config/prompts` | Update detection prompts |
| `POST` | `/api/config/threshold` | Update confidence threshold |
| `GET` | `/api/detections` | Recent detection log entries |
| `GET` | `/api/tracks` | Currently active tracked objects |
| `GET` | `/api/target` | Current target lock state |
| `POST` | `/api/target/lock` | Lock a track for keep-in-frame (`{"track_id": 5}`) |
| `POST` | `/api/target/unlock` | Release target lock |
| `POST` | `/api/target/strike` | Send strike command (`{"track_id": 5, "confirm": true}`) |
| `POST` | `/api/vehicle/loiter` | Command vehicle to hold position |
| `GET` | `/api/rf/status` | Current RF hunt state and RSSI readings |
| `POST` | `/api/rf/start` | Start an RF hunt with given parameters |
| `POST` | `/api/rf/stop` | Stop the active RF hunt |
| `GET` | `/review` | Post-mission review map page |
| `GET` | `/api/review/logs` | List available detection log files |
| `GET` | `/api/review/log/{filename}` | Parse and return detection data from a log file |
| `POST` | `/api/pipeline/stop` | Gracefully stop the pipeline |
| `POST` | `/api/pipeline/pause` | Pause or resume detection (`{"paused": true}`) |

Control endpoints (POST routes for target, vehicle, pipeline, and RF) require bearer token authentication.

## Vehicle Compatibility

| Platform | Hold mode | Yaw control | Strike mode |
|----------|-----------|-------------|-------------|
| Drones (ArduCopter) | LOITER | CONDITION_YAW | GUIDED waypoint |
| Boats (ArduRover, boat mode) | HOLD | Rudder steering | GUIDED waypoint |
| Rovers (ArduRover) | HOLD | Steering | GUIDED waypoint |

If it supports GUIDED mode and CONDITION_YAW, Hydra will work with it. The system auto-detects whether to use LOITER or HOLD from the vehicle's mode mapping.

## Dependencies

- Python 3.10+
- OpenCV (headless)
- [ultralytics](https://github.com/ultralytics/ultralytics) (YOLO)
- [supervision](https://github.com/roboflow/supervision) (ByteTrack)
- [pymavlink](https://github.com/ArduPilot/pymavlink) + pyserial
- FastAPI + uvicorn
- Optional: `mgrs` for military grid coordinates
- Optional: `requests` (for Kismet REST API in RF homing mode)
- Optional: [Kismet](https://www.kismetwireless.net/) (for RF source localization)
