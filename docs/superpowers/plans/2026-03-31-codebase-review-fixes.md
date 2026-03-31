# Codebase Review Fixes — 81 Issues

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 81 issues found during the full codebase review — 10 critical, 22 high, 28 medium, 23 low — across safety-critical, web, RF, TAK, logging, and utility modules.

**Architecture:** Fixes are grouped by file/module into 14 independent tasks with no file overlap, enabling maximum parallelization. Each task modifies 1-3 files. Safety-critical fixes (Tasks 1-5) should be prioritized.

**Tech Stack:** Python 3.10+, FastAPI, OpenCV, pymavlink, ByteTrack, ultralytics YOLO

---

## File Map

| Task | Files Modified | Fix Count |
|------|---------------|-----------|
| 1 | `approach.py` | 7 |
| 2 | `autonomous.py` | 4 |
| 3 | `dogleg_rtl.py` | 3 |
| 4 | `mavlink_io.py` | 6 |
| 5 | `geo_tracking.py` | 2 |
| 6 | `pipeline.py` | 11 |
| 7 | `camera.py`, `tracker.py` | 4 |
| 8 | `overlay.py` | 3 |
| 9 | `detectors/yolo_detector.py`, `config_schema.py` | 3 |
| 10 | `web/server.py` | 5 |
| 11 | `web/config_api.py` | 3 |
| 12 | `rf/hunt.py`, `rf/navigator.py`, `rf/kismet_client.py` | 13 |
| 13 | `tak/tak_output.py`, `tak/tak_input.py`, `tak/cot_builder.py` | 5 |
| 14 | `detection_logger.py`, `event_logger.py`, `review_export.py`, `mavlink_video.py`, `osd.py` | 8 |

**Dependency:** Task 5 (geo_tracking) depends on Task 4 (mavlink_io) adding `send_raw_message()`. Task 6 (pipeline) benefits from Task 13 (TAK) adding `is_running()` methods but can use `hasattr` fallback.

---

## Task 1: approach.py — Safety-Critical Fixes

**Files:**
- Modify: `hydra_detect/approach.py`
- Test: `tests/` (run full suite)

### Fix 1.1 — Drop servo double-fire race (S1, CRITICAL)
**Line 396-438:** Hold lock across the entire check + servo fire + flag set.

- [ ] **Step 1:** In `_update_drop`, replace the split lock pattern with a single lock scope:

```python
def _update_drop(self, track, fw: int, fh: int) -> None:
    """Update drop mode -- check distance, fire servo when close."""
    # Import at module level (also fixes the import-in-loop issue)
    # ... distance calculation stays outside lock ...

    if dist <= self._cfg.drop_distance_m:
        with self._lock:
            if self._drop_complete:
                return
            self._drop_complete = True  # Set BEFORE firing
        # Fire servo outside lock (MAVLink has its own lock)
        if self._cfg.drop_channel:
            self._mavlink.set_servo(self._cfg.drop_channel, self._cfg.drop_pwm)
```

Key change: set `_drop_complete = True` BEFORE firing the servo, under the lock. Second caller sees `True` and returns.

### Fix 1.2 — PIXEL_LOCK starts before GUIDED confirmed (S2, CRITICAL)
**Line 160-186:** Attempt GUIDED before committing mode state.

- [ ] **Step 2:** Restructure `start_pixel_lock` to try GUIDED first:

```python
def start_pixel_lock(self, track_id: int) -> bool:
    # Try GUIDED mode BEFORE committing state
    try:
        ok = self._mavlink.set_mode("GUIDED")
        if not ok:
            logger.warning("PIXEL_LOCK: failed to set GUIDED mode")
            return False
    except Exception as exc:
        logger.error("PIXEL_LOCK: mode switch error: %s", exc)
        return False

    with self._lock:
        if self._mode != ApproachMode.IDLE:
            return False
        self._pre_approach_mode = self._mavlink.get_vehicle_mode()
        self._target_track_id = track_id
        self._mode = ApproachMode.PIXEL_LOCK
        self._running = True
        # ... rest of init ...
    return True
```

