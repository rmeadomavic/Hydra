# Hydra Detect — API Reference

Every HTTP route served by `hydra_detect/web/server.py`. One section per
endpoint. Auth column:

- **none** — unauthenticated. Always works.
- **same-origin** — read-only. Dashboard works without a token; curl
  from another host needs a Bearer token.
- **bearer** — `Authorization: Bearer <token>` required unless the
  request is same-origin (dashboard).

Base URL: `http://<jetson-ip>:8080`. Substitute `<HOST>` in the cURL
examples below. When a `Bearer` header is shown, supply your token from
`[web] api_token` in `config.ini`.

## Pages (HTML)

### `GET /`
**Auth:** none · Dashboard SPA. Serves `base.html`.

### `GET /login`
**Auth:** none · Login page. Redirects to `/` if already authenticated
or if password auth is disabled.

### `GET /control`
**Auth:** none · Mobile operator control page.

### `GET /fleet`
**Auth:** none · Fleet View with per-vehicle status and abort.

### `GET /instructor`
**Auth:** none · Redirects to `/fleet` (307).

### `GET /review`
**Auth:** none · Post-sortie review page (standalone; does not share
`base.html`).

### `GET /capabilities`
**Auth:** none · Capability status page. Shows each subsystem as READY / WARN / BLOCKED / ARMED with plain-language reason strings. Polls `GET /api/capabilities` at 3s. Introduced in #146.

### `GET /setup`
**Auth:** none · First-boot setup wizard page.

---

## Auth

### `POST /auth/login`
**Auth:** none (rate-limited) · Validate password, set session cookie.

Body: `{"password": "..."}`. Response: `{"status": "ok"}` + `Set-Cookie:
hydra_session=...; HttpOnly; SameSite=Lax`. Rate-limit returns 429
after repeated failures.

```sh
curl -sX POST <HOST>/auth/login -d '{"password":"..."}' \
  -H 'Content-Type: application/json'
```

### `POST /auth/logout`
**Auth:** none · Clears session cookie.

### `GET /auth/status`
**Auth:** none · Returns `{"password_enabled": bool, "authenticated":
bool}`.

---

## Health / stats

### `GET /api/health`
**Auth:** none · Structured subsystem health for Docker HEALTHCHECK,
load balancers, and dashboards. 200 when overall `status ∈ {ok, warn}`,
503 when `status == fail`.

Returns:

```json
{
  "status": "ok|warn|fail",
  "ts": 1712345678.1,
  "subsystems": {
    "camera":   {"status": "ok|warn|fail", "detail": "..."},
    "mavlink":  {"status": "...", "detail": "..."},
    "gps":      {"status": "...", "detail": "..."},
    "detector": {"status": "...", "detail": "..."},
    "rtsp":     {"status": "...", "detail": "..."},
    "tak":      {"status": "...", "detail": "..."},
    "audit":    {"status": "...", "detail": "..."},
    "disk":     {"status": "...", "detail": "..."}
  },
  "healthy": true, "camera_ok": true, "fps": 10.2
}
```

```sh
curl -s <HOST>/api/health
```

### `GET /api/metrics`
**Auth:** none · Prometheus exposition (text format 0.0.4). Counters:
`hydra_tak_accepted_total`, `hydra_tak_rejected_total`,
`hydra_strike_events_total`, `hydra_drop_events_total`,
`hydra_hmac_invalid_total`. Gauges: `hydra_fps`, `hydra_inference_ms`,
`hydra_cpu_temp_c`, `hydra_gpu_temp_c`, `hydra_ram_pct`. Content-Type:
`text/plain; version=0.0.4; charset=utf-8`.

```sh
curl -s <HOST>/api/metrics
```

### `POST /api/client_error`
**Auth:** none (same-origin) · Frontend error sink. Rate-limited to
50 reports / 60 s / IP. Body (all fields optional): `{message, source,
lineno, colno, stack, url, timestamp}`. Returns `{"status": "ok",
"total": <ring size>}`.

### `GET /api/client_error/recent?limit=N`
**Auth:** none · Read-back of the client-error ring (max 200 entries).
Returns `{"total": int, "recent": [{ts, message, source, lineno, colno,
stack, url, remote_addr, user_agent}, ...]}`.

### `GET /api/preflight`
**Auth:** none · Structured pre-flight check results. Returns
`{"checks": [{"name": ..., "status": "pass|warn|fail", "message":
...}], "overall": "pass|warn|fail"}`.

