# Implementation Plan: MAVLink Commands, Autonomous Strike, Mission Review

## Overview

Three features to enable full beyond-visual-range USV operations:

1. **MAVLink Command Path** — Strike/lock/unlock over telemetry radio (no WiFi needed)
2. **Autonomous Strike Controller** — Geofenced auto-engage with qualification criteria
3. **Post-Mission Review Tool** — Web page + CLI script for reviewing detection logs on a map

---

## Feature 1: MAVLink Command Path

### Goal
Allow operators to send target lock, strike, and unlock commands from Mission Planner over RFD 900x radio using MAV_CMD_USER commands and NAMED_VALUE_INT messages.

### Files Modified
- `hydra_detect/mavlink_io.py` — Add command listener thread
- `hydra_detect/pipeline.py` — Wire MAVLink command callbacks
- `config.ini` — Add `mavlink_commands_enabled` setting

### Design

**MAV_CMD_USER approach (works from MP "Send Command" tab):**
- `MAV_CMD_USER_1` (31010): Target Lock → param1 = track_id
- `MAV_CMD_USER_2` (31011): Strike → param1 = track_id
- `MAV_CMD_USER_3` (31012): Unlock (no params)

**NAMED_VALUE_INT approach (for Lua scripts or custom GCS):**
- `HYDRA_LOCK` → value = track_id
- `HYDRA_STRIKE` → value = track_id
- `HYDRA_UNLOCK` → value = 0

**Implementation in mavlink_io.py:**
- New `_command_listener()` background thread (daemon, similar to `_gps_listener`)
- Listens for `COMMAND_LONG` and `NAMED_VALUE_INT` messages
- Calls registered command callbacks (set by pipeline)
- Sends `COMMAND_ACK` with result back to GCS
- Thread starts alongside GPS listener in `connect()`

**Pipeline integration:**
- `Pipeline.start()` registers command callbacks on MAVLinkIO:
  - `on_mavlink_lock(track_id)` → calls `_handle_target_lock(track_id)`
  - `on_mavlink_strike(track_id)` → calls `_handle_strike_command(track_id)`
  - `on_mavlink_unlock()` → calls `_handle_target_unlock()`
- STATUSTEXT confirmations already exist in those handlers

### New Tests
- `tests/test_mavlink_commands.py`:
  - Test command parsing for each MAV_CMD_USER
  - Test NAMED_VALUE_INT parsing
  - Test COMMAND_ACK sent back
  - Test invalid track_id handling
  - Test commands when callbacks not registered

---

## Feature 2: Autonomous Strike Controller

### Goal
When the vehicle is in AUTO mode, inside a geofence, and a target meets qualification criteria, automatically initiate a strike without operator confirmation.

### Files Created
- `hydra_detect/autonomous.py` — AutonomousController class
- `tests/test_autonomous.py` — Unit tests

### Files Modified
- `config.ini` — New `[autonomous]` section
- `hydra_detect/pipeline.py` — Integrate autonomous controller in main loop
- `hydra_detect/mavlink_io.py` — Add `get_vehicle_mode()` method

### Config Section
```ini
[autonomous]
enabled = false
; Geofence: circle defined by center + radius
geofence_lat = 0.0
geofence_lon = 0.0
geofence_radius_m = 100.0
; Geofence: polygon (semicolon-separated lat,lon pairs). Overrides circle if set.
; Example: 34.05,-118.25;34.06,-118.24;34.05,-118.23
geofence_polygon =
; Strike qualification criteria
min_confidence = 0.85
min_track_frames = 5
allowed_classes = mine,buoy,kayak
; Cooldown between autonomous strikes (seconds)
strike_cooldown_sec = 30.0
; Vehicle must be in one of these modes for autonomous strike
allowed_vehicle_modes = AUTO
```

### AutonomousController Design

```python
class AutonomousController:
    def __init__(self, config):
        # Parse geofence (circle or polygon)
        # Parse qualification criteria
        # Track persistence counter per track_id
        # Strike cooldown timer
        # Audit logger

    def evaluate(self, tracks, mavlink, pipeline_lock_cb, pipeline_strike_cb) -> None:
        """Called each frame from the pipeline loop. Evaluates all criteria."""
        # 1. Check enabled
        # 2. Check vehicle mode (must be AUTO or other allowed mode)
        # 3. Check vehicle inside geofence
        # 4. Check no strike already in progress (cooldown)
        # 5. For each track:
        #    a. Class in allowed list?
        #    b. Confidence >= threshold?
        #    c. Track seen for >= min_track_frames consecutive frames?
        # 6. If all criteria met for a track:
        #    a. Log autonomous decision
        #    b. Call lock callback
        #    c. Call strike callback
        #    d. Send STATUSTEXT alert
        #    e. Start cooldown timer
```

