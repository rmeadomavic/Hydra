# Code Review Tracker

Systematic review of the Hydra Detect v2.0 codebase, ordered by risk priority.
Safety-critical modules first, then vehicle comms, detection, web, and support.

> **How to use:** Work through chunks in order. Mark status as each review completes.
> After review, file issues or fix inline — link PRs in the Notes column.

| Status | Meaning |
|--------|---------|
| `[ ]`  | Not started |
| `[~]`  | In progress |
| `[x]`  | Complete |

---

## Chunk 1 — Core Pipeline (safety-critical hot loop)

**Risk:** HIGHEST — this is the real-time detect/track/alert loop. Bugs here
affect timing, thread safety, and fail-safe behavior on live vehicles.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/pipeline.py` | 2618 | Thread safety, bounded queues, no blocking I/O in hot path, config fallback alignment, callback crash isolation, GIL contention |
| `[ ]` | `hydra_detect/__main__.py` | 103 | Entry point arg parsing, graceful shutdown, signal handling |

**Total LOC:** 2,721

---

## Chunk 2 — Camera & Tracking (safety-critical input)

**Risk:** VERY HIGH — camera loss or tracker corruption directly impacts
detection reliability and can trigger false engagements.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/camera.py` | 459 | Thread-safe capture, reconnect logic, resource cleanup, V4L2/RTSP/GStreamer edge cases |
| `[ ]` | `hydra_detect/tracker.py` | 157 | ByteTrack wrapper correctness, track ID stability, memory bounds |
| `[ ]` | `hydra_detect/overlay.py` | 204 | Bounding box clamping to frame bounds, OpenCV crash prevention |

**Total LOC:** 820

---

## Chunk 3 — Autonomous & Engagement (safety-critical vehicle control)

**Risk:** VERY HIGH — these modules command vehicle movement, weapon release,
and strike authorization. Bugs can cause uncontrolled flight or unintended drops.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/autonomous.py` | 372 | Geofence enforcement, arm/disarm state machine, fail-safe on comms loss |
| `[ ]` | `hydra_detect/approach.py` | 563 | GUIDED mode confirmation before commit, abort restores pre-approach mode, lock rollback |
| `[ ]` | `hydra_detect/guidance.py` | 168 | Velocity math correctness, output clamping, no NaN/inf propagation |
| `[ ]` | `hydra_detect/servo_tracker.py` | 134 | PWM bounds, servo rate limiting, fail-safe position |
| `[ ]` | `hydra_detect/dogleg_rtl.py` | 179 | Waypoint sequencing, altitude floors, geofence interaction |
| `[ ]` | `hydra_detect/mission_profiles.py` | 84 | Profile validation, no unsafe default overrides |

**Total LOC:** 1,500

---

## Chunk 4 — MAVLink & Vehicle Comms (safety-critical comms)

**Risk:** HIGH — MAVLink is the vehicle control channel. Dropped heartbeats,
bad GPS, or malformed commands can cause flyaways or loss of control.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/mavlink_io.py` | 1069 | Public API usage (no `_mav` access), send_lock thread safety, heartbeat timeout, GPS validity checks |
| `[ ]` | `hydra_detect/mavlink_video.py` | 192 | Telemetry bandwidth, no ENCAPSULATED_DATA, thumbnail size bounds |
| `[ ]` | `hydra_detect/geo_tracking.py` | 116 | CAMERA_TRACKING_GEO_STATUS correctness, coordinate transforms |
| `[ ]` | `hydra_detect/osd.py` | 271 | OSD mode dispatch, statustext flood prevention, named_value encoding |
| `[ ]` | `hydra_detect/msp_displayport.py` | 341 | MSP v1 protocol correctness (protocol 42), byte framing, serial error handling |

**Total LOC:** 1,989

---

## Chunk 5 — Detection & Model (core ML)

**Risk:** MEDIUM-HIGH — model loading, inference correctness, and class mapping
affect detection quality. Memory leaks here exhaust Jetson shared RAM.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/detectors/yolo_detector.py` | 139 | GPU memory lifecycle, no unnecessary .cpu() transfers, inference error handling |
| `[ ]` | `hydra_detect/detectors/base.py` | 67 | Interface stability, contract completeness |
| `[ ]` | `hydra_detect/model_manifest.py` | 206 | Hash verification, class extraction from .pt/.engine/.onnx, manifest integrity |
| `[ ]` | `hydra_detect/config_schema.py` | 1178 | Type validation coverage, default-fallback alignment, range bounds |
| `[ ]` | `hydra_detect/profiles.py` | 57 | JSON profile loading, schema validation |

**Total LOC:** 1,647

---

## Chunk 6 — RF Hunt Subsystem

**Risk:** MEDIUM — RF hunt commands vehicle navigation via RSSI gradient ascent.
Bugs can send vehicle to wrong location or stall the state machine.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/rf/hunt.py` | 641 | State machine transitions, geofence clipping, timeout handling |
| `[ ]` | `hydra_detect/rf/navigator.py` | 164 | `best_position` None check, guided_to safety, sample_count > 0 guard |
| `[ ]` | `hydra_detect/rf/kismet_client.py` | 235 | REST API error handling, auth, connection timeouts |
| `[ ]` | `hydra_detect/rf/kismet_manager.py` | 272 | Subprocess cleanup (try/finally), orphan process prevention, dual-instance check |
| `[ ]` | `hydra_detect/rf/search.py` | 102 | Pattern generator bounds, waypoint validity |
| `[ ]` | `hydra_detect/rf/signal.py` | 54 | RSSI filtering, gradient NaN handling |
| `[ ]` | `hydra_detect/rf/rtl_power_client.py` | 138 | Subprocess lifecycle, output parsing robustness |

