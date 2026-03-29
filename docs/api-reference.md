# API Reference

All endpoints are served by FastAPI on port 8080. Control endpoints (POST/DELETE) require a Bearer token in the `Authorization` header unless noted otherwise. The token is auto-generated on first boot and stored in `config.ini [web] api_token`.

```
Authorization: Bearer <token>
```

Rate limiting: 50 failed auth attempts per IP per 60-second window triggers a lockout (HTTP 429).

---

## Health and Preflight

### GET /api/health

Lightweight health check. Returns 200 if processing frames, 503 if stalled.

**Auth**: No

**Response**:
```json
{"healthy": true, "camera_ok": true, "fps": 12.3}
```

### GET /api/preflight

Run pre-flight checks. Returns structured results for camera, MAVLink, GPS, config, models, and disk.

**Auth**: No

**Response**:
```json
{"checks": [...], "overall": "pass"}
```

---

## Stream

### GET /stream.mjpeg

MJPEG video stream. Connect from an `<img>` tag or VLC.

**Auth**: No

**Content-Type**: `multipart/x-mixed-replace; boundary=frame`

### GET /api/stream/quality

Current MJPEG quality setting.

**Auth**: No

**Response**: `{"quality": 70}`

### POST /api/stream/quality

Set MJPEG quality. **Auth**: Yes

**Body**: `{"quality": 50}` (int, 1-100)

---

## Detection and Tracking

### GET /api/stats

Pipeline statistics: FPS, inference time, active tracks, total detections, MAVLink status, GPS.

**Auth**: No

**Response**:
```json
{
  "fps": 12.3, "inference_ms": 35.2, "active_tracks": 3,
  "total_detections": 1542, "detector": "yolo",
  "mavlink": true, "gps_fix": 3, "position": {"lat": 34.05, "lon": -118.24}
}
```

### GET /api/detections

Recent detection log entries (ring buffer).

**Auth**: No

### GET /api/tracks

Currently active tracked objects with track ID, label, confidence, and bounding box.

**Auth**: No

### GET /api/target

Current target lock state.

**Auth**: No

**Response**: `{"locked": false, "track_id": null, "mode": null, "label": null}`

### POST /api/target/lock

Lock a track for keep-in-frame. **Auth**: Yes

**Body**: `{"track_id": 5}`

### POST /api/target/unlock

Release target lock. **Auth**: Yes

### POST /api/target/strike

One-shot strike command. Requires confirmation. **Auth**: Yes

**Body**: `{"track_id": 5, "confirm": true}`

---

## Approach Controller

### GET /api/approach/status

Current approach mode and state.

**Auth**: No

**Response**: `{"mode": "idle", "active": false}`

### POST /api/approach/follow/{track_id}

Start follow mode. **Auth**: Yes

### POST /api/approach/drop/{track_id}

Start drop approach. Requires confirmation. **Auth**: Yes

**Body**: `{"confirm": true}`

### POST /api/approach/strike/{track_id}

Start continuous strike approach. Requires confirmation. **Auth**: Yes

**Body**: `{"confirm": true}`

### POST /api/approach/abort

Abort current approach. Safes all channels, sets abort mode. **Auth**: Yes

### POST /api/abort

Emergency abort. Sets vehicle to RTL, then LOITER, then HOLD (first success).

**Auth**: No (intentionally unauthenticated for instructor safety override)

---

## Vehicle Mode

### POST /api/vehicle/loiter

Command vehicle to hold position (LOITER or HOLD). **Auth**: Yes

### POST /api/vehicle/mode

Set vehicle flight mode. **Auth**: Yes

**Body**: `{"mode": "AUTO"}`

**Allowed modes**: AUTO, RTL, LOITER, HOLD, GUIDED

---

## Camera

### GET /api/camera/sources

List available video sources (V4L2 devices).

**Auth**: No

### POST /api/camera/switch

Switch camera source at runtime. **Auth**: Yes

**Body**: `{"source": 2}` (device index or RTSP URL)

---

## Models

### GET /api/models

List available YOLO model files in the models directory.

**Auth**: No

### POST /api/models/switch

Switch YOLO model at runtime. **Auth**: Yes

**Body**: `{"model": "yolov8s.pt"}`

---

## Profiles

### GET /api/profiles

List loaded mission profiles from `profiles.json`.

**Auth**: No

### POST /api/profiles/switch

Switch to a mission profile. **Auth**: Yes

**Body**: `{"profile": "counter-uas"}`

### GET /api/mission-profiles

List built-in mission profile presets (RECON, DELIVERY, STRIKE).

**Auth**: No

---

## Configuration

### GET /api/config

Current runtime configuration (threshold, auto_loiter, etc.).

**Auth**: No

### POST /api/config/prompts

Update detection prompt labels. **Auth**: Yes

**Body**: `{"prompts": ["person", "car", "dog"]}` (max 20, each max 200 chars)

### POST /api/config/threshold

Update detection confidence threshold. **Auth**: Yes

**Body**: `{"threshold": 0.5}` (float, 0.0-1.0)

### GET /api/config/alert-classes

Current alert class filter and available classes grouped by tactical category.

**Auth**: No

### POST /api/config/alert-classes

Update alert class filter. **Auth**: Yes

**Body**: `{"classes": ["person", "car"]}` (empty list = all classes)

### GET /api/config/full