**Geofence implementation:**
- **Circle:** Haversine distance from vehicle GPS to center point. Inside if distance < radius.
- **Polygon:** Point-in-polygon using ray casting algorithm. Vertices defined as semicolon-separated lat,lon pairs in config.

**Track persistence:**
- Dict mapping `track_id → consecutive_frame_count`
- Incremented each frame the track appears
- Reset to 0 if track disappears for a frame
- Only qualify after `min_track_frames` consecutive appearances

**Vehicle mode check:**
- New `MAVLinkIO.get_vehicle_mode()` method
- Listens for `HEARTBEAT` messages from the FC (already received but not parsed)
- Extract `custom_mode` field and map to mode name
- Store in GPS-like state dict, read by autonomous controller

**Safety features:**
- Disabled by default (`enabled = false`)
- Geofence must be explicitly configured (0,0 center = invalid)
- Cooldown prevents rapid successive strikes
- Class whitelist prevents striking wrong objects
- High confidence threshold (0.85 default)
- Track persistence debounces false positives
- All autonomous decisions logged to `hydra.audit` logger with full context
- STATUSTEXT alert: `"AUTO-STRIKE: mine #4 @ 18SUJ1234567890"` sent to GCS

### Pipeline Integration Point
In `_run_loop()`, after tracking and MAVLink alerts, before overlay:
```python
if self._autonomous is not None:
    self._autonomous.evaluate(
        track_result, self._mavlink,
        self._handle_target_lock, self._handle_strike_command
    )
```

### Tests
- `tests/test_autonomous.py`:
  - `test_geofence_circle_inside` / `test_geofence_circle_outside`
  - `test_geofence_polygon_inside` / `test_geofence_polygon_outside`
  - `test_haversine_distance`
  - `test_qualification_all_criteria_met` → strike initiated
  - `test_qualification_low_confidence` → no strike
  - `test_qualification_wrong_class` → no strike
  - `test_qualification_insufficient_track_frames` → no strike
  - `test_qualification_outside_geofence` → no strike
  - `test_qualification_wrong_vehicle_mode` → no strike
  - `test_cooldown_enforced`
  - `test_disabled_by_default`
  - `test_polygon_parsing`

---

## Feature 3: Post-Mission Review Tool

### Goal
Two ways to review detection logs after a mission:
1. Web page on the Jetson (`/review`) with interactive Leaflet map
2. CLI script that generates a standalone HTML file for offline viewing

### Files Created
- `hydra_detect/web/templates/review.html` — Leaflet map review page
- `hydra_detect/review_export.py` — CLI script for standalone HTML export
- `tests/test_review.py` — Tests for log parsing and export

### Files Modified
- `hydra_detect/web/server.py` — Add review endpoints

### Web Review Page

**New endpoints in server.py:**
- `GET /review` — Serve the review HTML page
- `GET /api/review/logs` — List available JSONL/CSV log files in log_dir
- `GET /api/review/log/{filename}` — Parse and return detection data from a log file
- `GET /api/review/images/{filename}` — Serve saved detection images from image_dir

**Review page features (Leaflet.js via CDN):**
- OpenStreetMap tile layer (works offline with cached tiles)
- Detection markers placed at GPS coordinates
- Marker popup: label, confidence, timestamp, thumbnail image
- Color-coded markers by class
- Filter panel: filter by class label, minimum confidence slider
- Track trails: toggle to connect markers with same track_id as polylines
- Timeline slider: scrub through detections by timestamp
- Log file selector dropdown

### CLI Export Script

`python -m hydra_detect.review_export /data/logs/detections_20260315_120000.jsonl -o mission_report.html`

**Features:**
- Reads JSONL or CSV log file
- Embeds detection data as inline JSON in a self-contained HTML file
- Uses Leaflet.js CDN (requires internet to open) or optionally inlines the JS
- Generates summary statistics: total detections, unique classes, time range
- Outputs a single .html file you can SCP off and open anywhere
- Optional: `--images-dir` flag to embed thumbnail images as base64

### Tests
- `tests/test_review.py`:
  - Test JSONL log parsing
  - Test CSV log parsing
  - Test log file listing endpoint
  - Test image serving with path traversal protection
  - Test CLI export generates valid HTML
  - Test filtering by class and confidence

---

## Implementation Order

1. **Feature 2 first** (autonomous.py) — standalone module, no dependencies on other features
2. **Feature 1 second** (MAVLink commands) — modifies mavlink_io.py which is also needed by Feature 2
3. **Feature 3 last** (review tool) — completely independent, touches web/server.py

## Testing Strategy

Run `python -m pytest tests/ -v` after each feature. All existing tests must continue to pass. New tests added per feature as described above.
