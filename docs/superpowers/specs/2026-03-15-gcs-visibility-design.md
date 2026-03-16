# GCS Visibility & Operator Control — Design Spec

**Date:** 2026-03-15
**Status:** Draft
**Scope:** 6 features improving GCS integration, operator control, and web UI

---

## Context

Hydra Detect v2.0 currently sends all YOLO detections as STATUSTEXT at a single severity level. Operators have no way to filter which classes generate GCS alerts, no vehicle telemetry in the web dashboard, no way to resume a mission after loiter hold, and no tracked-target markers on the GCS map.

This spec covers 6 features that address these gaps as a single coordinated change set.

## Guiding Principles

- YOLO always detects all 80 COCO classes. Filtering only gates operator-facing outputs.
- Detection logging and ByteTrack tracking are never filtered — full data is always captured.
- No breaking changes. Empty/default config behaves like current behavior plus new capabilities.
- One new file (`geo_tracking.py`). Everything else extends existing modules.
- Safety-critical code paths (main detection loop) get minimal changes.

---

## Feature 1: Alert Class Filtering

### Purpose
Let operators control which detection classes send STATUSTEXT alerts to the GCS, without disabling detection of other classes.

### Config
`config.ini` `[mavlink]` section:
```ini
alert_classes = person, car, motorcycle, truck, bus, boat, bicycle, airplane,
                backpack, suitcase, handbag, cell phone, laptop, dog, horse,
                knife, scissors, baseball bat, bottle, umbrella
; Comma-separated class labels to alert on (empty = all detected classes).
; Labels must match the loaded model. Unknown labels are silently ignored.
```

### Backend — `mavlink_io.py`
- `__init__` accepts `alert_classes: set[str] | None` (None = all classes).
- `alert_detection()` early-returns if label not in `self._alert_classes`.
- Property getter/setter `alert_classes` for runtime updates from the web UI.

### Backend — `pipeline.py`
- Parse `alert_classes` from config: split comma-separated string into `set[str]`, empty string → `None`.
- Add `alert_classes=` parameter to the `MAVLinkIO(...)` constructor call in `Pipeline.__init__` (currently missing — the `mavlink_io.py` parameter exists but is not wired from pipeline).
- New callback `on_alert_classes_change(classes: list[str])`:
  - Empty list → `None` (all classes).
  - Non-empty → `set(classes)`.
  - Updates `self._mavlink.alert_classes`.
  - Stores in `stream_state.runtime_config["alert_classes"]`.
- Pass `alert_classes` to `draw_tracks()` for overlay dimming.

### Backend — `overlay.py`
- `draw_tracks()` accepts optional `alert_classes: set[str] | None`.
- Tracks with labels NOT in `alert_classes` render with reduced opacity (alpha ~0.35) — still visible, but visually de-emphasized.
- Tracks with labels IN `alert_classes` (or when `alert_classes` is None) render normally.
- Implementation note: `cv2` drawing functions don't support per-element alpha natively. Use `cv2.addWeighted` with a separate overlay layer for dimmed tracks. Keep it simple — draw dimmed tracks first, blend once, then draw full-opacity tracks on top.

### Web API — `server.py`
- `GET /api/config/alert-classes`:
  - Returns `{"alert_classes": ["person", "car"], "all_classes": ["person", "bicycle", ...], "categories": {...}}`.
  - `all_classes` is the live class list from the loaded detector model (see below).
  - `categories` groups classes into tactical categories (see UI section).
  - `alert_classes` is empty list when set to all (None internally).
- `POST /api/config/alert-classes`:
  - Body: `{"classes": ["person", "car"]}` or `{"classes": []}` for all.
  - Validates each class is a string that exists in the current model's class list. Unknown class names are rejected with an error.
  - Auth check, audit log.

