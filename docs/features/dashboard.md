---
title: "Web dashboard"
sidebarTitle: "Dashboard"
icon: "browser"
description: "Live operator dashboard with video stream, pipeline stats, and vehicle controls"
---

The Hydra Detect web dashboard is the primary operator interface. It runs on **port 8080** and is accessible from any device on the same network: laptop, tablet, or phone.

```
http://<jetson-ip>:8080
```

## Dashboard layout

The dashboard is split into a full-width video panel on the left and a control sidebar on the right.

| Section | What it shows |
|---------|--------------|
| **Video Stream** | Live MJPEG feed with bounding boxes, track IDs, and target lock overlays |
| **Pipeline Stats** | FPS, inference time, active tracks, total detections, detector engine |
| **Vehicle Link** | MAVLink connection status, GPS fix, position (MGRS or lat/lon) |
| **Target Control** | Active track list with Keep in Frame / Strike / Release buttons |
| **Detection Config** | Confidence threshold slider, adjustable without restarting |
| **Detection Log** | Scrolling feed of recent detections with timestamps and coordinates |

## Video stream

The main panel displays a live MJPEG stream served from `/stream.mjpeg`. Bounding boxes are drawn around every detected object with its class label, confidence score, and ByteTrack ID. When a target is locked, the overlay switches to a target-lock reticle.

Stream quality is configurable:

```ini
[web]
mjpeg_quality = 70
```

## Pipeline stats

The stats panel updates in real time:

- **FPS**: frames processed per second by the detection pipeline
- **Inference**: model inference time in milliseconds
- **Tracks**: number of currently active ByteTrack tracks
- **Detections**: total detection count since startup
- **Engine**: which detector backend is running (e.g. YOLOv8s, TensorRT)

These values are also available via the [`GET /api/stats`](/reference/api) endpoint.

## Vehicle link

Shows the MAVLink connection state and current vehicle telemetry: connection status, GPS fix type and satellite count, vehicle position in MGRS or lat/lon format, and current flight mode.

Requires MAVLink to be enabled in `config.ini`:

```ini
[mavlink]
enabled = true
connection_string = /dev/ttyACM0
baud = 115200
```

## Target control

Lists all currently tracked objects. From here you can interact with individual targets using three modes:

- **Keep in Frame**: yaw the vehicle to keep the selected target centered in the camera
- **Strike**: navigate the vehicle toward the target's estimated GPS position
- **Release**: unlock the current target and return to passive tracking

For a detailed walkthrough of each mode, see [Target control](/features/target-control).

## Detection config

The confidence threshold slider adjusts the YOLO detection threshold on the fly. Lowering it catches more objects but may introduce false positives. Raising it filters out low-confidence detections.

Changes take effect immediately, no restart required. Maps to the `POST /api/config/threshold` endpoint.

## Detection log

The bottom of the sidebar shows a scrolling feed of recent detections. Each entry includes timestamp, object class and confidence score, GPS coordinates (when available), and track ID.

Also available as JSON via [`GET /api/detections`](/reference/api).

## Configuration

```ini
[web]
enabled = true
host = 0.0.0.0
port = 8080
mjpeg_quality = 70
```

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Turn the web dashboard on or off |
| `host` | `0.0.0.0` | Bind address |
| `port` | `8080` | HTTP port |
| `mjpeg_quality` | `70` | JPEG quality for the video stream (1-100) |