Apply the same pattern to `start_follow` and `start_strike` if they have the same issue.

### Fix 1.3 — Abort never restores pre-approach mode (S3, CRITICAL)
**Line 259:** Use `_pre_approach_mode` first, fall back to `abort_mode`.

- [ ] **Step 3:** In `abort()`:

```python
# Switch to pre-approach mode, or abort mode as fallback
try:
    restore_mode = self._pre_approach_mode or self._cfg.abort_mode
    self._mavlink.set_mode(restore_mode)
    logger.info("Approach aborted — restored mode: %s", restore_mode)
except Exception:
    pass
```

### Fix 1.4 — Strike uses follow_distance_m (S6, HIGH)
**Line 465:** Use a dedicated strike approach distance.

- [ ] **Step 4:** Add `strike_approach_m` to `ApproachConfig` (default 5.0), use it in `_update_strike`:

```python
# In _update_strike, line 465:
target_pos = self._mavlink.estimate_target_position(
    error_x,
    self._cfg.strike_approach_m,  # was: follow_distance_m
    self._cfg.camera_hfov_deg,
)
```

Add `strike_approach_m` to config loading wherever `ApproachConfig` is constructed.

### Fix 1.5 — _last_wp_time TOCTOU (S7, HIGH)
**Lines 361, 456, 484:** Move the rate check inside the lock.

- [ ] **Step 5:** In `_update_follow` and `_update_strike`, protect `_last_wp_time`:

```python
with self._lock:
    now = time.monotonic()
    if (now - self._last_wp_time) < self._cfg.waypoint_interval:
        return
    self._last_wp_time = now
    self._waypoints_sent += 1
```

### Fix 1.6 — Import inside hot loop (LOW)
**Line 408:** Move `from .autonomous import haversine_m` to module top level.

- [ ] **Step 6:** Add `from .autonomous import haversine_m` at the top of `approach.py` with the other imports.

### Fix 1.7 — Abort log doesn't show target mode (MEDIUM)
**Line 263:** Add mode to log message.

- [ ] **Step 7:** Update the abort log line to include the restored mode.

- [ ] **Step 8: Run tests**
```bash
python -m pytest tests/ -v 2>&1 | tail -20
```

- [ ] **Step 9: Commit**
```bash
git add hydra_detect/approach.py
git commit -m "fix: 7 safety fixes in approach controller

- Hold lock across drop servo fire to prevent double-fire race
- Confirm GUIDED mode before committing to PIXEL_LOCK state
- Restore pre-approach mode on abort instead of hardcoded LOITER
- Add strike_approach_m config (was using follow_distance_m)
- Fix _last_wp_time TOCTOU by moving rate check inside lock
- Move haversine_m import to module level
- Log restored mode on abort"
```

---

## Task 2: autonomous.py — Safety Fixes

**Files:**
- Modify: `hydra_detect/autonomous.py`

### Fix 2.1 — Geofence checked with stale GPS (S4, CRITICAL)
**Lines 196-212:** Move GPS freshness check BEFORE geofence evaluation.

- [ ] **Step 1:** Reorder the blocks in `evaluate()`:

```python
# Check GPS freshness FIRST
get_gps = getattr(mavlink, "get_gps", None)
if get_gps is not None:
    gps_data = get_gps()
    gps_age = now - gps_data.get("last_update", 0.0)
    if gps_age > self._gps_max_stale_sec:
        logger.debug("GPS stale (%.1fs) -- skipping autonomous eval", gps_age)
        return

# THEN check geofence with fresh position
get_lat_lon = getattr(mavlink, "get_lat_lon", None)
if get_lat_lon is None:
    return
lat, lon, _ = get_lat_lon()
if lat is None or lon is None:
    return
if not self.check_geofence(lat, lon):
    return
```

### Fix 2.2 — _strike_in_progress never set True (S12, HIGH)
**Line 144/354:** Set `True` when strike callback fires.

- [ ] **Step 2:** Find where `strike_cb` is called in `evaluate()` and set `self._strike_in_progress = True` before it.