### Web UI — `index.html`
- New "Alert Classes" subsection in the Detection Config sidebar section.
- Scrollable checklist (~160px max-height) with classes grouped by tactical category.
- Category headers are collapsible. "Other" category is collapsed by default.
- Checkbox per class. "All" and "Clear" buttons at top.
- "Apply" button sends the current selection via `POST /api/config/alert-classes`.
- On page load, fetches current config and checks matching boxes.
- When all boxes are checked (or none via "All"), sends empty list (= all classes).
- When the model is swapped at runtime, the class list refreshes automatically (re-fetch from API).

### Dynamic Class List (replaces hardcoded COCO list)

The class list comes from the loaded YOLO model at runtime, not a hardcoded constant.

**Backend — `yolo_detector.py`:**
- New method `get_class_names() -> list[str]`:
  ```python
  def get_class_names(self) -> list[str]:
      if self._model is None:
          return []
      return list(self._model.names.values())
  ```
  The ultralytics YOLO model object has a `.names` dict mapping `{class_id: label}`.

**Backend — `pipeline.py`:**
- New callback `get_class_names` wired to `stream_state`, returns `self._detector.get_class_names()`.
- After a model switch (`_handle_model_switch`), the class list updates automatically since it reads from the live model.
- If the current `alert_classes` contains labels not in the new model, those labels are silently dropped (no error — the filter just won't match anything that doesn't exist).

**Backend — `server.py`:**
- `GET /api/config/alert-classes` calls the `get_class_names` callback to get `all_classes`.
- No hardcoded `COCO_CLASSES` constant.
- Category grouping is done server-side using a known-categories map. Classes that don't match any known category go into "Other."

### Tactical Category Grouping

Server-side mapping applied to whatever class list the model provides. Known categories for common class names:

```python
TACTICAL_CATEGORIES = {
    "Personnel":        ["person", "soldier", "combatant", "civilian"],
    "Ground Vehicles":  ["car", "motorcycle", "truck", "bus", "bicycle", "train", "tank", "apc", "humvee"],
    "Watercraft/Air":   ["boat", "airplane", "drone", "uav", "helicopter", "ship"],
    "Carried Equipment":["backpack", "suitcase", "handbag", "cell phone", "laptop", "radio"],
    "Animals":          ["dog", "horse", "bird", "cow", "sheep", "cat", "elephant", "bear", "zebra", "giraffe"],
    "Potential Weapons": ["knife", "scissors", "baseball bat", "rifle", "pistol", "rpg"],
    "Concealment":      ["umbrella", "kite"],
    "Containers":       ["bottle", "cup", "bowl"],
    "Landmarks":        ["fire hydrant", "stop sign", "traffic light", "bench", "chair"],
}
```

- Only categories that have at least one matching class in the loaded model are shown.
- Classes not matching any category go into "Other" (collapsed by default).
- This works for COCO models (person, car, truck, etc.) AND military-trained models (soldier, rifle, tank, UAV, etc.) with the same code.

### Default `alert_classes` in config.ini

```ini
alert_classes = person, car, motorcycle, truck, bus, boat, bicycle, airplane,
                backpack, suitcase, handbag, cell phone, laptop, dog, horse,
                knife, scissors, baseball bat, bottle, umbrella
```

This is the default for a COCO model. When a military model is loaded, the operator should update the filter via the web UI (or clear to all). Labels that don't exist in the loaded model are silently ignored.

---

## Feature 2: Vehicle Telemetry Display

### Purpose
Show live vehicle state (mode, armed, battery, speed, altitude, heading) in the web dashboard so operators have situational awareness without switching to the GCS.

### Backend — `mavlink_io.py`

**New data streams** requested in `connect()`:
- `MAV_DATA_STREAM_EXTENDED_STATUS` at 2 Hz → provides SYS_STATUS.
- `MAV_DATA_STREAM_EXTRA1` at 2 Hz → provides VFR_HUD.

