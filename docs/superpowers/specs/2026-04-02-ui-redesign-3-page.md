# Design Spec: 3-Page UI Redesign — Ops HUD / Config / Settings

**Date:** 2026-04-02
**Status:** Approved (brainstorming session 2026-04-01)

---

## Overview

Restructure the Hydra dashboard from a 2-view SPA (Operations + Settings) into
a 3-page layout with clear audience separation:

1. **Ops (HUD)** — Pilot heads-up display. Minimal, video-centric, clickable bounding boxes.
2. **Config** — Mission configuration. Video + all operational controls and tuning.
3. **Settings** — Backend/system settings. Sliders/dropdowns, log viewer, admin tools.

Also surfaces 15 backend features that currently have no UI, and fixes emoji
icons in settings nav.

---

## Page 1: Ops (HUD)

### Purpose
What the pilot/operator sees during a mission. Only essential in-flight data.
Think fighter jet HUD, not web dashboard.

### Layout
- **Full-width video** with transparent canvas overlay for clickable bounding boxes
- **Telemetry strip** (top or bottom overlay on video): mode, battery, speed, alt, heading, GPS
- **Minimap widget** (bottom-right corner): vehicle position + detection markers on map
- **Quick-action bar** (bottom-left or floating): Abort, Loiter, RTL, Beep
- **Lock info overlay** (appears when target engaged): track ID, label, approach mode, elapsed

### Clickable Bounding Boxes
- Invisible `<canvas>` element overlaid on the video `<img>`
- On click, hit-test against known track bounding boxes from `/api/tracks`
- Scale track bbox coordinates to displayed image dimensions
- On hit: show context menu at click position with actions:
  - Follow | Strike | Drop | Pixel Lock | Loiter | Cancel
- On miss: dismiss any open context menu
- Visual feedback: highlight the clicked track's bbox with accent border

### What's NOT on Ops
- No panel system, no scrolling, no config controls
- No detection log, no pipeline stats, no RF hunt config
- No alert class selection, no model switching

### Data Sources
- `/api/stats` (telemetry, mode, battery, GPS)
- `/api/tracks` (bounding boxes, track list for overlay)
- `/api/target` (lock status)
- `/api/approach/status` (approach mode info)
- `/stream.jpg` (video frames)

---

## Page 2: Config

### Purpose
Mission configuration and operational tuning. Has video
feed for visual feedback while tuning detection parameters.

### Layout
- **Video feed** (left, ~50% width): same snapshot polling, shows detection results
- **Config panels** (right, ~50% width): organized in collapsible sections

### Sections

#### Mission
- Mission profile selector with **rich metadata** (icons, behavior, approach_method from `/api/mission-profiles`)
- Mission name input + start/end buttons
- Active mission indicator

#### Detection
- Model selector dropdown (from `/api/models`)
- Confidence threshold **slider** (0.05-0.95)
- Alert classes (categorized checkboxes with All/Clear/Apply)
- **Camera source live switching** (dropdown from `/api/camera/sources`, instant switch)

#### Vehicle
- Mode buttons (Loiter, Hold, Auto, Guided, RTL)
- Telemetry display (battery, speed, alt, heading, GPS)
- Power mode selector

