# GCS Visibility & Operator Control — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 features improving GCS integration: alert class filtering, vehicle telemetry display, mode buttons, command feedback, severity-based STATUSTEXT coloring, and CAMERA_TRACKING_GEO_STATUS.

**Architecture:** Extend existing modules (`mavlink_io.py`, `pipeline.py`, `server.py`, `overlay.py`, `index.html`) following current patterns. One new file (`geo_tracking.py`) for the novel MAVLink message. Dynamic class list from loaded YOLO model replaces hardcoded COCO list.

**Tech Stack:** Python 3.10, pymavlink, FastAPI, ultralytics YOLO, cv2, Jinja2 HTML templates

**Spec:** `docs/superpowers/specs/2026-03-15-gcs-visibility-design.md`

**Partially applied:** `mavlink_io.py` already has `alert_classes` param, filter in `alert_detection()`, and property getter/setter. `config.ini` already has `alert_classes =` (empty). These are uncommitted working changes.

**Security note:** The web UI uses safe DOM methods (createElement, textContent, appendChild) for dynamic content. No innerHTML with untrusted data.

---

## File Map

| File | Responsibility | Tasks |
|---|---|---|
| `hydra_detect/mavlink_io.py` | Alert filter (done), telemetry dict, data streams, severity | 1, 3, 6 |
| `hydra_detect/detectors/yolo_detector.py` | `get_class_names()` method | 2 |
| `hydra_detect/overlay.py` | Dimmed rendering for non-alert tracks | 4 |
| `hydra_detect/geo_tracking.py` (new) | CAMERA_TRACKING_GEO_STATUS sender | 5 |
| `hydra_detect/web/server.py` | Alert-classes endpoints, vehicle/mode endpoint, tactical categories | 7, 8 |
| `hydra_detect/pipeline.py` | Wire everything: config parsing, callbacks, telemetry stats, GeoTracker | 9 |
| `hydra_detect/web/templates/index.html` | Telemetry display, mode buttons, command feedback, class checkboxes | 10 |
| `config.ini` | Update alert_classes default, add geo_tracking | 9 |
| `tests/test_alert_filter.py` (new) | Alert class filter tests | 1 |
| `tests/test_telemetry.py` (new) | Telemetry parsing tests | 3 |
| `tests/test_geo_tracking.py` (new) | GeoTracker message tests | 5 |
| `tests/test_overlay_dimming.py` (new) | Overlay dimming tests | 4 |
| `tests/test_web_api.py` | New endpoint tests | 7, 8 |

---

## Chunk 1: Backend — Alert Filter, Class Names, Telemetry

### Task 1: Alert Class Filter (mavlink_io.py) — tests + commit partial work

The filter logic is already implemented in the working tree. Write tests to lock it in.

**Files:**
- Modify: `hydra_detect/mavlink_io.py` (already modified, uncommitted)
- Create: `tests/test_alert_filter.py`

- [ ] **Step 1: Write tests for alert class filtering**

Create `tests/test_alert_filter.py` with tests for: default None allows all, init with classes, setter, setter to None, alert skipped when not in filter, alert sent when in filter, alert sent when filter is None. Use `_make_mavlink()` helper pattern from existing test files. Mock `_mav` and check `_mav.mav.send` call counts.