**New telemetry dict** alongside `_gps`, protected by `_gps_lock` (reusing the existing lock since both dicts are updated in the same listener thread and the lock is never held for expensive operations):
```python
self._telemetry: Dict[str, Any] = {
    "armed": False,
    "battery_v": None,      # volts (float)
    "battery_pct": None,    # 0-100 or -1 if unknown
    "groundspeed": None,    # m/s (float)
    "altitude": None,       # metres AGL (float)
    "heading": None,        # degrees 0-360 (float)
}
```

**Message parsing** in `_gps_listener` (which already handles HEARTBEAT, GLOBAL_POSITION_INT, GPS_RAW_INT):
- Add `SYS_STATUS` and `VFR_HUD` to the `recv_match` type list in `_gps_listener` only. The `_command_listener` thread is left unchanged — it only handles COMMAND_LONG and NAMED_VALUE_INT. All new message types go through `_gps_listener` to avoid recv_match contention on the same connection.
- HEARTBEAT (already handled): additionally extract `armed` from `base_mode & MAV_MODE_FLAG_SAFETY_ARMED`.
- SYS_STATUS: `voltage_battery` (mV → V, divide by 1000), `battery_remaining` (%).
- VFR_HUD: `groundspeed` (m/s), `alt` (m AGL), `heading` (degrees).

**New method:**
```python
def get_telemetry(self) -> Dict[str, Any]:
    """Return merged GPS + telemetry + vehicle mode state (thread-safe)."""
```
Returns a single dict combining:
- All `_gps` keys (`lat`, `lon`, `alt`, `fix`, `hdg`)
- All `_telemetry` keys (`armed`, `battery_v`, `battery_pct`, `groundspeed`, `altitude`, `heading`)
- `vehicle_mode` from `get_vehicle_mode()` (the existing `_vehicle_mode` string parsed from HEARTBEAT)

### Backend — `pipeline.py`
- In the stats update block (runs every frame), call `get_telemetry()` and merge into `stream_state.stats`.
- Keys: `vehicle_mode`, `armed`, `battery_v`, `battery_pct`, `groundspeed`, `altitude_m`, `heading_deg`.

### Web API — `server.py`
- No new endpoints. Telemetry rides on existing `GET /api/stats` (polled every 1s by the UI).

### Web UI — `index.html`
Vehicle Link section rework:
- **Row 1:** MAVLink badge (existing) + **Mode badge** (colored: green=AUTO, yellow=LOITER, red=GUIDED, grey=unknown) + **Armed badge** (red=ARMED, green=DISARMED).
- **Row 2:** Stat grid (2x2):
  - Battery: `12.4V 87%` with color thresholds (green >40%, yellow >20%, red ≤20%)
  - Speed: `3.2 m/s`
  - Alt: `28.5 m`
  - Heading: `247°`
- **Row 3:** GPS fix + position (existing, stays)
- **Row 4:** Mode buttons (see Feature 3)

---

## Feature 3: Vehicle Mode Buttons

### Purpose
Give operators AUTO, RTL, and LOITER buttons so they can resume a mission or return home without switching to the GCS.

### Backend — `pipeline.py`
- New handler:
  ```python
  def _handle_set_mode_command(self, mode: str) -> bool:
  ```
- Validates `mode` against allowlist: `{"AUTO", "RTL", "LOITER", "HOLD", "GUIDED"}`. The allowlist is broader than the 3 UI buttons because HOLD is the Rover equivalent of LOITER, and GUIDED is used by the strike command. The API should accept all valid modes even if the UI only exposes three buttons.
- Calls `self._mavlink.set_mode(mode)`.
- On success, sends `self._mavlink.send_statustext(f"MODE CMD: {mode}", severity=5)`.
- Returns bool success.
- Wired as callback `on_set_mode_command` in `stream_state.set_callbacks()`.

### Web API — `server.py`
- `POST /api/vehicle/mode`:
  - Body: `{"mode": "AUTO"}`.
  - Validates `mode` is a non-empty string.
  - Auth check, audit log.
  - Returns `{"status": "ok", "mode": "AUTO"}` or error.

