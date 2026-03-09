# Hydra Detect v2.0

Modular aerial detection and tracking system for drone/UAV platforms. Combines real-time object detection with multi-object tracking, MAVLink vehicle integration, and a live web dashboard.

## Architecture

```
Camera → Detector (YOLO / NanoOWL) → ByteTrack Tracker → MAVLink Alerts
                                                        → Detection Logger
                                                        → Web UI (MJPEG)
```

### Modules

| Module | File | Description |
|--------|------|-------------|
| Camera | `hydra_detect/camera.py` | Thread-safe capture with auto-reconnect (USB, RTSP, file) |
| Detectors | `hydra_detect/detectors/` | Swappable back-ends — YOLOv8 and NanoOWL/OWL-ViT |
| Tracker | `hydra_detect/tracker.py` | ByteTrack multi-object tracking via supervision |
| MAVLink | `hydra_detect/mavlink_io.py` | Connection, STATUSTEXT alerts, LOITER/ROI commands |
| Logger | `hydra_detect/detection_logger.py` | CSV/JSON logging with optional image crops |
| Overlay | `hydra_detect/overlay.py` | Bounding box + HUD renderer |
| Web UI | `hydra_detect/web/` | FastAPI dashboard with MJPEG stream |
| Pipeline | `hydra_detect/pipeline.py` | Main orchestrator tying everything together |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Edit config
vim config.ini

# Run
python -m hydra_detect --config config.ini
```

Open `http://localhost:8080` for the operator dashboard.

## Docker

```bash
docker build -t hydra-detect .
docker run --rm --device /dev/video0 -p 8080:8080 hydra-detect
```

## systemd Service

```bash
sudo cp scripts/hydra-detect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-detect
```

## Configuration

All settings live in `config.ini`. Key sections:

- **[camera]** — source (device index, RTSP URL, or file), resolution, FPS
- **[detector]** — engine (`yolo` or `nanoowl`), model paths, thresholds
- **[tracker]** — ByteTrack parameters
- **[mavlink]** — connection string, alert settings, vehicle commands
- **[web]** — dashboard host/port, MJPEG quality
- **[logging]** — log directory, format (CSV/JSON), crop saving