### `GET /api/stats`
**Auth:** same-origin · Current pipeline stats + flight instruments.
Keys include: `fps`, `inference_ms`, `cpu_temp_c`, `gpu_temp_c`,
`ram_used_mb`, `ram_total_mb`, `gpu_load_pct`, `mavlink`, `gps_fix`,
`is_sim_gps`, `detector`, `rtsp_clients`, `mavlink_video_fps`,
`approach`, `rf_hunt`, `callsign`, `position`, `battery`, `heading`,
`airspeed`, `altitude`, `vertical_speed`.

```sh
curl -s <HOST>/api/stats
```

### `GET /api/logs?lines=N&level=LEVEL`
**Auth:** none · Tail `hydra.log`. `lines` 1–500, `level` one of
DEBUG/INFO/WARNING/ERROR/CRITICAL. Returns a list of
`{timestamp, level, module, message}`.

```sh
curl -s '<HOST>/api/logs?lines=100&level=WARNING'
```

---

## Configuration

### `GET /api/config`
**Auth:** none · Runtime-config dict (prompts, threshold, alert_classes).

### `POST /api/config/prompts`
**Auth:** bearer · Update detection prompt labels.
Body: `{"prompts": ["person", "car", ...]}`. Max count enforced; empty
strings rejected.

```sh
curl -sX POST <HOST>/api/config/prompts \
  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' \
  -d '{"prompts":["person","car"]}'
```

### `POST /api/config/threshold`
**Auth:** bearer · `{"threshold": 0.4}` (0.0–1.0).

### `GET /api/config/alert-classes`
**Auth:** none · Returns `{alert_classes, all_classes, categories}`.

### `POST /api/config/alert-classes`
**Auth:** bearer · `{"classes": ["person","car"]}` or `[]` for all.

### `GET /api/config/full`
**Auth:** same-origin · All `config.ini` sections as JSON with
sensitive fields redacted (`api_token`, `kismet_pass`).

### `GET /api/config/schema`
**Auth:** none · Field metadata for schema-driven UI. One entry per
key: `{type, min, max, choices, default, description}`.

### `POST /api/config/full`
**Auth:** bearer · Nested section updates, e.g.
`{"web":{"hud_layout":"operator"}}`. Runs `validate_config_updates()`
and returns `{"status":"ok","restart_required":[...]}`.

### `POST /api/config/restore-backup`
**Auth:** bearer · Restores the last backup. 404 if none exists.

### `POST /api/config/factory-reset`
**Auth:** bearer · Restore `config.ini.factory` and trigger pipeline
restart.

### `GET /api/config/export`
**Auth:** bearer · Dump current config as JSON.

### `POST /api/config/import`
**Auth:** bearer · Accept a previously exported config JSON.

---

## Vehicle control

### `POST /api/vehicle/loiter`
**Auth:** bearer · Switch to LOITER/HOLD at current position.

```sh
curl -sX POST <HOST>/api/vehicle/loiter -H 'Authorization: Bearer <token>'
```

### `POST /api/vehicle/mode`
**Auth:** bearer · `{"mode": "AUTO|RTL|LOITER|HOLD|GUIDED"}`.

### `POST /api/vehicle/beep`
**Auth:** none · Play a tune on the Pixhawk buzzer.
Body: `{"tune": "alert|success|warning|error|charles|startup"}` or a
raw QBASIC tune string ≤100 chars.

```sh
curl -sX POST <HOST>/api/vehicle/beep -d '{"tune":"charles"}' \
  -H 'Content-Type: application/json'
```

### `POST /api/abort`
**Auth:** none (safety exception) · Tries RTL → LOITER → HOLD in order.
Returns `{"status":"ok","mode":"RTL"}` on first success.

---

## Targets / approach

### `GET /api/target`
**Auth:** none · Current target lock state.

### `POST /api/target/lock`
**Auth:** bearer · `{"track_id": 5}`. 404 if track not found.

### `POST /api/target/unlock`
**Auth:** bearer · Release target lock.

### `POST /api/target/strike`
**Auth:** bearer · `{"track_id": 5, "confirm": true}`. `confirm` must
be `true`.

### `GET /api/tracks`
**Auth:** same-origin · Active tracked objects (for HUD canvas and
target selection UI). Each: `{id, label, confidence, bbox, ...}`.

### `GET /api/detections`
**Auth:** none · Recent detection-log entries.

