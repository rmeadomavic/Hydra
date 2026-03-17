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

### View System

Three views, client-side routed via URL hash (`/#monitor`, `/#control`,
`/#settings`). The MJPEG stream stays connected across view switches — no
reconnection on navigation.

### Persistent Top Bar (~48px)

Always visible across all views:

- **Left:** SORCC diamond badge + "HYDRA DETECT" title
- **Center:** View tabs (pill-style buttons, active tab highlighted with green
  accent)
- **Right:** Connection status pill (LIVE/OFFLINE) + FPS counter

When on Control or Settings view, a **mini video thumbnail** (~120x80px) appears
in the top bar. Click to jump to Monitor.

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
auto-dismiss after 10s). Only interruption allowed on Monitor view.

### Presentation Mode

Keyboard shortcut (`P` or `F11`) hides top bar and footer entirely — truly
full-screen video with only floating overlays. One keypress to restore.

## Control View

Operator cockpit with the video feed and a grid of dockable panels.

### Layout

- **Left ~60%:** Video feed
- **Right ~40%:** Two-column panel grid

### Panel System

Each panel has:
- Header bar with title, collapse/expand toggle, drag handle
- **Collapse:** One click minimizes to header only, one click restores
- **Reorder:** Drag within the grid to rearrange
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

### Sections

1. **Camera** — source, resolution, FPS, FOV, GStreamer pipeline
2. **Detector** — model file, confidence threshold, NMS threshold, class list
3. **MAVLink** — connection string, baud rate, system/component IDs, alert classes
4. **Web** — host, port, API token (masked with show/hide), MJPEG quality, CORS
5. **Tracker** — ByteTrack parameters (track buffer, match threshold, etc.)
6. **RF Homing** — default search pattern, area, spacing, altitude, RSSI thresholds
7. **Display** — overlay options, color scheme tweaks, panel layout preferences
8. **System** — power mode, log directory, debug flags

### Behavior

- Reads current config from `GET /api/config/full`
- **Apply** writes via `POST /api/config/full` — server updates `config.ini` and
  applies runtime changes where possible
- **Reset to Saved** reverts form to current `config.ini` values
- Fields requiring restart show a warning icon
- Frontend validation: numeric ranges, required fields, valid paths
- `config.ini` remains source of truth on disk

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
- **Jinja2 template inheritance** — `base.html` with `{% block content %}` so
  views share top bar and footer
- **CSS variables file** — single source of truth for design tokens
- **`review.html` stays standalone** — separate tool, own Leaflet dependency,
  does not share the view system yet

### New API Endpoints

- `GET /api/config/full` — all config.ini sections as JSON
- `POST /api/config/full` — write changes, return which fields need restart
- Static file serving for `/static/` directory

## Migration Path

The existing `index.html` is replaced, not modified. The `review.html` page is
unchanged. All current API endpoints remain — this redesign is purely frontend
with two new config endpoints added to `server.py`.