- [ ] **Step 2: Run tests to verify they pass (code already exists)**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_alert_filter.py -v`
Expected: All 7 tests PASS (implementation already in working tree)

- [ ] **Step 3: Commit alert class filter (partial work + tests)**

```bash
git add hydra_detect/mavlink_io.py tests/test_alert_filter.py
git commit -m "feat: add alert class filter to MAVLinkIO with tests"
```

---

### Task 2: Dynamic class names from YOLO model (yolo_detector.py)

**Files:**
- Modify: `hydra_detect/detectors/yolo_detector.py:77` (after `model_path` property)
- Modify: `tests/test_detectors.py`

- [ ] **Step 1: Write failing test**

Add `TestGetClassNames` class to `tests/test_detectors.py`: test returns empty when no model loaded, test returns names dict values when model is mocked.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_detectors.py::TestGetClassNames -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement get_class_names()**

Add to `hydra_detect/detectors/yolo_detector.py` after the `model_path` property:
```python
def get_class_names(self) -> list[str]:
    """Return class label names from the loaded model."""
    if self._model is None:
        return []
    return list(self._model.names.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_detectors.py::TestGetClassNames -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/detectors/yolo_detector.py tests/test_detectors.py
git commit -m "feat: add get_class_names() to YOLODetector for dynamic class list"
```

---

### Task 3: Vehicle telemetry in mavlink_io.py

**Files:**
- Modify: `hydra_detect/mavlink_io.py` — init (add `_telemetry` dict), `_gps_listener` (add SYS_STATUS/VFR_HUD parsing, armed extraction), `connect()` (request extra data streams), new methods (`get_telemetry()`, `_update_armed_state()`)
- Create: `tests/test_telemetry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_telemetry.py` with: test `_telemetry` dict exists with correct defaults, test `get_telemetry()` returns merged GPS + telemetry + vehicle_mode, test armed flag extraction from HEARTBEAT base_mode (128 = MAV_MODE_FLAG_SAFETY_ARMED).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_telemetry.py -v`
Expected: FAIL — `_telemetry` doesn't exist yet

- [ ] **Step 3: Implement telemetry**

In `hydra_detect/mavlink_io.py`:
- Add `self._telemetry` dict in `__init__` after `self._gps_lock`
- Add `get_telemetry()` method that merges `_gps`, `_telemetry`, and `_vehicle_mode` under `_gps_lock`
- Add `_update_armed_state(heartbeat_msg)` — extracts `base_mode & 128`
- Call `_update_armed_state` from `_gps_listener` after `_update_vehicle_mode`
- Add `SYS_STATUS` and `VFR_HUD` to `recv_match` type list in `_gps_listener`
- Parse SYS_STATUS: `voltage_battery` (mV to V, skip 0xFFFF), `battery_remaining` (skip -1)
- Parse VFR_HUD: `groundspeed`, `alt`, `heading`
- Request `MAV_DATA_STREAM_EXTENDED_STATUS` and `MAV_DATA_STREAM_EXTRA1` at 2 Hz in `connect()`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_telemetry.py -v`
Expected: PASS

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/mavlink_io.py tests/test_telemetry.py
git commit -m "feat: add vehicle telemetry (battery, speed, alt, armed) to MAVLinkIO"
```

---

## Chunk 2: Overlay Dimming + GeoTracker

### Task 4: Overlay dimming for non-alert-class tracks

**Files:**
- Modify: `hydra_detect/overlay.py` — add `alert_classes` param to `draw_tracks()`, split drawing into dimmed/full passes using `cv2.addWeighted`, extract `_draw_single_track()` helper
- Create: `tests/test_overlay_dimming.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_overlay_dimming.py`: test `draw_tracks` accepts `alert_classes` param, test None draws normally, test dimmed track has lower total pixel intensity than alert track on identical input.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_overlay_dimming.py -v`
Expected: FAIL — `unexpected keyword argument 'alert_classes'`

- [ ] **Step 3: Implement overlay dimming**

In `hydra_detect/overlay.py`:
- Add `alert_classes: set[str] | None = None` to `draw_tracks()` signature
- Extract existing per-track drawing into `_draw_single_track(frame, track, is_locked, lock_mode, blink_on)`
- Split loop: separate tracks into alert (full opacity) and dimmed lists
- Draw dimmed tracks on `frame.copy()` overlay, blend with `cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)`
- Draw alert tracks directly on frame at full opacity
- Locked tracks always draw at full opacity regardless of filter

- [ ] **Step 4: Run tests**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_overlay_dimming.py -v && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/overlay.py tests/test_overlay_dimming.py
git commit -m "feat: dim non-alert-class tracks in video overlay"
```