### `GET /api/approach/status`
**Auth:** none · `{"mode": "idle|follow|drop|strike|pixel_lock",
"active": bool, ...}`.

### `POST /api/approach/follow/{track_id}`
**Auth:** bearer · Start follow mode.

### `POST /api/approach/drop/{track_id}`
**Auth:** bearer · Body `{"confirm": true}`. Start drop approach.

### `POST /api/approach/strike/{track_id}`
**Auth:** bearer · Body `{"confirm": true}`. Continuous strike approach.

### `POST /api/approach/pixel_lock/{track_id}`
**Auth:** bearer · Visual servoing pixel-lock.

### `POST /api/approach/abort`
**Auth:** bearer · Abort current approach; restore pre-approach mode.

```sh
curl -sX POST <HOST>/api/approach/abort -H 'Authorization: Bearer <token>'
```

---

## Autonomy

### `GET /api/autonomy/status`
**Auth:** same-origin · Autonomy snapshot for the `#autonomy` view.
Shape:

```json
{
  "mode": "dryrun|shadow|live",
  "enabled": true,
  "callsign": "HYDRA-1",
  "geofence": {"shape":"CIRCLE","radius_m":100.0,"center_lat":35.0527,
               "center_lon":-79.4927,"polygon":""},
  "self_position": {"lat":35.05241,"lon":-79.49305,"distance_m":38.2},
  "criteria": {"min_confidence":0.85,"min_track_frames":5,
               "strike_cooldown_sec":30.0,"gps_max_stale_sec":2.0,
               "require_operator_lock":true,
               "allowed_vehicle_modes":"AUTO",
               "allowed_classes":["mine","buoy","kayak"]},
  "gates": [{"id":"geofence","state":"PASS","detail":"38m of 100m"},
            {"id":"vehicle_mode","state":"PASS","detail":"AUTO"},
            {"id":"operator_lock","state":"FAIL","detail":"no soft-lock"},
            {"id":"gps_fresh","state":"PASS","detail":"fix age 0.4s"},
            {"id":"cooldown","state":"N/A","detail":"no prior strike"}],
  "log": [{"ts":"06:41:22","track_id":7,"label":"kayak",
           "action":"reject","reason":"operator_soft_lock required"}]
}
```

When no controller is registered the endpoint returns an idle default
(all five gates `N/A`, empty `log`) — shape identical so the frontend
has no branches.

### `POST /api/autonomy/mode`
**Auth:** bearer · `{"mode": "dryrun|shadow|live"}`.

```sh
curl -sX POST <HOST>/api/autonomy/mode -d '{"mode":"shadow"}' \
  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json'
```

---

## TAK

### `GET /api/tak/status`
**Auth:** none · `{enabled, running, callsign, events_sent}`.

### `POST /api/tak/toggle`
**Auth:** bearer · `{"enabled": true|false}`.

### `GET /api/tak/commands?limit=N`
**Auth:** same-origin · Inbound GeoChat command feed.
`limit` default 100, capped at 500. Shape:

```json
{
  "enabled": true,
  "commands": [
    {"ts":1713569872.1,"sender":"CERBERUS","action":"geochat",
     "accepted":true,"hmac_state":"verified",
     "raw":"...","track_id":null,"reject_reason":null}
  ],
  "allowed_callsigns": ["ALPHA","BRAVO"],
  "hmac_enforced": true,
  "duplicate_callsign_alarm": false,
  "limit": 100
}
```

### `GET /api/tak/type_counts?window_seconds=N`
**Auth:** same-origin · Inbound CoT type histogram. `window_seconds`
default 900, capped at 3600. Shape:

```json
{"enabled": true, "counts": {"a-f-G-U-C":4, "b-m-G":2},
 "total": 6, "window_seconds": 900}
```

### `GET /api/tak/peers`
**Auth:** same-origin · Peer roster + unicast targets + security flags.
Shape:

```json
{"enabled": true,
 "peers": [{"callsign":"CERBERUS","last_seen":1713569872,
            "lat":35.05,"lon":-79.49}],
 "unicast_targets": ["10.0.0.5:6969"],
 "hmac_enforced": true,
 "duplicate_callsign_alarm": false,
 "allowed_callsigns": ["ALPHA","BRAVO"]}
```

### `GET /api/tak/targets`
**Auth:** none · List current unicast targets (also exposed inside
`/api/tak/peers.unicast_targets`).