**Total LOC:** 1,606

---

## Chunk 7 — TAK / CoT Integration

**Risk:** MEDIUM — TAK feeds the common operating picture. Malformed CoT or
auth bypass could mislead operators or expose position data.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/tak/tak_output.py` | 311 | Multicast/unicast thread safety, socket cleanup, XML well-formedness |
| `[ ]` | `hydra_detect/tak/tak_input.py` | 498 | Command authentication, input validation, GeoChat parsing |
| `[ ]` | `hydra_detect/tak/cot_builder.py` | 189 | MIL-STD-2525 compliance, coordinate precision, XML escaping |
| `[ ]` | `hydra_detect/tak/type_mapping.py` | 46 | YOLO class to 2525 mapping completeness |

**Total LOC:** 1,044

---

## Chunk 8 — Web Server & API (attack surface)

**Risk:** MEDIUM — 70+ endpoints exposed on the network. XSS, auth bypass,
and OOM from unbounded responses are the main concerns.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/web/server.py` | 2506 | `_parse_json` usage on all POSTs, `_check_auth` same-origin logic, `_auth_failures` pruning, 50k record caps, no innerHTML sinks |
| `[ ]` | `hydra_detect/web/config_api.py` | 252 | File locking, config write validation, race conditions |
| `[ ]` | `hydra_detect/tls.py` | 41 | Certificate generation, key permissions |
| `[ ]` | `hydra_detect/system.py` | 198 | Command injection in power mode / system calls |

**Total LOC:** 2,997

---

## Chunk 9 — Web Frontend (JS/HTML/CSS)

**Risk:** LOW-MEDIUM — frontend runs on operator devices. XSS and wrong-target-lock
from stale DOM are the main risks.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/web/static/js/ops.js` | 726 | Canvas hit-testing, letterbox coord mapping, no innerHTML, click-to-lock race conditions |
| `[ ]` | `hydra_detect/web/static/js/config.js` | 1689 | Panel state management, event listener stacking prevention |
| `[ ]` | `hydra_detect/web/static/js/settings.js` | 723 | Schema-driven form generation, event delegation, no duplicate listeners |
| `[ ]` | `hydra_detect/web/static/js/app.js` | 764 | Router, polling lifecycle (pause on hidden/view-switch), error backoff |
| `[ ]` | `hydra_detect/web/static/js/review-map.js` | 398 | User data escaping in map popups |
| `[ ]` | `hydra_detect/web/static/js/instructor.js` | 198 | Mission control actions, abort reliability |
| `[ ]` | `hydra_detect/web/static/js/control.js` | 162 | Vehicle control inputs, confirmation dialogs |
| `[ ]` | `hydra_detect/web/templates/*.html` | ~1200 | CSP compliance (no inline scripts), external JS only |

**Total LOC:** ~5,860

---

## Chunk 10 — Logging, Export & Support Utilities

**Risk:** LOW — these modules support after-action review and operational logging.
Main risks are data integrity (hash chain) and path traversal in file access.

| Status | File | LOC | Focus Areas |
|--------|------|----:|-------------|
| `[ ]` | `hydra_detect/detection_logger.py` | 529 | Background writer thread safety, rotation, log index seeding, disk full handling |
| `[ ]` | `hydra_detect/event_logger.py` | 160 | 1 Hz vehicle track, bounded file size, JSON serialization |
| `[ ]` | `hydra_detect/verify_log.py` | 93 | SHA-256 hash chain correctness, tamper detection |
| `[ ]` | `hydra_detect/review_export.py` | 264 | HTML escaping via `esc()`, `</script>` breakout prevention, path traversal in file access |
| `[ ]` | `hydra_detect/waypoint_export.py` | 81 | QGC WPL 110 format correctness |
| `[ ]` | `hydra_detect/rtsp_server.py` | 177 | GStreamer pipeline cleanup, port conflicts |

**Total LOC:** 1,304

---

## Summary

| Chunk | Category | LOC | Risk | Status |
|------:|----------|----:|------|--------|
| 1 | Core Pipeline | 2,721 | HIGHEST | `[ ]` |
| 2 | Camera & Tracking | 820 | VERY HIGH | `[ ]` |
| 3 | Autonomous & Engagement | 1,500 | VERY HIGH | `[ ]` |
| 4 | MAVLink & Vehicle Comms | 1,989 | HIGH | `[ ]` |
| 5 | Detection & Model | 1,647 | MEDIUM-HIGH | `[ ]` |
| 6 | RF Hunt Subsystem | 1,606 | MEDIUM | `[ ]` |
| 7 | TAK / CoT Integration | 1,044 | MEDIUM | `[ ]` |
| 8 | Web Server & API | 2,997 | MEDIUM | `[ ]` |
| 9 | Web Frontend | ~5,860 | LOW-MEDIUM | `[ ]` |
| 10 | Logging, Export & Utils | 1,304 | LOW | `[ ]` |
| | **Total** | **~21,488** | | |