### Fix 2.3 — Empty allowed_classes silent (MEDIUM)
**Line 232:** Add a warning log.

- [ ] **Step 3:**
```python
if not self._allowed_classes:
    if self._frame_count % 300 == 0:  # throttle: every ~30s at 10fps
        logger.warning("Autonomous: allowed_classes is empty -- no targets will qualify")
    continue
```

### Fix 2.4 — get_lat_lon destructure guard (HIGH)

- [ ] **Step 4:** Wrap the destructure in try/except:
```python
try:
    lat, lon, _ = get_lat_lon()
except Exception:
    return
```

- [ ] **Step 5: Run tests and commit**
```bash
python -m pytest tests/test_autonomous.py tests/test_drop_strike.py -v
git add hydra_detect/autonomous.py
git commit -m "fix: 4 safety fixes in autonomous controller

- Check GPS freshness BEFORE geofence evaluation
- Set _strike_in_progress=True when strike fires
- Warn when allowed_classes is empty (fail-closed was silent)
- Guard get_lat_lon destructure against exceptions"
```

---

## Task 3: dogleg_rtl.py — Safety Fixes

**Files:**
- Modify: `hydra_detect/dogleg_rtl.py`

### Fix 3.1 — No error handling in _run (S8, HIGH)
**Lines 115-151:** Wrap entire _run in try/except, fall back to RTL.

- [ ] **Step 1:**
```python
def _run():
    try:
        # ... existing climb/offset/SMART_RTL logic ...
    except Exception as exc:
        logger.error("DoglegRTL failed: %s -- falling back to RTL", exc, exc_info=True)
        try:
            self._mavlink.set_mode("RTL")
        except Exception:
            pass
    finally:
        with self._lock:
            self._phase = "done"
```

### Fix 3.2 — Blind 5s sleep (S9, HIGH)
**Line 127:** Replace with altitude-polling loop.

- [ ] **Step 2:**
```python
# Poll altitude instead of blind sleep
for _ in range(50):  # 25s max (50 * 0.5s)
    if self._stop_evt.is_set():
        return
    cur = self._mavlink.get_lat_lon()
    if cur and cur[2] is not None and cur[2] >= self._climb_alt * 0.9:
        break
    time.sleep(0.5)
```

### Fix 3.3 — Silent fallthrough on waypoint miss (MEDIUM)
**Line 141:** Add log when loop exhausts.

- [ ] **Step 3:** Add `else` clause to the for loop:
```python
else:
    logger.warning("DoglegRTL: offset waypoint not reached within 60s -- proceeding to RTL")
```

- [ ] **Step 4: Run tests and commit**
```bash
python -m pytest tests/ -v
git add hydra_detect/dogleg_rtl.py
git commit -m "fix: 3 safety fixes in dogleg RTL

- Wrap _run in try/except with RTL fallback on failure
- Replace blind 5s sleep with altitude-polling loop
- Log warning when offset waypoint not reached"
```

---

## Task 4: mavlink_io.py — Lock Cleanup + Optimization

**Files:**
- Modify: `hydra_detect/mavlink_io.py`

### Fix 4.1 — STATUSTEXT on every waypoint (S5, HIGH)
**Line 690:** Remove `send_statustext` from `command_guided_to`.

- [ ] **Step 1:** Delete the `self.send_statustext(...)` line from `command_guided_to`. The `logger.info` already provides the record.

### Fix 4.2 — Unnecessary outer lock in send_statustext (HIGH)
**Lines 463-471:** Remove outer `_lock`, keep only `_send_lock`.

- [ ] **Step 2:**
```python
def send_statustext(self, text: str, severity: int = 4) -> None:
    # Rate diagnostics (no lock needed -- single-writer on CPython GIL)
    self._st_send_count += 1
    # ... rate diagnostic logic ...

    try:
        from pymavlink.dialects.v20 import common as mavlink2
        payload = text[:50].ljust(50, '\0').encode('utf-8')
        msg = mavlink2.MAVLink_statustext_message(severity=severity, text=payload)
        with self._send_lock:
            self._mav.mav.send(msg, force_mavlink1=False)
    except Exception as exc:
        logger.warning("Failed to send STATUSTEXT: %s", exc)
```