### `POST /api/tak/targets`
**Auth:** bearer · `{"host": "10.0.0.5", "port": 6969}`.

### `DELETE /api/tak/targets`
**Auth:** bearer · Same body as POST.

---

## Audit

### `GET /api/audit/summary?window_seconds=N&recent=N`
**Auth:** same-origin · Roll-up of recent audit events. Merges TAK
commands, HMAC rejections, approach arm/abort, strike/drop.
`window_seconds` default 3600 (cap 86400). `recent` default 50 (cap
200). Returns a summary object with per-action counts plus the recent
tail.

```sh
curl -s '<HOST>/api/audit/summary?window_seconds=3600&recent=20'
```

---

## RF hunt

### `GET /api/rf/status`
**Auth:** none · Hunt state-machine snapshot. `{state, ...}`.

### `GET /api/rf/rssi_history`
**Auth:** none · Bounded ring of RSSI samples for charting.

### `GET /api/rf/ambient_scan`
**Auth:** same-origin · Recent ambient SDR samples for the cockpit
ticker. Shape:

```json
{"enabled": true,
 "samples": [{"type":"wifi","mac":"AA:BB:CC:DD:EE:FF","name":"ssid",
              "vendor":"vendor","rssi_dbm":-62,"ts":1713569872}],
 "window_seconds": 60,
 "max_rssi": -42}
```

### `GET /api/rf/devices`
**Auth:** none · Current Kismet device feed, normalized. Payload:
`{mode: "live"|"replay"|"unavailable",
 devices: [{bssid, ssid, rssi, channel, freq_mhz, manuf,
            first_seen, last_seen, lat, lon, is_target}, ...]}`.
Replay fixtures are wired up when `[rf_homing] replay_path` is set
and the live Kismet API is unreachable — tabletop demos work with
no hardware.

### `GET /api/rf/events`
**Auth:** none · State-transition ring (≤50 entries) for the
dashboard hunt timeline. Each entry:
`{t, from, to, samples, elapsed_prev_sec}`.

### `POST /api/rf/target`
**Auth:** bearer · One-click hunt target — used by the device-feed
table. Body: `{mode?: "wifi"|"sdr", bssid?: str, freq_mhz?: float,
confirm: bool}`. Either `bssid` or `freq_mhz` is required;
`confirm=true` is mandatory (prevents accidental hunts). Reuses the
same validation + controller rebuild path as `POST /api/rf/start`.

### `POST /api/rf/start`
**Auth:** bearer · Body: `{mode, target_bssid, target_freq_mhz,
search_pattern, search_area_m, search_spacing_m, search_alt_m,
rssi_threshold_dbm, rssi_converge_dbm, gradient_step_m}` — all
optional. `mode` is `wifi` or `sdr`; BSSID required for wifi.

### `POST /api/rf/stop`
**Auth:** bearer · Stop active hunt.

---

## Servo

### `GET /api/servo/status`
**Auth:** same-origin · Current pan/tilt state for the cockpit dial.
Shape:

```json
{"enabled": true,
 "pan_deg": 12.4, "tilt_deg": -3.1,
 "pan_limit_min": -90.0, "pan_limit_max": 90.0,
 "tilt_limit_min": -30.0, "tilt_limit_max": 60.0,
 "scanning": false,
 "locked_track_id": 7}
```

Idle default when no tracker is registered — `enabled:false` with
zeroed angles.

---

## Camera / models / profiles

### `GET /api/camera/sources`
**Auth:** none · Available video sources.

### `POST /api/camera/switch`
**Auth:** bearer · `{"source": 2}` or `{"source": "rtsp://..."}`.

### `GET /api/models`
**Auth:** none · Available YOLO model files under `models/`.

### `POST /api/models/switch`
**Auth:** bearer · `{"model": "yolov8s.pt"}`. Path traversal rejected.

### `GET /api/profiles`
**Auth:** none · Saved mission profiles + active profile.

### `POST /api/profiles/switch`
**Auth:** bearer · `{"profile": "<id>"}`.

### `GET /api/mission-profiles`
**Auth:** none · Built-in presets (RECON / DELIVERY / STRIKE). Returns
a dict keyed by profile name with `display_name`, `description`,
`behavior`, `approach_method`, `post_action`, `icon`.

---

## System / power

### `GET /api/system/power-modes`
**Auth:** none · Available Jetson power modes.

### `POST /api/system/power-mode`
**Auth:** bearer · `{"mode_id": 0}`.