### Web UI — `index.html`
- Replace single "Command Loiter / Hold" button with a 3-button row:
  - **LOITER** (`btn-warning`) — confirms "Command vehicle to LOITER?"
  - **AUTO** (`btn-primary`) — confirms "Resume AUTO mission?"
  - **RTL** (`btn-danger`) — confirms "Return to Launch?"
- Each calls `POST /api/vehicle/mode` with the appropriate mode string.

---

## Feature 4: Command Feedback

### Purpose
Give operators immediate visual feedback when mode commands are sent.

### Implementation
Client-side only — no backend changes beyond Features 2 and 3.

- When a mode button is clicked and the API call succeeds, the mode badge flashes a "SENDING..." state with a CSS pulse animation.
- On the next `/api/stats` poll (within ~1 second), the badge updates to the actual vehicle mode from HEARTBEAT.
- If the mode didn't change after 3 seconds, briefly flash the badge red (CSS `failed` class, 1-second duration) before reverting to the current mode. This makes failure visible without requiring a toast/popup.

### Web UI — `index.html`
- JS: after successful `POST /api/vehicle/mode`, set badge text to `"{MODE}..."` with a `sending` CSS class.
- CSS: `.mode-badge.sending` gets a pulse animation (reuse existing `@keyframes pulse`).
- The regular `updateStats()` poll (every 1s) overwrites the badge with actual mode, naturally clearing the sending state.

---

## Feature 5: Severity-Based STATUSTEXT Coloring

### Purpose
Use MAVLink severity levels so different message types show with appropriate colors in Mission Planner (green for routine detections, red for strikes, yellow for warnings).

### Changes

| Call Site | File | Current Severity | New Severity | MP Color |
|---|---|---|---|---|
| `alert_detection()` | `mavlink_io.py` | 2 (CRITICAL, from config) | 6 (INFO) | Green |
| Target lock `TGT LOCK:` | `pipeline.py` | 2 (CRITICAL, config fallback) | 5 (NOTICE) | Blue/white |
| Target lock released `TGT LOCK RELEASED` | `pipeline.py` | 2 (CRITICAL, config fallback) | 5 (NOTICE) | Blue/white |
| Target lost `TGT LOST:` | `pipeline.py` | 2 (CRITICAL, config fallback) | 4 (WARNING) | Yellow |
| Strike GUIDED waypoint | `mavlink_io.py` | 2 (hardcoded) | 1 (ALERT) | Red |
| Mode command | `pipeline.py` | not sent currently | 5 (NOTICE) | Blue/white |

### Config Impact
- The `severity` setting in config.ini becomes the fallback for `send_statustext()` calls that don't specify an explicit severity.
- `alert_detection()` overrides with severity 6 regardless of config. This prevents routine detections from being red.
- Operators who want louder detection alerts can still adjust — but via a future per-type config, not the global severity knob.

---

## Feature 6: CAMERA_TRACKING_GEO_STATUS

### Purpose
Send tracked target positions as MAVLink CAMERA_TRACKING_GEO_STATUS messages so GCS can render target markers on the map.

### Config
`config.ini` `[mavlink]` section:
```ini
geo_tracking = true         ; Send CAMERA_TRACKING_GEO_STATUS for GCS map markers
```
Default is `true` (intentional). The message is harmless per MAVLink spec — GCS that don't understand it silently ignore it. This aligns with the guiding principle of "current behavior plus new capabilities" with no operator action required.

### New File — `hydra_detect/geo_tracking.py`

```
class GeoTracker:
    """Sends CAMERA_TRACKING_GEO_STATUS for GCS map integration."""

    def __init__(self, mavlink_io: MAVLinkIO, camera_hfov_deg: float = 60.0)
    def send(self, tracks, alert_classes, locked_track_id) -> None
```

