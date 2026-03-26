# Mission Profiles — Design Spec

**Date:** 2026-03-25
**Audience:** ops (primary), demo, dev
**Status:** Approved

## Problem

Hydra now has 6 YOLO models covering different domains (COCO general, counter-UAS,
aerial surveillance, military, ground vehicles, force protection). Operators must
manually switch the model, update alert classes, adjust confidence, and toggle
loiter settings — 4+ separate actions when it should be 1. An operator in the
field shouldn't need to know which `.pt` file maps to which mission type.

## Solution

**Mission Profiles** — pre-defined configuration bundles that apply a model +
confidence + class filters + alert classes + engagement settings in a single
click from the operations view.

## Data Structure

Profiles live in `profiles.json` at the project root (alongside `config.ini`).

```json
{
  "default_profile": "military",
  "profiles": [
    {
      "id": "general",
      "name": "General (COCO)",
      "description": "Standard detection — person, vehicle, boat, animal",
      "model": "yolov8n.pt",
      "confidence": 0.45,
      "yolo_classes": [0, 1, 2, 3, 5, 7, 8, 14, 15, 16, 24, 25, 28],
      "alert_classes": ["person", "car", "motorcycle", "truck", "bus", "boat", "dog"],
      "auto_loiter_on_detect": false,
      "strike_distance_m": 20.0
    },
    {
      "id": "military",
      "name": "Military (General)",
      "description": "Broad military — air, ground, and maritime targets",
      "model": "yolov8m-defence.pt",
      "confidence": 0.40,
      "yolo_classes": null,
      "alert_classes": ["tank", "fighter jet", "warship", "drone", "missile", "helicopter", "truck", "cargo ship"],
      "auto_loiter_on_detect": false,
      "strike_distance_m": 30.0
    },
    {
      "id": "counter-uas",
      "name": "Counter-UAS",
      "description": "Detect drones, helicopters, and aircraft in the sky",
      "model": "yolo11n-aerodetect.pt",
      "confidence": 0.35,
      "yolo_classes": null,
      "alert_classes": ["Drone", "Helicopter", "AirPlane"],
      "auto_loiter_on_detect": true,
      "strike_distance_m": 50.0
    },
    {
      "id": "aerial-surveillance",
      "name": "Aerial Surveillance",
      "description": "People and vehicles from a drone's perspective",
      "model": "yolo11n-visdrone.pt",
      "confidence": 0.30,
      "yolo_classes": null,
      "alert_classes": ["pedestrian", "people", "car", "van", "truck", "bus"],
      "auto_loiter_on_detect": false,
      "strike_distance_m": 20.0
    },
    {
      "id": "ground-vehicles",
      "name": "Ground Vehicles",
      "description": "Armored and light military vehicles",
      "model": "yolo12n-orion.pt",
      "confidence": 0.40,
      "yolo_classes": null,
      "alert_classes": ["AFV", "APC", "MEV", "LAV"],
      "auto_loiter_on_detect": false,
      "strike_distance_m": 25.0
    },
    {
      "id": "force-protection",
      "name": "Force Protection",
      "description": "Weapons and explosive threat detection",
      "model": "yolov8n-threat.pt",
      "confidence": 0.50,
      "yolo_classes": null,
      "alert_classes": ["Gun", "explosion", "grenade", "knife"],
      "auto_loiter_on_detect": true,
      "strike_distance_m": 15.0
    }
  ]
}
```

Fields per profile:
- `id` — unique key, used in API calls
- `name` — human-readable label for UI dropdown
- `description` — one-line explanation shown below dropdown on selection
- `model` — filename in `models/` directory
- `confidence` — detection confidence threshold (0.0-1.0)
- `yolo_classes` — list of YOLO class IDs to detect, or `null` for all
- `alert_classes` — list of class label names that trigger MAVLink alerts
- `auto_loiter_on_detect` — whether to command vehicle loiter on detection
- `strike_distance_m` — engagement distance threshold

`default_profile` sets which profile loads on startup.

## API

### `GET /api/profiles`

Returns all profiles and the currently active one.

```json
{
  "profiles": [
    {"id": "general", "name": "General (COCO)", "description": "...", "model": "yolov8n.pt", "model_exists": true},
    {"id": "counter-uas", "name": "Counter-UAS", "description": "...", "model": "yolo11n-aerodetect.pt", "model_exists": true}
  ],
  "active_profile": "counter-uas"
}
```

- `model_exists` tells the UI whether the model file is present on disk.
- Profiles with missing models are still listed but show a warning in the UI.

### `POST /api/profiles/switch`

Applies a profile. Requires auth token.

```json
// Request
{"profile": "counter-uas"}

// Response (success)
{"ok": true, "profile": "counter-uas", "model_switched": true}

// Response (missing model)
{"ok": false, "error": "Model yolo11n-aerodetect.pt not found in models/"}
```

