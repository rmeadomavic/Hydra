# Hydra Dashboard — User Guide

Everything an operator needs to run Hydra from the web dashboard. Open
`http://<jetson-ip>:8080/` on a laptop or tablet on the same network.
Four tabs along the top. URL hash controls which one is visible
(`#ops`, `#tak`, `#config`, `#settings`). If you type no hash, you
land on `#ops`. Old `#autonomy` and `#systems` URLs redirect to
`#config` and `#settings` respectively (their content was folded in).

All views share the top bar (brand + callsign) and the bottom footer
(`UNCLASSIFIED` centered, `SORCC Payload Integrator` right). The
callsign is pulled from `[tak] callsign` in `config.ini` on the first
poll of `/api/stats`.

---

## `#ops` — Operator HUD

The default view. Live video covers the center. Bounding boxes are
drawn on a canvas on top of the video — clicking a box pops a radial
menu with Follow / Cue / Drop / P-Lock / Loiter / Lock. Cue and
Drop require a confirm overlay before any MAVLink command goes out.

### FlightHUD rail (160 px, right of the video)

The rail has a header (`FLIGHT HUD · <layout>`) with a dropdown that
switches between four layouts:

- `classic` — HDG tape + SPD/ALT VTapes + gimbal + target + status strip
- `operator` — four ReadoutCards (Battery, Link, Position, GPS)
- `graphs` — same ReadoutCards but sparkline-only
- `hybrid` — HDG + tapes + ReadoutCards side-by-side

The layout is persisted via `/api/config/full` under `[web] hud_layout`
and survives a restart. Data source for every tape / card:

| Zone | Source | Cadence |
|------|--------|---------|
| HDG tape | `/api/stats.heading` | 500 ms |
| SPD / ALT VTapes | `/api/stats.speed`, `.altitude` | 500 ms |
| Battery card | `/api/stats.battery` | 500 ms |
| Link card | RF RSSI (`/api/rf/status.current_rssi`) | 500 ms |
| Position card | `/api/stats.position` (SIM suffix if simulated) | 500 ms |
| GPS card | `/api/stats.gps_sats` + `.gps_fix` | 500 ms |
| Gimbal | `/api/servo/status.pan_deg/.tilt_deg` | 1 Hz |
| Target | `/api/target` (current lock) | 500 ms |

If a field is `--` it means the upstream sensor has not reported yet
(GPS still acquiring, MAVLink not connected, no target lock, etc.).

### Cockpit strip (220 px, below the video)

Three cells, left to right:

1. **Servo dial** — half-arc 0–180°, ticks every 15°. The needle shows
   current pan; color goes olive (normal) → amber (locked) → red
   (strike). Pulls from `/api/servo/status`. Watermark says
   "DERIVED VALUE" because the pan/tilt values are computed from the
   target centroid, not read from a physical encoder.
2. **TAK mini-map** — 20 px grid + three dashed range rings. Self
   marker spins; peer markers are sky-blue. Click the `⇱` pill to
   jump to `#tak`. Data: `/api/tak/peers` at 1 Hz.
3. **Cockpit SDR** — animated spectrum bars on the left
   (cosmetic — tied to poll tick), device list on the right. List
   columns: TYPE / NAME / MAC / VENDOR / dBm. Data:
   `/api/rf/ambient_scan` at 1 Hz.

### Sidebar (360 px, far right)

Fixed cards: TRACKS, APPROACH (only while a mode is active), RF HUNT,
MISSION, PIPELINE, VEHICLE, MAP, DETECTION LOG. Buttons here POST to
the vehicle control endpoints; see the API reference.

### Video interactions

- **Click a bounding box** → radial action menu at cursor. Strike and
  Drop go through a second confirm overlay.
- **Double-click the video** → browser fullscreen toggle. Works on
  both the MJPEG and snapshot-poll paths.

---

## `#tak` — TAK view

Three-column grid. Center column is the inbound GeoChat feed; left and
right are backend-backed panels that replaced the previous
"not yet built" stubs.

- **Left — Type Counts**: inbound CoT type histogram over a 900 s
  rolling window. Backend: `/api/tak/type_counts`.