### Fix 4.3 — Cache mode_mapping (S13, MEDIUM)
**Lines 531, 658:** Cache the mode map after first call.

- [ ] **Step 3:** Add `self._mode_map: dict | None = None` in `__init__`. Add helper:
```python
def _get_mode_map(self) -> dict:
    if self._mode_map is None:
        self._mode_map = self._mav.mode_mapping()
    return self._mode_map
```
Use `self._get_mode_map()` in `command_loiter` and `command_guided_to`.

### Fix 4.4 — Magic number for MAV_TYPE_GCS (LOW)
**Line 247:** Use named constant.

- [ ] **Step 4:**
```python
if msg.type == mavutil.mavlink.MAV_TYPE_GCS:
    continue
```

### Fix 4.5 — Add `send_raw_message()` public method
For `geo_tracking.py` and `osd.py` to stop accessing private `_mav`.

- [ ] **Step 5:**
```python
def send_raw_message(self, msg) -> bool:
    """Send a pre-built MAVLink message via the send lock."""
    if self._mav is None:
        return False
    try:
        with self._send_lock:
            self._mav.mav.send(msg, force_mavlink1=False)
        return True
    except Exception as exc:
        logger.warning("Failed to send raw message: %s", exc)
        return False
```

### Fix 4.6 — Add `send_param_set()` public method
For `osd.py` to stop accessing private `_mav._mav.mav.param_set_send`.

- [ ] **Step 6:**
```python
def send_param_set(self, param_id: str, value: float, param_type: int = 9) -> bool:
    """Send PARAM_SET message."""
    if self._mav is None:
        return False
    try:
        with self._send_lock:
            self._mav.mav.param_set_send(
                self._mav.target_system,
                self._mav.target_component,
                param_id.encode("utf-8"),
                value,
                param_type,
            )
        return True
    except Exception as exc:
        logger.warning("Failed to send PARAM_SET %s: %s", param_id, exc)
        return False
```

- [ ] **Step 7: Run tests and commit**
```bash
python -m pytest tests/ -v
git add hydra_detect/mavlink_io.py
git commit -m "fix: 6 fixes in mavlink_io — lock cleanup, caching, public API

- Remove STATUSTEXT from command_guided_to (was flooding GCS)
- Remove unnecessary outer _lock from send_statustext
- Cache mode_mapping() result (was re-fetched on every loiter/waypoint)
- Use MAV_TYPE_GCS constant instead of magic number 6
- Add send_raw_message() public method for geo_tracking/osd
- Add send_param_set() public method for osd"
```

---

## Task 5: geo_tracking.py — Hardcoded Width + Private Access

**Files:**
- Modify: `hydra_detect/geo_tracking.py`
- **Depends on:** Task 4 (send_raw_message)

### Fix 5.1 — Hardcoded 320px (S10, HIGH)
**Line 70:** Accept frame width as parameter.

- [ ] **Step 1:** Add `frame_w: int` parameter to `send()` method. Replace `320.0` with `frame_w / 2.0`.

### Fix 5.2 — Private _mav access (S11, HIGH)
**Lines 88, 111:** Use public API.

- [ ] **Step 2:** Replace `self._mav._mav is None` with `not self._mav.connected` (add `connected` property to MAVLinkIO if needed). Replace `self._mav._mav.mav.send(msg)` with `self._mav.send_raw_message(msg)`.

- [ ] **Step 3: Update callers in pipeline.py** to pass frame width to `geo_tracker.send()`.

- [ ] **Step 4: Run tests and commit**

---

## Task 6: pipeline.py — Hot Path + Logging Fixes

**Files:**
- Modify: `hydra_detect/pipeline.py`