---

### Task 5: GeoTracker — CAMERA_TRACKING_GEO_STATUS

**Files:**
- Create: `hydra_detect/geo_tracking.py`
- Create: `tests/test_geo_tracking.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_geo_tracking.py`: test init, test no tracks = no send, test locked track prioritised, test non-alert-class tracks filtered out, test 2 Hz throttle (second immediate call skipped).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_geo_tracking.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement GeoTracker**

Create `hydra_detect/geo_tracking.py`:
- `GeoTracker.__init__(mavlink_io, camera_hfov_deg=60.0)` — stores ref, sets `_last_send = 0`, `_min_interval = 0.5`
- `send(tracks, alert_classes, locked_track_id)`:
  - Throttle check (500ms)
  - Pick target: locked first, then highest-confidence in alert filter
  - Estimate distance from altitude + camera vfov geometry: `alt / tan(hfov * 0.75 / 2)`
  - Call `mavlink_io.estimate_target_position(error_x, est_distance, hfov)`
  - If None, return
  - Call `_send_message(lat, lon, alt, is_locked)`
- `_send_message(lat, lon, alt, is_locked)`:
  - Import `pymavlink.dialects.v20.common`
  - Build `MAVLink_camera_tracking_geo_status_message` with degE7 coords, NaN for unknowns
  - `tracking_status`: 1 (ACTIVE) if locked, 2 (SEARCHING) otherwise
  - Send via `mavlink_io._mav.mav.send()`
  - Log at DEBUG level

- [ ] **Step 4: Run tests**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_geo_tracking.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/geo_tracking.py tests/test_geo_tracking.py
git commit -m "feat: add GeoTracker for CAMERA_TRACKING_GEO_STATUS map markers"
```

---

### Task 6: Severity overrides

**Files:**
- Modify: `hydra_detect/mavlink_io.py` — `alert_detection()` severity to 6 (INFO), `command_guided_to()` severity to 1 (ALERT)

- [ ] **Step 1: Change alert_detection severity**

In `alert_detection()`, change `self.send_statustext(msg)` to `self.send_statustext(msg, severity=6)`.

- [ ] **Step 2: Change strike severity**

In `command_guided_to()`, change `severity=2` to `severity=1`.

- [ ] **Step 3: Run all tests**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add hydra_detect/mavlink_io.py
git commit -m "feat: set detection alerts to INFO (green), strikes to ALERT (red)"
```

---

## Chunk 3: Web API Endpoints

### Task 7: Alert classes API + tactical categories

**Files:**
- Modify: `hydra_detect/web/server.py` — add `TACTICAL_CATEGORIES` constant, `_categorize_classes()` helper, GET and POST endpoints for `/api/config/alert-classes`
- Modify: `tests/test_web_api.py` — add `TestAlertClassesEndpoints`

- [ ] **Step 1: Write failing tests**

Add `TestAlertClassesEndpoints` to `tests/test_web_api.py`: test GET returns all_classes/alert_classes/categories, test POST with valid classes calls callback, test POST empty list means all, test POST with invalid class name returns 400.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_web_api.py::TestAlertClassesEndpoints -v`
Expected: FAIL — routes don't exist

- [ ] **Step 3: Implement**

In `hydra_detect/web/server.py`:
- Add `TACTICAL_CATEGORIES` dict constant (Personnel, Ground Vehicles, Watercraft/Air, Carried Equipment, Animals, Potential Weapons, Concealment, Containers, Landmarks)
- Add `_categorize_classes(all_classes)` function — matches classes to categories, remainder goes to "Other"
- Add `GET /api/config/alert-classes` — calls `get_class_names` callback, returns `{alert_classes, all_classes, categories}`
- Add `POST /api/config/alert-classes` — auth check, validates `classes` list, validates each class exists in model's class list, calls `on_alert_classes_change` callback, audit log

- [ ] **Step 4: Run tests**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_web_api.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/server.py tests/test_web_api.py
git commit -m "feat: add alert-classes API endpoints with tactical category grouping"
```

