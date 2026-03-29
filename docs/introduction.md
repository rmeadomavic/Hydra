---
title: "Hydra Detect"
sidebarTitle: "Introduction"
description: "Real-time object detection and tracking for uncrewed vehicles running ArduPilot on NVIDIA Jetson."
keywords:
  - object detection
  - YOLO
  - ArduPilot
  - Jetson
  - drone
  - UGV
  - USV
  - MAVLink
  - ByteTrack
  - FPV OSD
---

# Hydra Detect v2.0

Hydra is a real-time object detection and tracking payload for uncrewed vehicles. Drones, boats, rovers. If it runs [ArduPilot](https://ardupilot.org/) and has a camera, Hydra processes the feed and pushes detection data to your ground control station over MAVLink.

It runs on an NVIDIA Jetson (or any Linux box with a camera and a MAVLink radio) and integrates with your existing flight stack. No firmware changes required.

Built by [SORCC](https://github.com/rmeadomavic/Hydra).

## Architecture

```
Camera -> Detector (YOLO) -> ByteTrack -> MAVLink Alerts
                                               -> Target Lock / Strike
                                               -> Autonomous Strike Controller
                                               -> FPV OSD (via FC OSD chip)
                                               -> Web Dashboard (MJPEG)
                                               -> Detection Logger

Kismet (WiFi/SDR) -> RF Hunt Controller -> RSSI Gradient Ascent -> MAVLink Nav
```

Camera frames flow through YOLO for detection, ByteTrack for persistent multi-object tracking, then fan out to every downstream consumer simultaneously. RF homing runs as an independent pipeline using Kismet for RSSI data.

## Capabilities

<Columns cols={2}>

<Card title="Object detection" icon="eye">
  YOLOv8 running in real-time on-device via CUDA/TensorRT. Configurable confidence thresholds and class filters.
</Card>

<Card title="Multi-object tracking" icon="fingerprint">
  ByteTrack keeps persistent IDs across frames. Object #5 stays object #5 through occlusions and re-entries.
</Card>

<Card title="MAVLink alerts" icon="satellite-dish">
  STATUSTEXT alerts sent to Mission Planner or QGroundControl with GPS coordinates and class labels.
</Card>

<Card title="Target lock" icon="crosshairs">
  Lock onto a tracked target and the vehicle yaws to keep it centered in the camera field of view.
</Card>

<Card title="Strike mode" icon="location-arrow">
  Estimates target GPS from vehicle heading and camera offset, then navigates in GUIDED mode.
</Card>

<Card title="RF homing" icon="wifi">
  Locates WiFi APs or SDR signals using Kismet RSSI and gradient ascent navigation. Supports lawnmower and spiral search patterns.
</Card>

<Card title="Autonomous strike" icon="bolt">
  Auto-engage controller with geofencing (circle or polygon), class whitelists, confidence gates, and cooldown timers. Off by default.
</Card>

<Card title="Mission review" icon="map">
  Post-flight web map with detection markers, track trails, confidence filters, and class filtering over OpenStreetMap.
</Card>

<Card title="Web dashboard" icon="display">
  Live annotated MJPEG video stream with bounding boxes, track IDs, and target lock overlays.
</Card>

<Card title="FPV OSD" icon="vr-cardboard">
  Detection telemetry overlaid on your FPV feed through the flight controller's OSD chip. No extra hardware needed.
</Card>

</Columns>

## Supported vehicles

Hydra works with any ArduPilot vehicle that supports GUIDED mode:

| Platform | Hold mode | Yaw control | Strike mode |
|----------|-----------|-------------|-------------|
| **Drones** (ArduCopter) | LOITER | CONDITION_YAW | GUIDED waypoint |
| **Boats** (ArduRover, boat frame) | HOLD | Rudder steering | GUIDED waypoint |
| **Rovers** (ArduRover) | HOLD | Steering | GUIDED waypoint |

## Hardware requirements

- **Compute:** NVIDIA Jetson Orin Nano (recommended), any Jetson with JetPack 6.x, or Linux x86 with CUDA
- **Camera:** USB webcam, RTSP IP camera, or GStreamer pipeline
- **MAVLink radio:** Serial (USB or UART) or UDP connection to the flight controller
- **Optional:** Monitor-mode WiFi adapter or RTL-SDR dongle (for RF homing)

## Next steps

<CardGroup cols={2}>

<Card title="Quickstart" icon="rocket" href="/quickstart">
  Install Hydra and get the dashboard running in a few minutes.
</Card>

<Card title="Jetson setup" icon="microchip" href="/setup/jetson-flash">
  Flash and configure a Jetson Orin Nano from scratch.
</Card>

<Card title="Configuration reference" icon="gear" href="/reference/configuration">
  Every config.ini parameter documented.
</Card>

<Card title="API reference" icon="code" href="/reference/api">
  REST endpoints for the web dashboard and automation.
</Card>

</CardGroup>
