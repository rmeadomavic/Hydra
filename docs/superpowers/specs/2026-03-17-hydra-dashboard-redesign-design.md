# Design: Hydra Dashboard Redesign — Multi-View with Dockable Panels

**Date:** 2026-03-17
**Status:** Draft

## Purpose

Redesign the Hydra Detect web dashboard from a single-page scrolling sidebar
into a multi-view application with dockable panels and a dedicated settings
page. The current `index.html` (1515 lines of inline HTML/CSS/JS) will be
decomposed into a maintainable file structure with no build tools.

### Audiences

- **VIPs/Generals:** Clean, impressive monitor view that communicates capability
  without overwhelming with controls
- **Instructors/Demos:** Self-explanatory interface that clearly shows what the
  system does
- **Operators/Students:** Fast access to what matters, configurable layout that
  doesn't waste their time

### Target Form Factors

- Laptop (primary, ≥1280px)
- Projection/large display (Monitor view optimized for audience viewing)
- Steam Deck (1280x800, 7" touch screen)

## Architecture

### Single-Page Application

This is a **single-page app** (SPA). The server renders one page using Jinja2
`{% include %}` directives to compose `base.html` with all three view sections
(`monitor.html`, `control.html`, `settings.html`) embedded in the same document.
JavaScript in `app.js` handles hash-based routing (`/#monitor`, `/#control`,
`/#settings`) by showing/hiding view sections via CSS (`display: none` on
inactive views).

This is critical because the MJPEG `<img>` element must remain in the DOM
continuously — destroying and recreating it would drop the stream connection.
The `<img>` lives in `base.html` outside any view section, using
`position: fixed`. Each view applies a CSS class to `<body>` (e.g.,
`body.view-monitor`, `body.view-control`) that controls the element's
dimensions and position:
- **Monitor (`body.view-monitor`):** Full-size, fills the view area
  (`top/left/right/bottom` pinned to view bounds)
- **Control (`body.view-control`):** Sized and positioned to the ~60% left
  column area
- **Settings (`body.view-settings`):** Hidden (`opacity: 0; pointer-events: none`)

The mini thumbnail in the top bar uses a separate clipping container with
`overflow: hidden` sized to ~120x80px. Inside it, a CSS `transform: scale()`
with `transform-origin: top left` scales down the same `<img>` element. Since
the `<img>` is `position: fixed`, the thumbnail container uses a `clip-path` or
`overflow: hidden` wrapper to crop it. This avoids a second MJPEG connection.

### Polling Coordination

`app.js` manages a centralized polling coordinator. Polling for view-specific
data **pauses when that view is not active** to avoid wasting Jetson CPU cycles.

Base polling intervals (normal operation):
- **`/api/stats` (2s)** — always active. Returns FPS, inference time, GPU/CPU/
  RAM, MAVLink status, GPS fix, vehicle state. Serves the top bar and both
  Monitor and Control views.
- **`/api/tracks` (1s)** — Monitor + Control views. Track list for target
  selection and summary overlay.
- **`/api/target` (1s)** — Monitor + Control views. Target lock state.
- **`/api/detections` (3s)** — Control view only. Detection log feed.
- **`/api/rf/status` (2s)** — Monitor + Control views (only when RF hunt is
  active; skip poll entirely when RF is disabled).
- **Settings view** — no polling. Config loaded once via `GET /api/config/full`
  on view enter.

### Persistent Top Bar (~48px)

Always visible across all views:

- **Left:** SORCC diamond badge + "HYDRA DETECT" title
- **Center:** View tabs (pill-style buttons, active tab highlighted with green
  accent)
- **Right:** Connection status pill (LIVE/OFFLINE) + FPS counter

When on Control or Settings view, a **mini video thumbnail** (~120x80px) appears
in the top bar — this is a CSS-scaled reference to the same MJPEG `<img>`
element, not a second stream. Click to jump to Monitor.

### Footer

"UNCLASSIFIED" bar with top-edge gradient glow matching the header's bottom-edge
treatment, creating visual bookends.

## Monitor View

Full-screen video feed with floating overlays. Optimized for demos and passive
monitoring.

### Layout

Video feed fills the entire view area edge-to-edge (minus top bar and footer).

### Floating Overlays (semi-transparent, backdrop blur)

**Bottom-left — System & Vehicle Vitals:**
- Pipeline: FPS, inference time (ms), GPU temp + load %
- Divider
- Vehicle: mode, armed state, battery (V + %), altitude, heading, GPS fix, speed

**Bottom-right — Detection & RF Summary:**
- RF hunt status (only when active): state + RSSI
- Active track count + top class labels

**Top-center — Target Lock Indicator (only when active):**
- Tracking mode: `◇ TRACKING #4 — person (87%)`
- Strike mode: `⚠ STRIKE #4 — person (87%)` (red accent)
- Hidden when no lock is active

### Auto-Hide Behavior

All overlays fade out after 5 seconds of inactivity. Reappear on mouse move or
touch. Provides a clean feed for passive viewing, vitals on interaction.

### Quick Action Toolbar (bottom-center, auto-hide)

Slim floating bar that appears on interaction:

- **Left group:** Target actions — Lock, Strike, Release
- **Right group:** Vehicle commands — Loiter, RTL, Auto
- Thin divider between groups
- Strike retains the confirmation modal — no accidental engagement

### Toast Notifications

MAVLink disconnect or critical alerts slide in from top-right (red accent,
auto-dismiss after 10s). Maximum 3 visible toasts — newest replaces oldest.
Duplicate messages within a 5-second window are suppressed. Only interruption
allowed on Monitor view.

### Presentation Mode

Keyboard shortcut (`Ctrl+Shift+P`) hides top bar and footer entirely — truly
full-screen video with only floating overlays. Same shortcut to restore. Only
bound when no text input is focused.

## Control View

Operator cockpit with the video feed and a grid of dockable panels.

### Layout

- **Left ~60%:** Video feed
- **Right ~40%:** Two-column panel grid

### Panel System

Each panel has:
- Header bar with title, collapse/expand toggle, drag handle
- **Collapse:** One click minimizes to header only, one click restores
- **Reorder:** Drag within the grid to rearrange (using SortableJS, ~10KB
  gzipped, no dependencies, touch-ready for Steam Deck)
- **Show/hide:** Panel menu button to toggle panel visibility entirely
- **Persistence:** Layout saved to `localStorage`, survives sessions

### Default Panels (6)

1. **Vehicle Telemetry** — mode, armed, battery, speed, altitude, heading, GPS,
   mode buttons (Loiter/Auto/RTL)
2. **Target Control** — lock indicator, active tracks list, Lock/Strike/Release
   buttons
3. **Pipeline Stats** — FPS, inference time, GPU/CPU/RAM with color bars, power
   mode selector
4. **Detection Config** — model selector, confidence slider, alert class filter
   (categorized checklist)
5. **RF Hunt** — status display + config form (mode, target, search pattern,
   area, spacing, altitude, RSSI thresholds, start/abort)
6. **Detection Log** — scrolling real-time log of recent detections

### Consolidation from Current UI

Current 9 sidebar sections → 6 panels:
- Camera Source → moved to Settings view
- Pipeline Controls (pause/stop) → merged into Pipeline Stats panel
- Pipeline stats section → merged into Pipeline Stats panel

## Settings View

All `config.ini` settings editable in the browser.

### Layout

- **Left nav (~160px):** Vertical list of config sections, highlighted active
- **Right content:** Form fields for selected section

### Sections (matching `config.ini`)

1. **Camera** — source, resolution (width/height), FPS, horizontal FOV
2. **Detector** — YOLO model file, confidence threshold, class filter
3. **Tracker** — ByteTrack parameters (track threshold, track buffer, match
   threshold)
4. **MAVLink** — enabled, connection string, baud, source system ID, GPS
   settings, alert settings (statustext, interval, severity, alert classes),
   vehicle commands (auto-loiter, guided ROI, strike distance, geo tracking)
5. **Web** — host, port, MJPEG quality, API token (masked with show/hide toggle;
   see Security section for redaction rules)
6. **OSD** — FPV overlay enabled, mode (statustext/named_value), update interval
7. **Autonomous** — enabled (with prominent warning), geofence (circle: lat/lon/
   radius, polygon: coordinate pairs), min confidence, min track frames, allowed
   classes, strike cooldown, allowed vehicle modes. This section gets a red
   warning banner: "Autonomous strike settings — changes affect safety-critical
   behavior"
8. **RF Homing** — enabled, mode (wifi/sdr), target BSSID/frequency, Kismet
   connection, search pattern/area/spacing/altitude, RSSI thresholds/window,
   gradient step/rotation, polling interval, arrival tolerance
9. **Logging** — log directory, format (csv/jsonl), image saving (enabled, dir,
   quality), crop saving (enabled, dir)

### Behavior

- Reads current config from `GET /api/config/full`
- **Apply** writes via `POST /api/config/full` — server updates `config.ini` and
  applies runtime changes where possible
- **Reset to Saved** reverts form to current `config.ini` values
- Fields requiring restart show a warning icon
- Frontend validation: numeric ranges, required fields, valid paths
- `config.ini` remains source of truth on disk

### Security

- Both `GET /api/config/full` and `POST /api/config/full` require bearer token
  auth (same `_check_auth()` as existing control endpoints)
- `GET` response **redacts** the API token value, returning `"***"` instead of
  the real token
- `POST` treats an unchanged `"***"` value as "keep existing token" — only
  updates if a new value is provided
- `POST` request body size is bounded (server rejects payloads > 64KB).
  Enforced by reading `Content-Length` header and returning 413 before parsing
  if it exceeds the limit. This avoids loading large payloads into Jetson RAM.

### Config Write Safety

Writing `config.ini` on Jetson must be crash-safe:
- **Atomic write:** Write to a temp file (`config.ini.tmp`) in the same
  directory, then `os.replace()` to the final path. This is atomic on Linux
  filesystems and prevents corruption if power is lost mid-write.
- **File locking:** Use `fcntl.flock()` to prevent concurrent writes from
  multiple browser sessions
- **Backup:** Before overwriting, copy current `config.ini` to
  `config.ini.bak`. The Settings view shows a "Restore Backup" button if
  `.bak` exists.

## Error States

### Monitor View
- **Before first MJPEG frame:** Dark background with a subtle loading spinner
  and "Connecting to video stream..." text
- **Stream disconnected:** Last frame stays visible with a red "STREAM LOST"
  overlay badge. Auto-reconnects every 2 seconds.

### Control View
- **Pipeline stopped:** Panels show last-known values grayed out with a
  "Pipeline offline" banner across the panel area
- **Individual panel data unavailable:** Panel shows "No data" placeholder
  instead of stale values

### Settings View
- **Config load failure:** Error banner with retry button: "Could not load
  configuration — check connection"
- **Config save failure:** Toast notification with the error message, form
  retains unsaved changes

### General
- **Corrupted localStorage layout:** `panels.js` validates stored layout on
  load. If invalid (missing panels, unknown IDs), falls back to default layout
  silently.
- **WebSocket/polling failure:** Top bar connection pill switches to OFFLINE
  (red). Polling continues attempts with exponential backoff (1s → 2s → 4s,
  max 10s).

## Visual Design System

Refining the existing SORCC aesthetic — same identity, elevated finish.

### Color System

Core palette unchanged:
- `--ogt-green: #385723` (primary institutional green)
- `--ogt-green-dark: #2a4118`
- `--ogt-muted: #A6BC92`
- `--ogt-warm: #D8E2D0`
- `--ogt-light: #EFF5EB`
- Dark base: `--panel-bg: #0c0c0c`, `--sidebar-bg: #141414`, `--card-bg: #1c1c1c`

Enhancements:
- **Panel gradient backgrounds** instead of flat color:
  `linear-gradient(145deg, #1c1c1c, #1a1f18)` (subtle warm-green tint)
- **Accent glow effects** on active/selected states:
  `box-shadow: 0 0 12px rgba(56,87,35,0.3)`
- **Smoother gradient color bars** for GPU/CPU/RAM instead of hard color steps

### Borders & Corners

- Panels: `border-radius: 8px`
- Modals: `border-radius: 12px`
- Buttons and inputs: `border-radius: 6px`
- Panel borders: subtle gradient (lighter at top, fading down)
- Buttons: `background: linear-gradient(...)` lift instead of flat color

### Typography

Same families (Barlow Condensed, Barlow, JetBrains Mono). Tighter system:

- Size scale: `0.65rem / 0.75rem / 0.85rem / 1rem / 1.2rem / 1.5rem`
- Letter-spacing: condensed uppercase headers `0.08em`, body `0`, mono `0.02em`

### Motion

- Panel collapse/expand: `max-height` transition, `ease-out`, ~200ms
- View transitions: fade crossfade, ~150ms
- Button press: `transform: scale(0.97)`, ~100ms; brightness lift on hover
- Toast notifications: slide from top-right with slight bounce easing
- LIVE status pill: subtle pulse animation (soft green glow that breathes)
- No scroll-jacking, no parallax, no heavy animations — must stay snappy on Jetson

### Atmosphere

- Hex pattern overlay on panels: `opacity: 0.04` + subtle noise texture
- Header gradient refined with third color stop
- Footer top-edge gradient glow (bookend to header)

## Responsive Behavior

### Breakpoints

- **≥1280px** — full layout as designed
- **800–1279px** — Steam Deck / tablet adaptations
- **≤799px** — graceful fallback

### Steam Deck / Small Screens (800–1279px)

- **Monitor:** No changes — video + overlays work as-is
- **Control:** Video shrinks to top ~40%. Panels switch to single-column
  scrollable list below video
- **Settings:** Left nav collapses to dropdown selector at top, full-width form
- **Top bar:** View tab labels collapse to icons only. Mini thumbnail hides.
- **Touch targets:** All buttons minimum 44x44px. Larger slider thumbs. Wider
  panel drag handles.

### Projection

- Monitor view is the projection target — auto-hide overlays provide clean feed
- Presentation mode (hide top bar + footer) for true full-screen

### Implementation

- CSS media queries for breakpoints (no JS layout switching)
- `localStorage` saves layout preferences **per breakpoint**
- `pointer: coarse` media query for touch target sizing

## File Organization

### New Structure

```
hydra_detect/web/
├── server.py                    (existing, add new routes)
├── templates/
│   ├── base.html                (shared shell: top bar, footer, view container)
│   ├── monitor.html             (monitor view content)
│   ├── control.html             (control view panels)
│   ├── settings.html            (settings form)
│   └── review.html              (existing, unchanged)
└── static/
    ├── css/
    │   ├── variables.css        (SORCC design tokens)
    │   ├── base.css             (reset, typography, shared components)
    │   ├── topbar.css           (persistent top bar)
    │   ├── monitor.css          (monitor view)
    │   ├── control.css          (control view + panel system)
    │   └── settings.css         (settings view)
    └── js/
        ├── app.js               (view router, shared state, API polling)
        ├── panels.js            (drag, collapse, reorder, localStorage)
        ├── monitor.js           (overlay auto-hide, quick actions, presentation mode)
        ├── control.js           (panel logic, track list updates)
        └── settings.js          (form loading, validation, apply/reset)
```

### Key Decisions

- **No build tools** — plain CSS and vanilla JS. No webpack, no npm.
  One exception: SortableJS loaded from a vendored copy in `static/js/vendor/`
  (committed to the repo, not downloaded at build time).
- **Jinja2 `{% include %}`** — `base.html` includes all three view templates
  into a single page. This is NOT template inheritance with separate routes —
  it is a single server-rendered page with client-side view switching.
- **CSS variables file** — single source of truth for design tokens
- **`review.html` stays standalone** — separate tool with its own Leaflet
  dependency. Will be evaluated for integration as a fourth view in a future
  phase.

### New API Endpoints

- `GET /api/config/full` — all config.ini sections as JSON (auth required,
  token redacted)
- `POST /api/config/full` — write changes, return which fields need restart
  (auth required, 64KB body limit)
- `app.mount("/static", StaticFiles(directory="..."))` — static file serving,
  directory path resolved relative to the web module (not CWD) for Docker
  compatibility

### Accessibility

Basic keyboard and screen-reader support (low effort, high value):
- Tab order through panels and interactive elements
- Escape to close modals
- ARIA labels on status indicators (LIVE/OFFLINE, armed state, GPS fix)
- Focus-visible outlines on all interactive elements

### Performance Notes

- `backdrop-filter: blur()` on floating overlays may be expensive on Jetson GPU
  (shared with CUDA inference). Implementation should test this and fall back to
  solid semi-transparent backgrounds (`rgba(0,0,0,0.85)`) if frame rate drops.
- Hex pattern overlay + noise texture on panels is cosmetic compositing. Profile
  with `tegrastats` after implementation — remove noise layer first if GPU
  memory pressure is observed.

## Migration Path

The existing `index.html` is replaced, not modified. The `review.html` page is
unchanged. All current API endpoints remain — this redesign is purely frontend
with two new config endpoints and a static file mount added to `server.py`.
