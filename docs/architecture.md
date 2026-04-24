# Hydra Detect — Architecture

One-page tour of how Hydra is wired. For endpoint detail see
[api-reference.md](api-reference.md); for operator-visible behavior see
[dashboard-user-guide.md](dashboard-user-guide.md).

## Data flow

```
                 ┌──────────────┐
                 │   Camera     │  USB / RTSP / GStreamer / file
                 │  camera.py   │
                 └──────┬───────┘
                        │ frames (BGR ndarray)
                        ▼
                 ┌──────────────┐
                 │   Detector   │  YOLO (ultralytics) — GPU inference
                 │ detectors/*  │  classes: YAML model manifest
                 └──────┬───────┘
                        │ detections (boxes + conf + class)
                        ▼
                 ┌──────────────┐
                 │   Tracker    │  ByteTrack via supervision
                 │  tracker.py  │  persistent track IDs
                 └──────┬───────┘
                        │ Track[]  (id, bbox, class, hits)
                        ▼
                 ┌──────────────┐
                 │   Overlay    │  draws boxes onto the frame for stream
                 │  overlay.py  │
                 └──────┬───────┘
                        │
             ┌──────────┴──────────────────────┐
             │  pipeline.py — main loop        │
             │  writes into StreamState        │
             └──┬───────┬──────┬───────┬───────┘
                │       │      │       │
     frame ▼   ▼ tracks  ▼ stats  ▼ logs
  ┌──────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐
  │ StreamState │ FastAPI │ Event   │ DetectionLog  │
  │ (thread-    │ layer   │ Logger  │ (JSONL + CSV, │
  │  safe)      │         │         │  hash-chain)  │
  └──┬────┬──┘ └──┬──┬──┬─┘ └────────┘ └──────────────┘
     │    │      │  │  │
     │ /stream.jpg       │
     │    │      │  │  └── /api/events (timeline)
     │    │      │  └───── /api/stats (flight + health)
     │    │      └──────── /api/tracks (for HUD canvas)
     │    │
     │    └── MAVLink publisher (mavlink_io.py)
     │         · STATUSTEXT alerts
     │         · CONDITION_YAW / mode changes
     │         · MAVLink video thumbnails (mavlink_video.py)
     │         · FPV OSD (osd.py, msp_displayport.py)
     │
     └── TAK output (tak/tak_output.py)
          · multicast 239.2.3.1:6969
          · unicast targets (managed via /api/tak/targets)
          · CoT detection markers + self-SA

  ┌──────────────┐
  │ TAK input    │  ← ATAK GeoChat commands (tak/tak_input.py)
  │              │      HMAC-verified, bounded ring buffer
  └──────┬───────┘
         │
         ▼ commands
   pipeline.py callbacks (on_loiter / on_follow / …)

  ┌──────────────┐
  │ RF hunt      │  ← Kismet (WiFi) or rtl_power (SDR)
  │ rf/hunt.py   │     RSSI → gradient ascent → MAVLink nav
  └──────┬───────┘
         │ samples
         ▼
     /api/rf/status, /api/rf/rssi_history, /api/rf/ambient_scan

  ┌──────────────┐
  │ Autonomy     │  consumes Track[] + GPS + config
  │ autonomous.py│  runs 5 gates per candidate
  └──────┬───────┘
         │ decisions (engage / reject / defer)
         ▼
     /api/autonomy/status  (gates + rolling log)
```

## Module ownership

| Module group | Files | Owns |
|--------------|-------|------|
| Orchestrator | `pipeline.py`, `__main__.py` | The hot loop. Wires every subsystem together. Fails safe if a subsystem crashes. |
| Input | `camera.py` | Frame acquisition. Handles USB / RTSP / GStreamer / file sources behind one interface. |
| Detection | `detectors/base.py`, `detectors/yolov8.py`, `detectors/yolov11.py` | Model loading, inference, TensorRT compilation if `.engine` is present. |
| Tracking | `tracker.py`, `overlay.py` | Persistent IDs. Drawing boxes on frames. |
| Vehicle | `approach.py`, `guidance.py`, `autonomous.py`, `dogleg_rtl.py` | Everything that can move the vehicle. Kept separate from detection so a broken detector cannot command flight. |
| MAVLink | `mavlink_io.py`, `mavlink_video.py`, `osd.py`, `msp_displayport.py`, `geo_tracking.py` | All pymavlink traffic. Public API: `send_raw_message`, `send_param_set`, `get_flight_data`, plus callbacks set by `pipeline.py`. |
| TAK | `tak/tak_output.py`, `tak/tak_input.py`, `tak/cot_builder.py`, `tak/type_mapping.py` | CoT multicast + unicast, GeoChat listener, MIL-STD-2525 mapping. |
| RF | `rf/hunt.py`, `rf/navigator.py`, `rf/signal.py`, `rf/kismet_client.py`, `rf/kismet_manager.py`, `rf/search.py` | RSSI sourcing, search patterns, gradient ascent. |
| Web | `web/server.py`, `web/config_api.py` | FastAPI routes. `web/server.py` is the only process-local sink for dashboard polls. |
| Logging | `detection_logger.py`, `event_logger.py`, `verify_log.py`, `review_export.py` | JSONL + CSV + hash chain, timeline events, post-mission HTML reports. |
| Config | `config_schema.py`, `profiles.py`, `mission_profiles.py` | Typed schema, mission profile loading, RECON / DELIVERY / STRIKE presets. |