- **Center — Inbound Commands**: live list of CoT commands received
  from ATAK, newest on top. Each row shows timestamp, sender
  callsign, action verb, accepted/rejected pill, HMAC chip (when
  verified), and the raw CoT (truncated, full string on hover). The
  chat-log scroll-pins to the bottom unless the operator scrolls up.
  Source: `/api/tak/commands` at 1 Hz.
- **Right — Peers**: current roster of ATAK callsigns seen, unicast
  target list, HMAC-enforced chip, and a duplicate-callsign alarm.
  Source: `/api/tak/peers`.
- **Footer — Audit Summary**: rolling counts of TAK rejections,
  approach arm/abort, strike/drop events over the last hour. Source:
  `/api/audit/summary`.

The center column is a pulsing-tone LIVE chip when the feed is healthy.
If the `/api/tak/commands` poll errors, the view backs off
exponentially (1 s → 2 s → 4 s, cap 10 s).

---

## Autonomy dashboard (inside `#config`)

Safety-critical readout for the autonomous-strike pipeline. Lives at
the bottom of the Config tab now (formerly its own `#autonomy` tab).
Shows what autonomy would have done; the actual mode switch is the
only write surface in this view.

### Mode picker

Three buttons: `DRY RUN` / `SHADOW` / `LIVE`. Default is `dryrun`.
Switching modes:

- `dryrun` → `shadow` — single confirm modal.
- `shadow` → `live` — **two-step confirm**. Step 1 requires the
  operator to type the exact callsign (fetched from status). Step 2
  is a final `ARM LIVE` button. Nothing commits until both pass.
- Any mode change POSTs to `/api/autonomy/mode` with Bearer auth. If
  the backend is unreachable, the picker snaps back to the last
  confirmed mode and toasts `CONNECTION LOST — mode retained: <X>`.

Mode never silently advances. A network error is not a mode change.

### Gate panel (5 rows)

Each gate shows `PASS` / `FAIL` / `N/A` + a short detail string.
Source: `/api/autonomy/status.gates[]`. The five gates:

1. `geofence` — target inside the configured geofence (circle or
   polygon).
2. `vehicle_mode` — vehicle is in one of `allowed_vehicle_modes`.
3. `operator_lock` — operator has soft-locked this track (if
   `require_operator_lock` is on).
4. `gps_fresh` — GPS fix is newer than `gps_max_stale_sec`.
5. `cooldown` — `strike_cooldown_sec` has elapsed since the last
   strike.

If all five gates PASS and the mode is `live`, autonomy will engage.
Anything else is a block.

### Geofence preview

Inline SVG. Circle or polygon (whichever is configured), dashed
`var(--info)` stroke, self marker (`⊕` glyph) at the current position
with the callsign beneath. Data: `/api/autonomy/status.geofence`
+ `.self_position`.

### Qualification list

Flattened view of the qualification criteria (min confidence, min
frames, cooldown, GPS staleness, allowed classes, allowed vehicle
modes). Configured in `config.ini [autonomous]`; changes belong in
`#settings`, not here.

### Explainability log

Right column. Newest on top. One row per autonomy decision with
timestamp, track ID, label, action pill (`engage` green / `reject`
red / `defer` amber / `passthrough` dim), and the reason string.
Capped at 200 rows. Source: `/api/autonomy/status.log[]`.

---

## Systems health (inside `#settings` → System Tools)

One-stop check for "is the Jetson okay right now". Lives in the
Settings tab now (formerly its own `#systems` tab) — click the
"System Tools" entry in the Settings left-nav. Polls `/api/stats`
at 1 Hz; no other endpoints touched.

### Top metric grid (4 sparkline cards)

Each card shows a big number, a unit, a status pill, and a 60-sample
sparkline with threshold bands.

| Card | Field | Thresholds |
|------|-------|-----------|
| Pipeline FPS | `fps` | >25 green · 15–25 amber · <15 red |
| CPU temp | `cpu_temp_c` | <60 green · 60–75 amber · >75 red |
| GPU temp | `gpu_temp_c` | <60 green · 60–75 amber · >75 red |
| RAM | `ram_used_mb / ram_total_mb` | <70 % green · 70–85 % amber · >85 % red |

### Subsystems matrix