### Fixes (11 total):
- [ ] **6.1 (P1, CRITICAL):** Move `self._last_frame_time = time.monotonic()` to right after `det_result = self._detector.detect(frame)` (~line 1181)
- [ ] **6.2 (P2, CRITICAL):** Replace `cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)` with `float(frame[:, :, 1].mean())` — green channel, zero alloc
- [ ] **6.3 (P5, HIGH):** Move `logging.basicConfig()` from `start()` to `__main__.py:main()` before `Pipeline()` construction
- [ ] **6.4 (P11, LOW):** Change `logger.error("Detector failed to load: %s", exc)` to `logger.exception("Detector failed to load")`
- [ ] **6.5 (P12, LOW):** Change hardcoded `"yolo"` to `type(self._detector).__name__`
- [ ] **6.6 (P14, MEDIUM):** Cache `gps` and `telem` at top of loop, reuse in stats block below
- [ ] **6.7 (C1):** Store `pan_ch`/`strike_ch` as instance attrs to avoid scope hazard
- [ ] **6.8 (C4):** Remove dead `alert_sent` variable
- [ ] **6.9 (P7, MEDIUM):** Replace `._thread` access with `hasattr(obj, 'is_running') and obj.is_running()` pattern (graceful fallback if subsystems don't have the method yet)
- [ ] **6.10 (P6, MEDIUM):** Cache `strike_distance_m` and `drop_distance_m` from config at init, don't read config on web thread
- [ ] **6.11 (M4):** Add `logger.debug("No alert-class matches")` when all tracks filtered

- [ ] **Run tests and commit**

---

## Task 7: camera.py + tracker.py

**Files:**
- Modify: `hydra_detect/camera.py`, `hydra_detect/tracker.py`

### Fixes:
- [ ] **7.1 (P3, HIGH):** `switch_source()` join timeout: change `timeout=5.0` to `timeout=35.0`
- [ ] **7.2 (H2, HIGH):** Set `self._cap = None` after `self._cap.release()` in grab loop read-fail branch
- [ ] **7.3 (H4, HIGH):** In `tracker.py`, add comment documenting class_id→label 1:1 assumption
- [ ] **7.4:** In `camera.py`, add `"auto"` to the docstring for `video_standard`

- [ ] **Run tests and commit**

---

## Task 8: overlay.py

**Files:**
- Modify: `hydra_detect/overlay.py`

### Fixes:
- [ ] **8.1 (P10, LOW):** Re-clamp strike box coordinates after +2px expansion: `max(0, x1-2)`, `min(w-1, x2+2)`, etc.
- [ ] **8.2 (P13, MEDIUM):** Replace `np.zeros_like(overlay_roi)` with pre-allocated buffer or in-place `cv2.multiply(overlay_roi, 0.5, dst=overlay_roi)`
- [ ] **8.3:** No change for frame.copy() — already guarded by `if dimmed_tracks:`

- [ ] **Run tests and commit**

---

## Task 9: yolo_detector.py + config_schema.py

**Files:**
- Modify: `hydra_detect/detectors/yolo_detector.py`, `hydra_detect/config_schema.py`

### Fixes:
- [ ] **9.1 (P4, HIGH):** Batch GPU→CPU tensor copy:
```python
xyxy_np = boxes.xyxy.cpu().numpy()
conf_np = boxes.conf.cpu().numpy()
cls_np = boxes.cls.cpu().numpy().astype(int)
for i in range(len(xyxy_np)):
    x1, y1, x2, y2 = xyxy_np[i]
    conf = float(conf_np[i])
    cls_id = int(cls_np[i])
    # ... build Detection ...
```

- [ ] **9.2 (P8, MEDIUM):** Add `"auto"` to `config_schema.py` `video_standard` choices
- [ ] **9.3 (P9, LOW):** Align `yolo_imgsz` default between schema and pipeline fallback
- [ ] **9.4 (M9):** Log model class count after load: `logger.info("Model loaded: %d classes, task=%s", len(self._model.names), self._model.task)`

- [ ] **Run tests and commit**

---

## Task 10: web/server.py

**Files:**
- Modify: `hydra_detect/web/server.py`

### Fixes:
- [ ] **10.1 (W1, HIGH):** Fix rate-limit bypass in `_check_auth` (lines 181-183):
```python
failures = [t for t in _auth_failures.get(client_ip, []) if now - t < _AUTH_FAIL_WINDOW]
if failures:
    _auth_failures[client_ip] = failures
elif client_ip in _auth_failures:
    del _auth_failures[client_ip]
if len(failures) >= _AUTH_FAIL_MAX:
    return JSONResponse(...)
```
Apply same pattern to `auth_login` (lines 523-528).

- [ ] **10.2 (W3, MEDIUM):** TAK port validation:
```python
if not isinstance(port, int) or not (1 <= port <= 65535):
    return JSONResponse({"error": "port must be 1-65535"}, status_code=400)
```

- [ ] **10.3 (W4, MEDIUM):** profile_id validation:
```python
if not isinstance(profile_id, str):
    return JSONResponse({"error": "profile must be a string"}, status_code=400)
profile_id = profile_id.strip()[:100]
```

- [ ] **10.4 (W5, MEDIUM):** ZIP export symlink guard:
```python
if f.is_file() and not f.is_symlink():
```

- [ ] **10.5 (W8, LOW):** Replace `datetime.utcnow()` with `datetime.now(datetime.timezone.utc)`

- [ ] **Run tests and commit**

---

## Task 11: web/config_api.py

**Files:**
- Modify: `hydra_detect/web/config_api.py`

### Fixes:
- [ ] **11.1 (W2, HIGH):** Redact sensitive values in audit log:
```python
log_value = "[REDACTED]" if (section in REDACTED_FIELDS and key in REDACTED_FIELDS[section]) else value
audit_log.info("CONFIG WRITE: %s.%s = %s", section, key, log_value)
```

- [ ] **11.2 (W6, MEDIUM):** Populate `RESTART_REQUIRED_FIELDS["detector"]`:
```python
"detector": {"model", "device", "yolo_imgsz"},
```

- [ ] **11.3 (W7, MEDIUM):** Track changes and skip write when none:
```python
changed = False
# ... inside loop:
if old_value != value:
    config.set(section, key, value)
    changed = True
# ... after loop:
if not changed:
    return {"updated": updated, "skipped": skipped, "restart_required": False}
```

- [ ] **Run tests and commit**

---

## Task 12: RF Hunt — 13 Fixes

**Files:**
- Modify: `hydra_detect/rf/hunt.py`, `hydra_detect/rf/navigator.py`, `hydra_detect/rf/kismet_client.py`

### Critical:
- [ ] **12.1 (RF1):** In `navigator.py`, protect `bearing` and `probe_count` with `self._lock` in `next_probe()`
- [ ] **12.2 (RF2):** In `hunt.py`, use `alt or self._search_alt_m` before passing to `_geofence_waypoint`
- [ ] **12.3 (RF3):** In `_do_lost`, guard `(0.0, 0.0)`: `if blat == 0.0 and blon == 0.0: self._set_state(HuntState.ABORTED); return`
- [ ] **12.4 (RF4):** Widen callback catch to `except Exception as exc:`

### High:
- [ ] **12.5 (RF5):** In `kismet_client.py`, add staleness filter using `last_time` field
- [ ] **12.6 (RF6):** Reset `_consecutive_clips = 0` in `start()`
- [ ] **12.7 (RF7):** Use a separate counter for lost-detection instead of injecting -100 into the filter
- [ ] **12.8 (RF8):** On exhausted search, command loiter before ABORTED
- [ ] **12.9 (RF9):** Don't call `_kismet_manager.restart()` synchronously from the hunt loop — set a flag and handle in background

### Medium:
- [ ] **12.10 (RF10):** Use `time.time()` instead of `time.monotonic()` in `RSSISample.timestamp`
- [ ] **12.11 (RF11):** Log warning on `_ensure_auth()` failure in `get_wifi_rssi`/`get_sdr_rssi`
- [ ] **12.12 (RF12):** Widen `_run_loop` catch to `except Exception`
- [ ] **12.13 (RF13):** Use configured log directory instead of hardcoded `/tmp`

- [ ] **12.14:** Also reset `_navigator` and `_filter` in `start()` for clean re-runs
- [ ] **12.15:** Move `from ..autonomous import haversine_m` to module top level

- [ ] **Run tests and commit**
```bash
python -m pytest tests/test_rf_hunt.py tests/test_rf_navigator.py tests/test_rf_signal.py tests/test_rf_kismet.py -v
```

---

## Task 13: TAK Fixes

**Files:**
- Modify: `hydra_detect/tak/tak_output.py`, `hydra_detect/tak/tak_input.py`, `hydra_detect/tak/cot_builder.py`

### Fixes:
- [ ] **13.1 (T1, HIGH):** In `tak_output.py`, change `return` to `continue` on lines 251 and 267
- [ ] **13.2 (T4, MEDIUM):** Protect `_unicast_targets` with `_data_lock`:
```python
def add_unicast_target(self, host, port):
    with self._data_lock:
        target = (host, port)
        if target not in self._unicast_targets:
            self._unicast_targets.append(target)
```
Copy list under lock before iterating in `_send_cot`.

- [ ] **13.3 (T5, MEDIUM):** Gate bare "HYDRA" prefix behind config flag or remove it
- [ ] **13.4 (T6, MEDIUM):** Auto-clear `_duplicate_callsign` after 60s timeout
- [ ] **13.5 (T7, MEDIUM):** Fix CoT XML encoding:
```python
return ET.tostring(event, encoding="utf-8", xml_declaration=True)
```
(Returns bytes directly — no `.encode("utf-8")` needed)

- [ ] **13.6:** Add `is_running()` property to `TAKOutput` and `TAKInput` for pipeline.py

- [ ] **Run tests and commit**

---

## Task 14: Logging + Utility Fixes

**Files:**
- Modify: `hydra_detect/detection_logger.py`, `hydra_detect/event_logger.py`, `hydra_detect/review_export.py`, `hydra_detect/mavlink_video.py`, `hydra_detect/osd.py`

### Fixes:
- [ ] **14.1 (T3, HIGH):** In `detection_logger.py`, move `_recent.append()` to after successful `put_nowait()`:
```python
try:
    self._write_queue.put_nowait(record)
    with self._recent_lock:
        self._recent.append(record)
except queue.Full:
    logger.warning("Detection log queue full -- record dropped")
```

- [ ] **14.2 (T2, HIGH):** In `review_export.py`, wrap `S.time_start`/`S.time_end` with `esc()`:
```javascript
${{esc((S.time_start||'').slice(11,19))}}
```

- [ ] **14.3 (T8, MEDIUM):** In `event_logger.py`, hold lock for the entire read:
```python
def get_recent_events(self, limit=200):
    with self._lock:
        if self._file is None:
            return []
        filepath = self._file.name
        path = Path(filepath)
        if not path.exists():
            return []
        # Read last N lines efficiently
        lines = path.read_text().strip().splitlines()[-limit:]
    return [json.loads(l) for l in lines if l.strip()]
```

- [ ] **14.4 (T10, MEDIUM):** In `mavlink_video.py`, add startup warning:
```python
logger.warning("MAVLink video uses ENCAPSULATED_DATA which ArduPilot does not support")
```

- [ ] **14.5 (T11, MEDIUM):** Fix `set_params` interval logic:
```python
self._interval = 1.0 / max_fps  # was: min(self._interval, 1.0 / max_fps)
```

- [ ] **14.6 (T12, MEDIUM):** In `review_export.py`, add `--max-images` limit (default 100) and path traversal guard
- [ ] **14.7 (T13, MEDIUM):** In `osd.py`, use `self._mav.send_param_set()` instead of private access
- [ ] **14.8:** Fix `review_export.py` variable shadowing: rename `html` to `html_content`

- [ ] **Run tests and commit**
```bash
python -m pytest tests/test_review_export.py tests/test_tak.py -v
```

---

## Final Verification

After all tasks complete:

- [ ] `python -m pytest tests/ -v` — full suite green
- [ ] `flake8 hydra_detect/ tests/` — no new warnings
- [ ] Review diff: `git log --oneline -15` confirms 14 focused commits