---

## Pipeline control

### `POST /api/restart`
**Auth:** bearer · Restart the pipeline loop. Does not restart the
Python process; code changes require a container restart.

### `POST /api/pipeline/stop`
**Auth:** bearer · Graceful shutdown.

### `POST /api/pipeline/pause`
**Auth:** bearer · `{"paused": true}` pauses, `{"paused": false}`
resumes.

---

## Stream output

### `GET /stream.jpg?raw=0|1`
**Auth:** none · Single JPEG frame. `?raw=1` returns the
un-annotated frame (the Ops HUD uses this so its canvas-drawn boxes
do not double up with the server overlay). Cached for ~33 ms to
absorb rapid polls.

### `GET /stream.mjpeg`
**Auth:** none · MJPEG multipart stream. Fallback — prefer snapshot
polling from `/stream.jpg` since `BaseHTTPMiddleware` in some
Starlette versions hangs multipart responses.

### `GET /api/stream/quality`
**Auth:** none · `{"quality": 70}`.

### `POST /api/stream/quality`
**Auth:** none (display preference, not a control action) ·
`{"quality": 70}` (1–100).

---

## RTSP / MAVLink video

### `GET /api/rtsp/status`
**Auth:** none · `{enabled, running, url, clients}`.

### `POST /api/rtsp/toggle`
**Auth:** bearer · `{"enabled": true|false}`.

### `GET /api/mavlink-video/status`
**Auth:** none · `{enabled, running, width, height, quality,
current_fps, bytes_per_sec}`.

### `POST /api/mavlink-video/toggle`
**Auth:** bearer · `{"enabled": true|false}`.

### `POST /api/mavlink-video/tune`
**Auth:** bearer · Live-tune params. Body: `{width, height, quality,
max_fps}` — all optional; ranges: width 40–320, height 30–240,
quality 5–50, max_fps 0.1–5.0.

---

## Events / missions

### `GET /api/events`
**Auth:** none · Timeline for current/most recent mission.

### `GET /api/events/status`
**Auth:** none · `{mission_active, mission_name}`.

### `POST /api/mission/start`
**Auth:** bearer · `{"name": "mission-alpha"}` (optional; defaults
to `mission-<unix>`).

### `POST /api/mission/end`
**Auth:** bearer · End the active mission.

---

## Post-mission review

### `GET /api/review/logs`
**Auth:** none · List available detection + event-timeline log files.
Returns `{logs:[{filename,size_kb,modified}], event_logs:[...],
image_dir:"..."}`.

### `GET /api/review/log/{filename}`
**Auth:** none · Parse and return detections from a log file (JSONL
or CSV). Capped at 50k records; `truncated: true` when hit.

### `GET /api/review/events/{filename}`
**Auth:** none · Events from an event-timeline JSONL file. Same 50k
cap.

### `GET /api/review/waypoints/{filename}?classes=a,b&alt_m=15`
**Auth:** none · Export waypoints (QGC WPL 110 format) from a saved
log. `classes` is an optional comma-separated filter, `alt_m` is the
waypoint altitude.

### `GET /api/review/images/{filename}`
**Auth:** none · Serve a saved detection JPEG. Path traversal rejected.

### `GET /api/export`
**Auth:** bearer · Download a ZIP of the current session (logs +
images). Cleaned up via `BackgroundTask` after the response completes.

### `GET /api/export/waypoints?classes=a,b&alt_m=15`
**Auth:** bearer · Export current GPS-tagged detections as
QGC WPL 110.

---

## Setup wizard

### `GET /api/setup/devices`
**Auth:** none · List `/dev/video*` and serial ports matching
`ttyACM|ttyUSB|ttyTHS|ttyAMA`.

### `POST /api/setup/save`
**Auth:** bearer when a token is configured; none on first boot.
Body: `{camera_source, serial_port, vehicle_type, team_number,
callsign}`. `vehicle_type ∈ {drone,usv,ugv,fw}`. Auto-builds
`HYDRA-<team>-<VEHICLE>` callsign when one is not given. Triggers a
pipeline restart on success.

---

## Capability Status

### `GET /api/capabilities`
**Auth:** none · Returns readiness status for all registered subsystems.
Response cached for 2s. Body: `{generated_at: ISO8601, capabilities:
[{name, status, reasons: string[], fix_target: string|null}]}`.
`status ∈ {READY, WARN, BLOCKED, ARMED}`. Introduced in #146.