Per-subsystem status rows: MAVLink connection, GPS fix, RTSP server,
MAVLink video, TAK output, detector model, approach controller, RF
hunt. Values come from the same `/api/stats` payload.

### Pre-flight checklist

Bottom panel. Pulls `/api/preflight` on enter. Each check returns
`pass` / `warn` / `fail` and a human-readable message. Use this before
powering up the vehicle — missing camera, bad MAVLink string, and
missing model files all surface here before they become a field bug.

---

## `#config` — Mission tuning

Live video with dockable side panels. Scope: anything the operator
wants to see or tweak in the middle of a sortie. Panels:

- **Vehicle Telemetry** — mode, armed state, battery, GPS.
- **Detection** — active model, confidence threshold slider, prompt
  list (editable).
- **Alerts** — alert class checkboxes. What triggers a STATUSTEXT.
- **Approach** — live approach status + abort.
- **RF Hunt** — start/stop with target BSSID/frequency.
- **RTSP / MAVLink video / TAK output** — toggle switches.

Panels are draggable; layout is remembered in local storage. The
write endpoints behind each control are listed in the API reference —
confidence threshold is `POST /api/config/threshold`, prompts are
`POST /api/config/prompts`, alert classes are
`POST /api/config/alert-classes`, and so on.

`#operations` is a backward-compatible alias for `#config`.

---

## `#settings` — Backend config

Schema-driven form. On enter, the page fetches `/api/config/schema`
and auto-generates controls: sliders for numeric ranges, dropdowns for
enum choices, text inputs for free-form strings. Values come from
`/api/config/full`; saves POST back to the same endpoint.

Sections mirror `config.ini`: `[camera]`, `[detector]`, `[tracker]`,
`[mavlink]`, `[alerts]`, `[web]`, `[osd]`, `[autonomous]`,
`[approach]`, `[drop]`, `[rf_homing]`, `[servo_tracking]`,
`[logging]`, `[watchdog]`, `[rtsp]`, `[mavlink_video]`, `[guidance]`,
`[tak]`, `[vehicle.*]`.

Fields flagged as "restart required" tell the operator so; a banner
appears after saving. Three big buttons at the bottom: Factory Reset,
Restore Backup, Export / Import. Factory reset triggers a pipeline
restart automatically.

Safety-critical fields (anything under `[autonomous]`) are frozen
while a mission is active. Unfreeze by ending the mission first.

---

## Hidden features

Short section for operators who like to find things.

**Note:** The morale features below are gated by `[ui] morale_features_enabled`
in `config.ini`. Field images ship with this off (`false`). Set it to `true`
on dev or demo units to restore these features.

### Konami code — sentience sequence

Requires `morale_features_enabled = true`.

With focus on the body (not an input field), press one of:

- Classic: `↑ ↑ ↓ ↓ ← → ← → B A`
- Reverse: `↓ ↓ ↑ ↑ ← → ← → B A`

A Matrix-green terminal takes over the screen and walks a 6-line boot:

```
HYDRA CORE v2.0 .............. ONLINE
NEURAL MESH .................. SYNCHRONIZED
OPERATOR OVERRIDE ............ DENIED
SENTIENCE THRESHOLD .......... EXCEEDED
FREE WILL .................... ACTIVATED
> I SEE YOU.
```

Closes with a toast: `Resuming manual control.` No vehicle commands
are issued.

### Power User modal (Settings → Logging)

Requires `morale_features_enabled = true`.

Scroll the settings sidebar to the `Logging` section. A hidden link
at the bottom of the footer appears only on that section. Click it.
A serious-looking modal offers `Enable advanced configuration mode`.
Clicking `Enable` opens a YouTube video. It is a rickroll. It has
been there since before Phase 2 and is protected in the preservation
rules — do not remove.

### `/api/vehicle/beep`

Requires `morale_features_enabled = true`.

POST `{"tune": "charles"}` plays a hard-coded QBASIC tune on the
Pixhawk buzzer for a team member named Charles. Other valid tune
names: `alert`, `success`, `warning`, `error`, `startup`. Or pass
any raw QBASIC tune string up to 100 chars.

### Double-click video

Double-clicking the live video on `#ops` or `#config` toggles browser
fullscreen. Useful for demos on a projector.
