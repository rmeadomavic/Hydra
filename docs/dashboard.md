# Web Dashboard

The Hydra dashboard is a single-page application served on port 8080. It is the primary operator interface for monitoring detections, controlling the vehicle, and managing configuration.

The SPA has two main views (Operations and Settings) that share a common shell (`base.html`). Both views always exist in the DOM and toggle visibility via CSS. A standalone Review page lives at `/review`.

## Pages

| Path | Page | Description |
|------|------|-------------|
| `/` | Dashboard SPA | Operations + Settings tabs |
| `/control` | Mobile Control | Touch-friendly controls for phone |
| `/instructor` | Instructor Overview | Multi-vehicle status from one browser |
| `/review` | Post-Mission Review | Map with detection markers and track replay |
| `/setup` | Setup Wizard | First-boot device configuration |

## Operations Tab

<!-- TODO: Screenshot — dashboard-ops.png -->

The Operations tab shows the live video stream with bounding boxes, track IDs, and HUD overlays.

### Video Stream

The dashboard uses **snapshot polling** — the browser requests `GET /stream.jpg`
which returns a single JPEG frame. JavaScript polls by setting
`img.src = '/stream.jpg?t=<timestamp>'` on each load event (~30 fps cap).

The server caches the encoded JPEG for 33ms to avoid re-encoding on rapid polls
(handles 500+ requests/second with zero CPU waste). When the browser tab is hidden,
polling pauses automatically to save Jetson CPU.

A legacy MJPEG endpoint (`/stream.mjpeg`) is preserved but unused by default.

**Double-click the video** to toggle fullscreen mode.

Bounding boxes are drawn by the overlay module before frames reach the browser:

- **Green corner brackets**: tracked object
- **Red corner brackets**: active strike target
- **Solid red rectangle**: strike approach in progress

### Authentication

The dashboard automatically bypasses API token authentication for same-origin
requests. External API access (curl, scripts) still requires a Bearer token
when `api_token` is configured in `config.ini`.

### HUD Elements

The topbar displays real-time pipeline metrics:

- **FPS**: detection pipeline frame rate
- **Inference**: YOLO inference time in milliseconds
- **Tracks**: number of active tracked objects
- **Detections**: total detection count this session
- **MAVLink**: connection status (green dot = connected)
- **GPS**: fix type and coordinates (MGRS when `mgrs` package is available)

### LOW LIGHT Badge

When average frame brightness drops below `low_light_luminance` (default 40), a yellow LOW LIGHT badge appears in the topbar. Detection accuracy degrades in low light. This is informational, not a hard gate.

### Track List and Target Controls

Below the stream, active tracks appear as selectable items showing track ID, label, and confidence.

For each track:

- **Lock**: start Keep-in-Frame mode. Vehicle yaws to center the target.
- **Follow**: start follow approach. Vehicle navigates toward the target with speed scaling.
- **Drop**: start drop approach. Vehicle approaches and releases payload at configured distance.
- **Strike**: start strike approach. Requires confirmation dialog. Two-stage arm circuit.
- **Unlock/Abort**: release lock or abort active approach.

### Mission Profiles

Three built-in mission profile presets:

| Profile | Behavior | Approach | Post-Action |
|---------|----------|----------|-------------|
| RECON | follow | GPS waypoint | SMART_RTL |
| DELIVERY | drop | GPS waypoint | SMART_RTL |
| STRIKE | strike | GPS waypoint | LOITER |

Selecting a profile sets the approach behavior and post-action mode. The post-action adjusts per vehicle type (DOGLEG_RTL for drones, HOLD for UGVs).

### RF Hunt Panel

When RF homing is enabled, a dedicated panel shows:

- Hunt state (IDLE, SCANNING, SEARCHING, HOMING, CONVERGED)
- Current RSSI reading and history graph
- Target info (BSSID or frequency)
- Start/stop controls

## Settings Tab

<!-- TODO: Screenshot — dashboard-settings.png -->

The Settings tab provides:

### Config Editor

Displays all `config.ini` sections as editable fields. Changes are sent to `POST /api/config/full` and persisted to disk. Fields that require a restart are flagged.

Sensitive fields (`api_token`, `kismet_pass`) are redacted in the display.

### Model Selector

Lists available YOLO models from the `models/` directory. Switch models at runtime without restarting the pipeline.

### Camera Selector

Lists detected video sources. Switch cameras at runtime.

### Recovery Tools

- **Restart Pipeline**: triggers a pipeline restart via `POST /api/restart`
- **Restore Backup**: reverts config.ini to the boot-time backup
- **Factory Reset**: restores `config.ini.factory` defaults and restarts
- **Export Config**: downloads current config as JSON
- **Import Config**: uploads and applies a config JSON

### Stream Quality

Slider to adjust MJPEG stream quality (1-100) at runtime. Lower values save bandwidth on slow links.

### Power Mode

On Jetson hardware, displays current nvpmodel power mode and allows switching between modes.

## Pre-flight Checklist

<!-- TODO: Screenshot — dashboard-preflight-pass.png -->

On page load, the dashboard overlays a pre-flight checklist. Each subsystem shows green (pass), yellow (warn), or red (fail):

- Camera source
- MAVLink connection
- GPS fix quality
- Config validation
- Model integrity
- Disk space

The checklist remains visible until the operator dismisses it. Access programmatically via `GET /api/preflight`.

## Mobile Control Page

<!-- TODO: Screenshot — dashboard-mobile.png -->

The `/control` page is a touch-optimized control surface for field use on phones. It shows:

- Compact video stream
- Large touch targets for Lock, Strike, Abort
- Simplified status indicators

No settings editing. Designed for the operator holding the RC transmitter.

## Instructor Overview Page

<!-- TODO: Screenshot — dashboard-instructor.png -->

The `/instructor` page shows multiple Hydra vehicles on one screen. It fetches `GET /api/stats` from each configured Jetson's IP address. Each vehicle card shows:

- Callsign
- FPS and track count
- MAVLink and GPS status
- Abort button (unauthenticated, calls `POST /api/abort` on that vehicle)

> [!WARNING]
> The abort endpoint is intentionally unauthenticated. An instructor must be able to abort any vehicle without configuring tokens. The `/api/abort` and `/api/stats` endpoints have permissive CORS headers for cross-origin instructor page access.

## Connection Indicators

| Indicator | Meaning |
|-----------|---------|
| Green dot next to MAVLink | MAVLink connected, heartbeat received |
| Red dot next to MAVLink | MAVLink disconnected or no heartbeat |
| GPS fix number | Current fix type (0-6) |
| FPS number | Detection pipeline throughput |

## Security Headers

All pages include defense-in-depth headers:

- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- Content Security Policy restricting scripts, styles, and connections

The instructor page has a relaxed CSP (`connect-src *`) to allow cross-origin fetches to other Jetsons.