Full config.ini as JSON. Sensitive fields redacted. **Auth**: Yes

### POST /api/config/full

Update config.ini fields. Returns fields requiring restart. **Auth**: Yes

**Body**: `{"camera": {"source": "0"}, "detector": {"yolo_confidence": "0.5"}}`

**Max body**: 64KB

### POST /api/config/restore-backup

Restore config.ini from boot-time backup. **Auth**: Yes

### POST /api/config/factory-reset

Restore `config.ini.factory` defaults and restart pipeline. **Auth**: Yes

### GET /api/config/export

Export current config as JSON. **Auth**: Yes

### POST /api/config/import

Import config from uploaded JSON. **Auth**: Yes

---

## TAK

### GET /api/tak/status

TAK CoT output status: enabled, running, callsign, events sent.

**Auth**: No

### POST /api/tak/toggle

Start or stop TAK output. **Auth**: Yes

**Body**: `{"enabled": true}`

### GET /api/tak/targets

List current TAK unicast targets.

**Auth**: No

### POST /api/tak/targets

Add a unicast target. **Auth**: Yes

**Body**: `{"host": "<TAK_TARGET_IP>", "port": 6969}`

### DELETE /api/tak/targets

Remove a unicast target. **Auth**: Yes

**Body**: `{"host": "<TAK_TARGET_IP>", "port": 6969}`

---

## RF Hunt

### GET /api/rf/status

Current RF hunt state and target info.

**Auth**: No

### GET /api/rf/rssi_history

RSSI sample history for visualization.

**Auth**: No

### POST /api/rf/start

Start an RF hunt. All fields optional (unset fields keep current config). **Auth**: Yes

**Body**:
```json
{
  "mode": "wifi",
  "target_bssid": "AA:BB:CC:DD:EE:FF",
  "search_pattern": "lawnmower",
  "search_area_m": 100.0,
  "search_spacing_m": 20.0,
  "search_alt_m": 15.0,
  "rssi_threshold_dbm": -80.0,
  "rssi_converge_dbm": -40.0,
  "gradient_step_m": 5.0
}
```

### POST /api/rf/stop

Stop active RF hunt. **Auth**: Yes

---

## RTSP

### GET /api/rtsp/status

RTSP server status: enabled, running, URL, client count.

**Auth**: No

### POST /api/rtsp/toggle

Start or stop RTSP server. **Auth**: Yes

**Body**: `{"enabled": true}`

---

## MAVLink Video

### GET /api/mavlink-video/status

MAVLink video status: enabled, running, resolution, quality, FPS, bandwidth.

**Auth**: No

### POST /api/mavlink-video/toggle

Start or stop MAVLink video. **Auth**: Yes

**Body**: `{"enabled": true}`

### POST /api/mavlink-video/tune

Live-tune MAVLink video parameters. All fields optional. **Auth**: Yes

**Body**: `{"width": 160, "height": 120, "quality": 20, "max_fps": 2.0}`

---

## Events and Mission

### GET /api/events

Event timeline for current or most recent mission.

**Auth**: No

### GET /api/events/status

Mission status: active, mission name.

**Auth**: No

### POST /api/mission/start

Start a named mission. **Auth**: Yes

**Body**: `{"name": "patrol-alpha"}`

### POST /api/mission/end

End the current mission. **Auth**: Yes

---

## Review and Export

### GET /api/review/logs

List available detection log files and event timeline files.

**Auth**: No

### GET /api/review/log/{filename}

Parse and return detection data from a log file.

**Auth**: No

### GET /api/review/events/{filename}

Return events from an event timeline JSONL file.

**Auth**: No

### GET /api/review/images/{filename}

Serve a saved detection image.

**Auth**: No

### GET /api/export

Export logs and images as a ZIP download. **Auth**: Yes

---

## Setup

### GET /api/setup/devices

List available cameras and serial ports for setup wizard.

**Auth**: No

### POST /api/setup/save

Save setup wizard configuration and restart. **Auth**: Yes (skipped on first boot when no token exists)

**Body**:
```json
{
  "camera_source": "0",
  "serial_port": "/dev/ttyTHS1",
  "vehicle_type": "usv",
  "team_number": "1",
  "callsign": "HYDRA-1-USV"
}
```

---

## System

### GET /api/system/power-modes

List available Jetson power modes.

**Auth**: No

### POST /api/system/power-mode

Set Jetson power mode. **Auth**: Yes

**Body**: `{"mode_id": 0}`

### GET /api/logs

Tail the application log file. **Auth**: No

**Query params**: `lines` (int, 1-500, default 50), `level` (DEBUG/INFO/WARNING/ERROR/CRITICAL, default INFO)

**Response**: Array of log entries with timestamp, level, module, message.

### POST /api/restart

Restart the detection pipeline. **Auth**: Yes

### POST /api/pipeline/stop

Gracefully stop the pipeline and shut down. **Auth**: Yes

### POST /api/pipeline/pause

Pause or resume detection. **Auth**: Yes

**Body**: `{"paused": true}`

---

## Pages (HTML)

| Path | Description |
|------|-------------|
| `GET /` | Operator dashboard SPA |
| `GET /control` | Mobile operator control page |
| `GET /instructor` | Instructor multi-vehicle overview |
| `GET /review` | Post-mission review map |
| `GET /setup` | First-boot setup wizard |