**`send()` logic:**
1. Throttle: skip if last send was <500ms ago (2 Hz cap).
2. Pick the target:
   - If a track is locked → use that track.
   - Otherwise → highest-confidence track whose label is in `alert_classes`.
   - If no qualifying tracks → don't send.
3. Estimate target distance from altitude and camera vertical FoV (rough ground-plane projection: `alt / tan(vfov/2)` as a baseline distance). Then compute lat/lon via `mavlink_io.estimate_target_position()` using this estimated distance instead of `strike_distance_m`. Note: without range data this is an approximation — the map marker shows the bearing correctly but the distance is estimated from altitude geometry. This is fundamentally better than using `strike_distance_m` (which is a fixed 20m approach distance, not a target distance estimate).
4. Encode and send `CAMERA_TRACKING_GEO_STATUS` (message ID 275):
   - `tracking_status`: 1 (TRACKING_ACTIVE) if locked, 2 (TRACKING_SEARCHING) otherwise.
   - `lat`: int32, degE7.
   - `lon`: int32, degE7.
   - `alt`: float32, metres MSL (from vehicle alt).
   - `h_acc`, `v_acc`: float32, NaN (unknown accuracy).
   - `vel_n`, `vel_e`, `vel_d`: float32, NaN (unknown velocity).
   - `hdg`: float32, NaN (unknown target heading).
5. Log at DEBUG level to avoid noise.

**Why a separate file:**
- Novel MAVLink message type with specific field encoding.
- Self-contained concern — no existing code does this.
- Easy to disable (`geo_tracking = false`) without touching other modules.

### Integration — `pipeline.py`
- Import `GeoTracker`.
- Instantiate in `__init__` if MAVLink enabled and `geo_tracking = true`.
- Call `geo_tracker.send()` in `_run_loop()` after the tracking step, passing current tracks, alert_classes, and locked_track_id.

### GCS Compatibility
- QGroundControl: renders natively on map.
- Mission Planner: partial support in newer builds (≥1.3.80).
- Other GCS: message is harmless — unrecognized messages are silently ignored per MAVLink spec.

---

## Files Modified

| File | Changes |
|---|---|
| `config.ini` | Add `alert_classes` (with tactical defaults), `geo_tracking` settings |
| `hydra_detect/mavlink_io.py` | Alert class filter, telemetry dict, extra data streams, severity overrides |
| `hydra_detect/pipeline.py` | Wire new callbacks, parse new config, integrate GeoTracker, pass alert_classes to overlay, telemetry to stats |
| `hydra_detect/detectors/yolo_detector.py` | New `get_class_names()` method |
| `hydra_detect/web/server.py` | New endpoints: alert-classes GET/POST, vehicle/mode POST. TACTICAL_CATEGORIES map. |
| `hydra_detect/web/templates/index.html` | Class filter checkboxes with categories, telemetry display, mode buttons, command feedback animations |
| `hydra_detect/overlay.py` | Dimmed rendering for non-alert-class tracks |

## New Files

| File | Purpose |
|---|---|
| `hydra_detect/geo_tracking.py` | CAMERA_TRACKING_GEO_STATUS encoder and sender |

## Not Changed

- `camera.py`, `tracker.py`, `detectors/`, `detection_logger.py`, `autonomous.py`, `osd.py`, `rf/` — untouched.
- Detection logging always captures all classes regardless of filter.
- ByteTrack always tracks all detected objects.

---

## Testing

- Unit tests for `GeoTracker` message encoding (mock MAVLinkIO).
- Unit test for alert class filter in `mavlink_io.alert_detection()`.
- Unit test for overlay dimming logic.
- Integration: run on Jetson with Pixhawk, verify in Mission Planner:
  - Green detection messages in Messages tab.
  - Mode badge updates in web UI.
  - Class filter checkbox → only checked classes generate alerts.
  - AUTO button resumes mission after loiter.
  - Target marker appears on MP map (if MP version supports it).