---

### Task 8: Vehicle mode API endpoint

**Files:**
- Modify: `hydra_detect/web/server.py` — add `POST /api/vehicle/mode`
- Modify: `tests/test_web_api.py` — add `TestVehicleModeEndpoint`, add to auth test list

- [ ] **Step 1: Write failing tests**

Add `TestVehicleModeEndpoint`: test success calls callback, test missing mode returns 400, test no callback returns 503, test failed command returns 503. Add `("POST", "/api/vehicle/mode", {"mode": "AUTO"})` to `CONTROL_ENDPOINTS`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_web_api.py::TestVehicleModeEndpoint -v`
Expected: FAIL — route doesn't exist

- [ ] **Step 3: Implement endpoint**

Add `POST /api/vehicle/mode` to `server.py` — validates mode is non-empty string, auth check, calls `on_set_mode_command` callback, audit log, returns `{status, mode}` or error.

- [ ] **Step 4: Run tests**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/test_web_api.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/server.py tests/test_web_api.py
git commit -m "feat: add vehicle mode endpoint (AUTO/RTL/LOITER)"
```

---

## Chunk 4: Pipeline Wiring + Config

### Task 9: Wire everything in pipeline.py + update config.ini

This is the integration task — connects all backend pieces.

**Files:**
- Modify: `hydra_detect/pipeline.py` — MAVLinkIO constructor, callbacks, alert loop, stats block, severity calls, GeoTracker integration
- Modify: `config.ini` — final alert_classes defaults, geo_tracking setting

- [ ] **Step 1: Update config.ini with final defaults**

Replace `alert_classes` line with tactical defaults. Add `geo_tracking = true` after `strike_distance_m`.

- [ ] **Step 2: Parse alert_classes and pass to MAVLinkIO**

In `Pipeline.__init__`, parse `alert_classes` from config (comma-split to `set[str]`, empty = `None`). Add `alert_classes=alert_classes` to `MAVLinkIO()` constructor. Store as `self._alert_classes`.

- [ ] **Step 3: Add handlers**

Add `_ALLOWED_MODES` class attribute. Add `_handle_set_mode_command(mode)` — validates against allowlist, calls `set_mode()`, sends STATUSTEXT at severity 5. Add `_handle_alert_classes_change(classes)` — updates `self._alert_classes` and `self._mavlink.alert_classes`, updates `stream_state.runtime_config`.

- [ ] **Step 4: Wire callbacks**

Add `on_set_mode_command`, `on_alert_classes_change`, `get_class_names` to `stream_state.set_callbacks()`. Add `alert_classes` to initial `stream_state.update_runtime_config()`.

- [ ] **Step 5: Pass alert_classes to draw_tracks()**

Add `alert_classes=self._alert_classes` to the `draw_tracks()` call in `_run_loop()`.

- [ ] **Step 6: Add telemetry to stats**

In the stats update block, call `self._mavlink.get_telemetry()` and add `vehicle_mode`, `armed`, `battery_v`, `battery_pct`, `groundspeed`, `altitude_m`, `heading_deg` to `stats_update`.

- [ ] **Step 7: Add severity to pipeline STATUSTEXT calls**

Target lock: `severity=5`. Target unlock: `severity=5`. Target lost: `severity=4`.

- [ ] **Step 8: Integrate GeoTracker**

In `__init__`, instantiate `GeoTracker` if MAVLink enabled and `geo_tracking = true`. In `_run_loop()`, call `geo_tracker.send()` after tracking step.