## How the FastAPI layer talks to the pipeline

`web/server.py` runs on a daemon thread spawned by `run_server()`; the
pipeline runs on the main thread. They share one object:

```python
# web/server.py
stream_state = StreamState()  # module-global, thread-safe via Lock
```

`StreamState` holds:

- the latest frame (`get_frame()` / `get_raw_frame()`)
- the latest stats dict (`get_stats()`)
- the current target lock (`get_target_lock()`)
- the runtime-config dict (prompts, threshold, alert classes)
- a dict of callbacks the pipeline registers via `set_callbacks(...)`

Pipeline path for a dashboard write:

1. Browser POSTs to (for example) `/api/vehicle/loiter`.
2. FastAPI handler validates the body and auth, then calls
   `stream_state.get_callback("on_loiter_command")`.
3. If the callback is registered (pipeline has booted, MAVLink is up),
   the handler invokes it. The callback dispatches to
   `mavlink_io.command_loiter()`.
4. The handler returns 200 or a structured error. Every control action
   is written to the `hydra.audit` logger via `_audit(request, ...)`.

Pipeline path for a dashboard read:

1. Browser GETs `/api/stats`.
2. Handler calls `stream_state.get_stats()`, which returns a copy of
   the stats dict (held under a `threading.Lock`).
3. Handler projects flight-instrument fields onto the response via
   `_flight_fields()` — if MAVLink is registered via
   `set_mavlink(mav)`, heading / airspeed / altitude / vertical_speed
   are pulled from `mav.get_flight_data()`; otherwise they return as
   `None` and the HUD renders a dash.

Side references for the new views:

- `set_tak_input(tak_in)` — wires `/api/tak/commands`,
  `/api/tak/type_counts`, `/api/tak/peers`.
- `set_tak_output(tak_out)` — wires the unicast-target roll-up.
- `set_servo_tracker(servo)` — wires `/api/servo/status`.
- `set_rf_ambient_scan(scanner)` — wires `/api/rf/ambient_scan`.
- `set_autonomous_controller(ctrl)` — wires `/api/autonomy/status`
  and `/api/autonomy/mode`.

Every one of those setters accepts `None` to detach. When the handle
is `None` the endpoint returns a shape-identical idle response so the
frontend has no branches.

## Threading rules

- The detect loop runs on the main thread.
- MAVLink heartbeats run on a background thread owned by
  `mavlink_io.MAVLinkIO`.
- TAK input runs on its own socket thread.
- Kismet polling runs on an `rf.kismet_poller` thread.
- Uvicorn runs on the `hydra-web` daemon thread.
- All shared state lives in `StreamState` or the controller classes
  and is guarded by `threading.Lock` — not `asyncio`. `asyncio.Event`
  is **not** safe to signal between the pipeline thread and the
  uvicorn event loop; use a `threading.Event` or a poll instead.

## Fail-safe invariants

- `/api/abort` is unauthenticated and tries RTL → LOITER → HOLD in
  order. Any callback exception is caught; the endpoint must return a
  response so range control knows whether the abort landed.
- Approach controllers always restore `_pre_approach_mode` on abort,
  not a hardcoded LOITER.
- Autonomy snapshots return an idle default on any exception — the
  dashboard keeps rendering even if the controller misbehaves.
- MJPEG uses pure-ASGI middleware because `BaseHTTPMiddleware` wraps
  `StreamingResponse` bodies and hangs. Snapshot polling
  (`/stream.jpg`) is the primary path; `/stream.mjpeg` is a fallback.

## Where to go next

- [api-reference.md](api-reference.md) — every endpoint, method,
  auth, and response shape.
- [dashboard-user-guide.md](dashboard-user-guide.md) — operator's
  walkthrough of each tab.
- [preservation-rules.md](preservation-rules.md) — things that look
  unused but are not. Read before deleting anything unfamiliar.
- [configuration.md](configuration.md) — every `config.ini` key.
- [vehicle-control.md](vehicle-control.md) — follow / drop / strike
  mode mechanics.
- [autonomous-operations.md](autonomous-operations.md) — geofencing
  and the five gates in full.
