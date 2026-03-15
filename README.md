# Hydra Detect v2.0

Hydra is a real-time object detection and tracking system for uncrewed vehicles. If you're running a drone, boat, or rover on [ArduPilot](https://ardupilot.org/) and you want it to see the world, this is the payload software that makes that happen.

It runs on an NVIDIA Jetson (or really any Linux box with a camera and a MAVLink radio) and hooks into your existing flight stack without any firmware changes.

```
Camera ─> Detector (YOLO) ─> ByteTrack ─> MAVLink Alerts
                                                   ─> Target Lock / Strike
                                                   ─> FPV OSD (via FC OSD chip)
                                                   ─> Web Dashboard (MJPEG)
                                                   ─> Detection Logger
```

## What Can It Do?

- **See things** — YOLOv8 object detection running in real-time on-device
- **Remember them** — ByteTrack keeps persistent IDs across frames, so object #5 stays object #5
- **Tell you about them** — sends MAVLink STATUSTEXT alerts to Mission Planner / QGroundControl with GPS coordinates
- **Keep them in frame** — lock onto a tracked target and the vehicle will yaw to keep it centered in the camera
- **Go to them** — strike mode navigates the vehicle toward a target's estimated GPS position
- **Log everything** — timestamps, GPS, confidence scores, and optional image snapshots
- **Show up on your goggles** — detection data overlaid on your FPV feed through the flight controller's OSD chip
- **Stream to a browser** — live annotated video over MJPEG, accessible from any device on the network

## Getting Started

```bash
# Grab the code
git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra
pip install -r requirements.txt

# Point it at your hardware
vim config.ini

# Fire it up
python -m hydra_detect --config config.ini
```

Then open **http://localhost:8080** in a browser — that's your operator dashboard.

### Running on a Jetson (Docker)

The easiest way to get Hydra running on a Jetson is through Docker. The base image is big (~6 GB) but it comes with CUDA, PyTorch, and TensorRT ready to go.