Under the hood, this single handler:
1. Looks up the profile by ID
2. Switches the YOLO model (reuses `_handle_model_switch`)
3. Sets `yolo_classes` on the detector (new `set_classes()` method)
4. Updates confidence threshold (reuses `set_threshold`)
5. Updates alert classes (reuses `_handle_alert_classes_change`)
6. Updates auto_loiter and strike_distance in runtime config
7. Stores active profile ID in stream state

### Active profile tracking

- `stream_state.runtime_config["active_profile"]` tracks the current profile ID
- If the operator manually changes any individual setting (model, threshold,
  alert classes) via existing controls, `active_profile` resets to `null`
- The UI shows "Custom" when `active_profile` is `null`

## New Files

### `profiles.json`
The profile definitions as shown above.

### `hydra_detect/profiles.py`
Small module (~60 lines):
- `load_profiles(path: str) -> dict` — read and validate profiles.json
- `get_profile(profiles: dict, profile_id: str) -> dict | None` — lookup by ID
- Validation: check required fields exist, types are correct
- Graceful fallback: if file missing/malformed, return empty profiles list

## Modified Files

### `hydra_detect/detectors/yolo_detector.py`
Add `set_classes(classes: list[int] | None) -> None` method to update
`self._classes` at runtime. This is passed to `model.predict(classes=...)`.

### `hydra_detect/pipeline.py`
- Load profiles on init from `profiles.json`
- Add `_handle_profile_switch(profile_id: str) -> bool` callback
- Register callback with web server as `switch_profile`
- Apply `default_profile` on startup after detector loads
- Track `active_profile` in runtime config
- Reset `active_profile` to `null` in existing handlers for threshold,
  alert_classes, and model switch when called independently

### `hydra_detect/web/server.py`
- Add `GET /api/profiles` endpoint (no auth required, read-only)
- Add `POST /api/profiles/switch` endpoint (auth required)
- Both delegate to pipeline callbacks

### `hydra_detect/web/templates/operations.html`
- Add profile dropdown + description text above the existing model/confidence section
- Existing model display becomes read-only label showing current model filename
- Confidence slider, alert classes, and other controls remain interactive

### `hydra_detect/web/static/js/operations.js`
- `loadProfiles()` — fetch and populate profile dropdown on page load
- `switchProfile(id)` — POST to switch, update all UI controls to reflect new state
- On profile switch success: update model label, confidence slider, alert class
  checkboxes, loiter toggle to match the applied profile
- On manual setting change: set dropdown to "Custom"
- Show warning icon on profiles whose model file is missing

### `hydra_detect/web/static/css/base.css`
- Styling for profile dropdown (slightly larger/more prominent than other controls)
- "Custom" state styling
- Missing-model warning styling

## UI Layout (Operations View)

```
┌─────────────────────────────────────┐
│  Mission Profile                    │
│  ┌────────────────────────────────┐ │
│  │ Counter-UAS                 ▼  │ │
│  └────────────────────────────────┘ │
│  Detect drones, helicopters,        │
│  aircraft in the sky                │
│                                     │
│  Model: yolo11n-aerodetect.pt       │
│                                     │
│  Confidence ────────────────────    │
│  ████████░░░░░░░░░░░░  0.35        │
│                                     │
│  Alert Classes ─────────────────    │
│  [✓] Drone  [✓] Helicopter         │
│  [✓] AirPlane                       │
│  [All] [Clear] [Apply]             │
└─────────────────────────────────────┘
```

- Profile dropdown is the top-most control in the detection panel
- Description updates on selection
- Manual changes to any control below switch dropdown to "Custom"

## Error Handling

- **Missing `profiles.json`:** Hydra starts normally. Profile dropdown hidden.
  Existing model/settings controls work as before.
- **Malformed JSON:** Log warning, treat as missing. No profiles available.
- **Missing model file:** Profile appears in dropdown with warning indicator.
  Switching to it returns a clear error message.
- **Model load failure:** `switch_model()` already handles this — keeps old model
  active, returns false, UI shows error toast.

## Startup Behavior

1. Pipeline loads `profiles.json`
2. If valid and `default_profile` is set, apply that profile's settings
3. This sets model, confidence, yolo_classes, alert_classes, loiter, strike distance
4. If `default_profile` model doesn't exist, fall back to `config.ini` values
5. `active_profile` set to the default profile ID (or `null` on fallback)

## Verification

1. `profiles.json` loads without error on startup
2. `GET /api/profiles` returns all 6 profiles with correct `model_exists` flags
3. `POST /api/profiles/switch` with each profile ID successfully switches model
   and applies all settings
4. Web UI dropdown shows all profiles, selecting one updates all controls
5. Manually changing confidence/alert classes resets dropdown to "Custom"
6. Removing a model `.pt` file shows warning in UI, switch returns error
7. Deleting `profiles.json` — Hydra starts normally without profile UI
8. Run `python -m pytest tests/` — existing tests still pass