#### Engagement
- Track list with Lock/Follow/Strike/Drop/**Pixel Lock** buttons
- Approach status panel (mode, elapsed, waypoints, arm status)
- Abort button

#### RF Hunt
- Mode selector (WiFi/SDR)
- Target input (BSSID or frequency)
- Search pattern, area, spacing, altitude
- RSSI thresholds
- Start/Stop buttons
- RSSI sparkline + signal map (when active)

#### Outputs
- RTSP toggle + status
- MAVLink video toggle + resolution/quality sliders
- TAK toggle + **unicast target management** (add/remove IPs)
- **Waypoint export** button (live session)

#### Detection Log
- Scrollable detection feed with timestamps

---

## Page 3: Settings

### Purpose
System administration and backend configuration. The dev/admin page.
All values editable via **sliders and dropdowns** where possible, not typed text.

### Layout
- **Left nav** (vertical tabs): Clean text labels, NO emoji icons
- **Right content**: Config editor with smart input controls

### Navigation Labels (replacing emoji)
- Camera | Detector | Tracker | MAVLink | Web | OSD | Autonomous | RF Homing | TAK | Logging

### Input Control Types
| Data Type | Control | Example |
|-----------|---------|---------|
| Float with range | Slider + value display | Confidence: 0.45 |
| Int with range | Slider + value display | Max log files: 20 |
| Boolean | Toggle switch | Enabled: ON |
| Enum/choices | Dropdown | OSD mode: named_value |
| Bounded string | Dropdown (if options known) | Video standard: NTSC |
| Free text | Text input | Callsign: HYDRA-1 |
| IP/URL | Text input with validation | MAVLink port: /dev/ttyTHS1 |
| Comma-separated | Tag input or textarea | Alert classes: person, car |
| Password | Password input with reveal | API token: ******** |

### Sections

#### System Tools
- **Live log viewer** panel (from `/api/logs`, with level filter dropdown)
- Pipeline stats (FPS, inference, GPU/CPU/RAM)
- Pipeline controls (Pause / Stop / Restart with confirmation)
- **Session ZIP export** button (from `/api/export`)
- Config backup / restore / factory reset / import / export
- **Logout** button

#### Page Links
- Links to standalone pages: `/control` (mobile), `/fleet` (fleet view), `/review` (post-sortie), `/setup` (wizard)

### Config Sections
All 10 config.ini sections, each with appropriate input controls derived from
`config_schema.py` field specs (min/max/choices → slider/dropdown automatically).

---

## Navigation

### Topbar
- Brand/callsign (left)
- 3 nav tabs: **Ops** | **Config** | **Settings** (center)
- Status indicators (right): CAM, MAV, GPS dots + FPS + LIVE pill

### Routing
- Hash-based: `#ops`, `#config`, `#settings`
- Default view: `#ops`
- Video polling active on Ops and Config, paused on Settings

### Page Links in Footer or Settings
- `/control` — Mobile tactical (simplified touch-friendly ops)
- `/fleet` — Fleet View (multi-vehicle polling)
- `/review` — Post-sortie map review
- `/setup` — First-boot wizard

---

## Unimplemented Features to Surface

| Feature | Page | Implementation |
|---------|------|---------------|
| Pixel Lock approach | Ops (bbox menu) + Config (track list) | Add button, call `/api/approach/pixel_lock/{id}` |
| Waypoint export (live) | Config (Outputs section) | Button → `GET /api/export/waypoints` download |
| Waypoint export (review) | Review page | Button → `GET /api/review/waypoints/{file}` |
| TAK unicast targets | Config (Outputs section) | List + add/delete form for `/api/tak/targets` |
| Buzzer/beep | Ops (quick actions) | Button → `POST /api/vehicle/beep` |
| Session ZIP export | Settings (System Tools) | Button → `GET /api/export` download |
| Live log viewer | Settings (System Tools) | Panel polling `/api/logs?lines=50&level=INFO` |
| Logout | Settings (System Tools) | Button → `POST /auth/logout` + redirect |
| Mission profile metadata | Config (Mission section) | Use `/api/mission-profiles` for icons + behavior |
| Camera live switch | Config (Detection section) | Dropdown + instant apply via `/api/camera/switch` |
| Config prompts | Config (Detection section) | Text field → `POST /api/config/prompts` |
| Control page link | Settings (Page Links) | `<a href="/control">` |
| Fleet View link | Settings (Page Links) | `<a href="/fleet">` |
| Review page link | Settings (Page Links) | `<a href="/review">` |
| Setup page link | Settings (Page Links) | `<a href="/setup">` |

---

## Technical Approach

### Canvas Overlay (Clickable Bounding Boxes)
```
<div class="video-container">
  <img id="video-frame" src="/stream.jpg">
  <canvas id="bbox-overlay"></canvas>  <!-- same dimensions, position:absolute -->
</div>
```
- Canvas redraws on each track update (1Hz from `/api/tracks`)
- `canvas.addEventListener('click', hitTest)` maps pixel coords to track bboxes
- Scale factor: `displayWidth / frameWidth` (frame dimensions from `/api/stats`)
- Context menu: absolutely-positioned div at click coordinates

### Minimap Widget
- Leaflet.js or simple canvas with vehicle marker + detection markers
- Vehicle position from `/api/stats` (lat/lon)
- Detection positions from `/api/tracks` (lat/lon per track)
- Refreshes every 2 seconds
- Small (200x150px), bottom-right corner of video

### Settings Input Generation
- Read field specs from `/api/config/full` response (includes schema metadata)
- Auto-select control type based on schema:
  - `min_val` + `max_val` present → slider
  - `choices` present → dropdown
  - `type: bool` → toggle
  - Otherwise → text input
- Schema-driven, no hardcoded field lists

---

## Files to Create/Modify

### New Files
- `hydra_detect/web/templates/ops.html` — HUD page content
- `hydra_detect/web/templates/config.html` — Config page content (replaces operations.html)
- `hydra_detect/web/static/js/ops.js` — HUD logic + canvas overlay + context menu
- `hydra_detect/web/static/js/config.js` — Config page logic (evolved from operations.js)
- `hydra_detect/web/static/css/ops.css` — HUD-specific styles
- `hydra_detect/web/static/css/config.css` — Config page styles

### Modified Files
- `hydra_detect/web/templates/base.html` — 3 tabs, include ops.html + config.html + settings.html
- `hydra_detect/web/templates/settings.html` — Remove emoji icons, add system tools section
- `hydra_detect/web/static/js/app.js` — 3-view router, updated polling logic
- `hydra_detect/web/static/js/settings.js` — Slider/dropdown generation from schema
- `hydra_detect/web/static/css/settings.css` — Slider styles, remove emoji
- `hydra_detect/web/static/css/variables.css` — Any new tokens needed
- `hydra_detect/web/server.py` — Serve new templates, add any missing endpoint wiring

### Deprecated (kept for reference, not loaded)
- `hydra_detect/web/templates/operations.html` — replaced by ops.html + config.html
- `hydra_detect/web/static/js/operations.js` — replaced by ops.js + config.js
- `hydra_detect/web/static/css/operations.css` — replaced by ops.css + config.css

---

## Constraints

- No external CDN dependencies (Leaflet can be bundled or use simple canvas map)
- Must work on tablets in field conditions (touch-friendly, readable at arm's length)
- Video polling paused on Settings page (saves Jetson CPU)
- CSP: no inline scripts, external JS files only
- No `.innerHTML` — use `.textContent` and DOM creation
- Mobile-first: large touch targets for gloved hands
- Palantir/Anduril aesthetic: data-dense, monospace numbers, subtle depth

---

## Implementation Order

1. **Quick wins** — Wire missing endpoints (pixel lock button, waypoint export buttons, page links, logout, beep) into existing UI. No restructure needed.
2. **3-view router** — Update base.html and app.js for #ops/#config/#settings routing
3. **Ops page** — New HUD with canvas overlay, telemetry strip, minimap
4. **Config page** — Evolved from operations.html, add missing features
5. **Settings page** — Slider/dropdown generation, log viewer, system tools
6. **Polish + test** — Cross-page consistency, responsive testing, full test suite