```bash
# Grab the base image (one-time download)
docker pull dustynv/l4t-pytorch:r36.4.0

# Build and run
docker build --network=host -t hydra-detect .
docker run --rm --privileged --runtime nvidia \
  --device /dev/video0 --device /dev/video2 \
  --device /dev/ttyACM0 \
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

For a full walkthrough (flashing the Jetson, wiring, first boot), see the [Jetson setup guide](docs/jetson-setup-guide.md).

### Preflight Check (Jetson Orin Nano Super)

Before you head to the field, run the preflight script to make sure the Jetson is happy:

```bash
./scripts/jetson_preflight.sh
```

For best performance, run these once after boot:

```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```

If preflight passes, you're good to go — just make sure your camera and MAVLink wiring match what's in `config.ini`.

### Auto-Start on Boot

Want Hydra to start automatically when the Jetson powers on?

```bash
sudo cp scripts/hydra-detect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-detect
```

## The Dashboard

The web dashboard lives at port 8080 and gives you everything you need to operate:

| Section | What You'll See |
|---------|----------------|
| **Video Stream** | Live MJPEG feed with bounding boxes, track IDs, and target lock overlays |
| **Pipeline Stats** | FPS, inference time, active tracks, total detections, detector engine |
| **Vehicle Link** | MAVLink connection status, GPS fix, position (MGRS or lat/lon) |
| **Target Control** | Active track list with Keep in Frame / Strike / Release buttons |
| **Detection Config** | Confidence threshold slider — tweak it live |
| **Detection Log** | Scrolling feed of recent detections with timestamps and coordinates |

### Controlling the Vehicle

**Hold Position** — Tells the vehicle to stop and hold where it is. Uses `LOITER` for drones, `HOLD` for rovers and boats.

**Keep in Frame** — Pick a tracked object and the pipeline will send yaw corrections every frame to keep it centered. Works on any ArduPilot vehicle.

**Strike** — This is the big one. Pick a target, click Strike, and confirm in the popup. Here's what happens:

1. Hydra estimates the target's GPS position from vehicle heading + camera offset
2. Switches the vehicle to `GUIDED` mode
3. Sends a waypoint via `SET_POSITION_TARGET_GLOBAL_INT`
4. Keeps tracking and yawing during the approach
5. Sends STATUSTEXT alerts to your GCS so you know what's happening

You always have override through Mission Planner or any other GCS.

### FPV OSD Overlay

If your flight controller has an onboard analog OSD chip (MAX7456 / AT7456E), Hydra can push detection telemetry right onto your FPV feed over MAVLink. No extra hardware, no video passthrough, no added latency.

Here's what it looks like on your goggles:

```
T:3 12fps 35ms LK#5TRK
```

That's: track count, pipeline FPS, inference time, and locked target status — all composited with sub-millisecond delay.

**Works with:** Matek H743, SpeedyBee F405-Wing, or any FC with an AT7456E/MAX7456 chip.
**Won't work with:** Pixhawk 6C (no OSD chip) — use the web dashboard overlay instead.

There are two modes depending on how much setup you want to do:

| Mode | Effort | What You Get |
|------|--------|-------------|
| `statustext` | Just flip a config flag | Detection info in the OSD message panel |
| `named_value` | Copy a Lua script to the FC | Richer display with stale-link warnings |

**The easy way (statustext):**
```ini
[osd]
enabled = true
mode = statustext
```

**The fancy way (named_value with Lua):**
1. Copy `scripts/hydra_osd.lua` to `APM/scripts/` on the FC's SD card
2. Set these ArduPilot parameters: `SCR_ENABLE=1`, `SCR_HEAP_SIZE=65536`, `OSD_TYPE=1`, `OSD1_ENABLE=1`
3. Add to `config.ini`:
   ```ini
   [osd]
   enabled = true
   mode = named_value
   ```
4. Reboot the FC

**Running HDZero?** Check out the [HDZero OSD setup guide](docs/hdzero-osd-setup.md) for MSP DisplayPort wiring and parameters.

## Configuration

Everything lives in `config.ini`. Here's the full reference:

### [camera]
| Key | Default | What It Does |
|-----|---------|--------------|
| `source` | `0` | Camera source — device index (`0`), RTSP URL, GStreamer pipeline, or file path |
| `width` | `640` | Capture width in pixels |
| `height` | `480` | Capture height in pixels |
| `fps` | `30` | Target frame rate |
| `hfov_deg` | `60.0` | Horizontal field of view in degrees (used to estimate target bearing) |

### [detector]
| Key | Default | What It Does |
|-----|---------|--------------|
| `yolo_model` | `yolov8n.pt` | YOLO model file (auto-downloads on first run) |
| `yolo_confidence` | `0.45` | Confidence threshold — lower catches more, higher reduces false positives |
| `yolo_classes` | *(all)* | Comma-separated COCO class IDs to detect (empty = everything) |

### [tracker]
| Key | Default | What It Does |
|-----|---------|--------------|
| `track_thresh` | `0.5` | Minimum confidence to start a new track |
| `track_buffer` | `30` | Frames to keep a lost track alive before dropping it |
| `match_thresh` | `0.8` | IoU threshold for matching detections to existing tracks |

### [mavlink]
| Key | Default | What It Does |
|-----|---------|--------------|
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
| Key | Default | What It Does |
|-----|---------|--------------|
| `enabled` | `true` | Turn the web dashboard on or off |
| `host` | `0.0.0.0` | Bind address |
| `port` | `8080` | HTTP port |
| `mjpeg_quality` | `70` | JPEG quality for the video stream (1–100) |

### [osd]
| Key | Default | What It Does |
|-----|---------|--------------|
| `enabled` | `false` | Turn FPV OSD overlay on or off (needs MAVLink + FC with OSD chip) |
| `mode` | `statustext` | `statustext` (simple) or `named_value` (needs Lua script on FC) |
| `update_interval` | `0.2` | Seconds between OSD updates — lower is snappier but chattier on the MAVLink bus |

### [logging]
| Key | Default | What It Does |
|-----|---------|--------------|
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
  config.ini                          # All your settings
  requirements.txt                    # Python dependencies
  Dockerfile                          # Jetson container build
  scripts/
    hydra-detect.service              # systemd unit file
    jetson_preflight.sh               # Hardware sanity checks
    hydra_osd.lua                     # ArduPilot Lua script for FPV OSD

  hydra_detect/
    __init__.py
    __main__.py                       # Entry point (python -m hydra_detect)
    pipeline.py                       # The main loop — detect, track, alert, repeat
    camera.py                         # Thread-safe capture with auto-reconnect
    tracker.py                        # ByteTrack multi-object tracker
    overlay.py                        # Bounding boxes + HUD + target lock rendering
    osd.py                            # FPV OSD overlay via MAVLink
    mavlink_io.py                     # MAVLink connection, alerts, vehicle commands
    detection_logger.py               # CSV/JSONL logging with image snapshots

    detectors/
      __init__.py
      base.py                         # Abstract detector interface
      yolo_detector.py                # YOLOv8/v11 via ultralytics

    web/
      __init__.py
      server.py                       # FastAPI — REST API + MJPEG stream
      templates/
        index.html                    # Operator dashboard
```

## API Reference

| Method | Path | What It Does |
|--------|------|--------------|
| `GET` | `/` | Serves the operator dashboard |
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

## Vehicle Compatibility

**Drones (ArduCopter)** — Uses `LOITER` for holding, `CONDITION_YAW` for tracking, `GUIDED` for strike. Works best with a gimbal for ROI targeting.

**Boats (ArduRover, boat mode)** — Uses `HOLD` mode. Yaw commands steer the rudder. `GUIDED` drives toward the target. Tune `strike_distance_m` for your water speed.

**Rovers (ArduRover)** — Same as boats. Yaw commands turn the vehicle. Make sure there's a clear path before sending a strike.

**Anything else on ArduPilot** — If it supports `GUIDED` mode and `CONDITION_YAW`, Hydra will work with it. The system auto-detects whether to use `LOITER` or `HOLD` from the vehicle's mode mapping.

## Dependencies

- Python 3.10+
- OpenCV (headless)
- [ultralytics](https://github.com/ultralytics/ultralytics) (YOLO)
- [supervision](https://github.com/roboflow/supervision) (ByteTrack)
- [pymavlink](https://github.com/ArduPilot/pymavlink) + pyserial
- FastAPI + uvicorn
- Optional: `mgrs` for military grid coordinates