- [ ] **Step 9: Run all tests**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add config.ini hydra_detect/pipeline.py
git commit -m "feat: wire alert filter, telemetry, mode commands, geo-tracker in pipeline"
```

---

## Chunk 5: Web UI

### Task 10: Vehicle telemetry display, mode buttons, command feedback, class checkboxes

All changes in `hydra_detect/web/templates/index.html`. Uses safe DOM methods (createElement, textContent, appendChild) — no innerHTML with untrusted data.

**Files:**
- Modify: `hydra_detect/web/templates/index.html`

- [ ] **Step 1: Rework Vehicle Link section HTML**

Replace existing Vehicle Link content with: mode badge, armed badge, battery/speed/alt/heading stat grid, GPS fix/position row, 3-button row (LOITER/AUTO/RTL).

- [ ] **Step 2: Add CSS for mode/armed badges and command feedback**

Add `.mode-badge` variants (`.auto`, `.loiter`, `.hold`, `.guided`, `.rtl`, `.sending`, `.failed`). Add `.armed-badge` variants (`.armed`, `.disarmed`).

- [ ] **Step 3: Update updateStats() to display telemetry**

Add vehicle mode badge update (skip if `.sending` class active), armed badge, battery with color thresholds, speed, alt, heading. Add pending mode feedback check (3s timeout, flash red on failure).

- [ ] **Step 4: Add commandMode() function**

Replace `commandLoiter()` with `commandMode(mode)` — confirms with mode-specific message, POSTs to `/api/vehicle/mode`, sets badge to sending state with pulse animation.

- [ ] **Step 5: Add alert class checklist UI**

Add "Alert Classes" subsection in Detection Config: All/Clear buttons, scrollable categorized checklist container, Apply button. Build DOM with safe methods (createElement/textContent/appendChild).

- [ ] **Step 6: Add alert class JS functions**

`loadAlertClasses()` — fetches from API, builds `alertClassData` state. `renderAlertClassList()` — builds DOM with category headers (collapsible, Other collapsed by default) and checkbox labels using createElement. `alertClassAll()` / `alertClassClear()` — toggle all checkboxes. `applyAlertClasses()` — POSTs selected classes (empty list if all selected).

- [ ] **Step 7: Add loadAlertClasses() to init calls and model-swap hook**

Add alongside existing `loadConfig()`, `loadCameraSources()`, `loadPowerModes()`, `loadModels()`. Also add a `loadAlertClasses()` call inside the existing `switchModel()` function's success path, so the class list refreshes automatically when the operator swaps YOLO models at runtime.

- [ ] **Step 8: Manual test in browser**

Run Hydra and verify: telemetry displays, mode buttons work with feedback, class checkboxes load categorized, apply sends filter.

- [ ] **Step 9: Commit**

```bash
git add hydra_detect/web/templates/index.html
git commit -m "feat: add telemetry display, mode buttons, command feedback, class filter UI"
```

---

## Chunk 6: Final Integration + Run

### Task 11: Full integration test

- [ ] **Step 1: Run full test suite**

Run: `cd /home/sorcc/Hydra && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run linter**

Run: `cd /home/sorcc/Hydra && flake8 hydra_detect/ tests/ --max-line-length=120`
Fix any issues.

- [ ] **Step 3: Run on Jetson with Pixhawk**

Run: `cd /home/sorcc/Hydra && python3 -m hydra_detect --config config.ini`

Verify:
- [ ] Detection alerts appear as green text in Mission Planner Messages tab
- [ ] Vehicle mode badge updates in web UI
- [ ] Armed/disarmed badge correct
- [ ] Battery, speed, alt, heading populate
- [ ] LOITER button switches mode (badge updates)
- [ ] AUTO button resumes mission
- [ ] Alert class checkboxes work — uncheck "toothbrush", apply, no more toothbrush alerts
- [ ] Dimmed tracks visible in video overlay for non-alert classes
- [ ] Target lock STATUSTEXT shows in blue/white (severity 5)
- [ ] Strike STATUSTEXT shows in red (severity 1)
- [ ] (If MP supports it) Target marker appears on GCS map

- [ ] **Step 4: Commit any integration fixes**

```bash
git add -A
git commit -m "fix: integration fixes from live Jetson testing"
```
