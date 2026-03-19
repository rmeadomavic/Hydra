# Web UI Overhaul — Operations View

**Date:** 2026-03-18
**Status:** Approved
**Scope:** Merge Monitor and Control views into a single Operations view with
tiered panel priority and no auto-hide

## Problem

The web UI has three views: Monitor, Control, and Settings. Monitor shows a
fullscreen video feed with floating overlay panels that auto-hide after 5
seconds of no mouse movement. Control shows a 60/40 split with video and
dockable panels. The operator must switch between Monitor and Control to get
both the clean video view and the full panel set.

The auto-hide is disruptive — the operator loses situational awareness every
time they stop moving the mouse. The two-view split forces unnecessary context
switching. The operator is piloting the craft, monitoring the Jetson, and
guiding the AI — they need a single unified view with information prioritized
by operational importance.

## Solution

Merge Monitor and Control into a single **Operations** view. Remove the
auto-hide mechanism entirely. Organize panels into three tiers based on
operational priority. Keep Settings unchanged as the second view.

## Design

### View Structure

**Two views** (down from three):

| View | Purpose | Tab label |
|------|---------|-----------|
| Operations | Video feed + all panels, always visible | Operations |
| Settings | Config editor (unchanged) | Settings |

The existing SPA architecture stays: `<body>` class toggles between
`view-operations` and `view-settings`. URL hash routing (`#operations`,
`#settings`). Default view is Operations.

### CSS Class and Element ID Naming

The current codebase uses the same class names on both `<body>` (state) and
`<div>` containers (content). Both must be renamed consistently:

| Old | New |
|-----|-----|
| `body.view-monitor` | removed |
| `body.view-control` | `body.view-operations` |
| `div.view-monitor#view-monitor` | removed |
| `div.view-control#view-control` | `div.view-operations#view-operations` |
| `#control-panels` | `#operations-panels` |
| `#monitor-lock-indicator` | `#ops-lock-indicator` |
| `#monitor-loading` | `#ops-loading` |
| `#monitor-stream-lost` | `#ops-stream-lost` |
| `#monitor-overlay-left` | removed (data moves to panels) |
| `#monitor-overlay-right` | removed (data moves to panels) |
| `#monitor-toolbar` | removed (buttons move to panels) |

All JS references to these IDs must update accordingly.

### Lifecycle Hooks

The current `app.js` calls `HydraMonitor.onEnter/onLeave()` and
`HydraControl.onEnter/onLeave()` when switching views. The new pattern:

- `HydraOperations.onEnter()` — starts the 500ms update timer, initializes
  panels. Called when switching to Operations from Settings.
- `HydraOperations.onLeave()` — stops the update timer. Called when switching
  to Settings.
- `HydraSettings.onEnter/onLeave()` — unchanged.

Since Operations is the default and primary view, `onEnter()` is also called
on initial page load.

### Operations Layout

**Desktop (>= 1280px):** 60/40 split — video stream on left (60%), scrollable
panel column on right (40%).

**Compact (< 1280px):** Video on top at 40vh, panels stack below in a
scrollable column.

### Video Area

The video area contains only:
- **MJPEG stream** — fills the space
- **Target lock indicator** (`#ops-lock-indicator`) — floating overlay,
  top-center of the video area. Green "TRACKING: #ID label" or red pulsing
  "STRIKE: #ID label". Hidden when no lock is active. The update logic from
  `monitor.js updateLockIndicator()` moves into `operations.js`.
- **Stream status** — loading spinner (`#ops-loading`) and "STREAM LOST —
  RECONNECTING" badge (`#ops-stream-lost`). The `initStreamWatcher()` in
  `app.js` must reference the new element IDs.

All other data (FPS, vehicle stats, tracks, RF) moves into the panels.

### Panel Tiers

Panels are organized by operational priority. Tier determines default
expand/collapse state on first visit (before localStorage has saved
preferences). All panels are collapsible, reorderable, and toggleable via the
existing panel visibility dropdown. State persists to localStorage.

**Tier 1 — Always expanded (mission-critical, glanceable):**

1. **Vehicle Telemetry** — mode badge, armed badge, battery (V + %, color-coded),
   speed, altitude, heading, GPS fix, GPS position. Mode buttons: Loiter, Hold,
   Auto, Guided, RTL (with confirm dialogs).

2. **Target Control** — lock indicator bar, scrollable track list with per-track
   Lock/Strike buttons, Release Lock button.

**Tier 2 — Expanded by default (operational awareness):**

3. **Pipeline Stats** — FPS, inference (ms), engine type. GPU temp + load, CPU
   temp, RAM usage with color bars. Power mode dropdown. Pause / Stop buttons.

4. **RF Hunt** — state badge in header. Config form when idle, status display
   when active (best RSSI, samples, WP progress, signal bar). Start / Abort
   buttons.

**Tier 3 — Collapsed by default (useful but secondary):**

5. **Detection Config** — model dropdown, confidence threshold slider, alert
   class filter with categorized checkbox list.

6. **Detection Log** — scrollable recent detections feed (timestamp, label,
   confidence, GPS).

### localStorage Migration

Existing users have `hydra-panels-desktop` and `hydra-panels-compact` keys in
localStorage from the old Control view. Since the panel IDs and container ID
change (`control-panels` → `operations-panels`), the old keys become stale.

On first load, `panels.js` should check for the old key format. If found,
delete the old keys and let the new tier-based defaults apply. This gives
existing users the new tiered layout automatically.

### What Is Removed

- **Monitor view** — template, CSS, JS all deleted
- **Control view** — template, CSS, JS renamed to operations equivalents
- **Monitor overlay panels** — bottom-left (Pipeline/Vehicle) and bottom-right
  (Tracks/RF) floating panels. Their data is now in the Operations panels.
- **Monitor floating toolbar** — Lock/Strike/Release and mode buttons now live
  in their respective panels (Target Control and Vehicle Telemetry)
- **Auto-hide mechanism** — the `monitor-idle` CSS class, the idle timer in
  `monitor.js`, the `isIdle()` function in `app.js`, and the `lastActivity`
  tracker. No panels ever auto-hide.

### What Is Unchanged

- **Settings view** — template, CSS, JS, behavior all unchanged
- **Review page** — standalone page at `/review`, unchanged
- **Topbar** — same structure, but two tabs instead of three (Operations,
  Settings). LIVE/OFFLINE pill and FPS counter stay.
- **All API endpoints** — no backend changes needed
- **Panel features** — drag-to-reorder (SortableJS), collapse/expand chevron,
  visibility toggle dropdown, localStorage persistence per breakpoint
- **MJPEG stream setup** — same single `<img>` element, same error recovery
- **Settings thumbnail** — stays (navigates to `#operations` on click, title
  updated to "Click to go to Operations")
- **Strike confirmation modal** — stays as shared component
- **Presentation Mode** (Ctrl+Shift+P) — out of scope for this change, stays
  as-is. May need future update to also hide panel column.

### Topbar Changes

- Remove the Monitor tab button
- Rename the Control tab button to "Operations"
- Default hash changes from `#monitor` to `#operations`
- Thumbnail click target changes from `#monitor` to `#operations`
- Thumbnail title changes to "Click to go to Operations"
- Tab highlight styling stays the same

### Polling

Same pollers, simplified activation. Note: the `detections` poller was
previously only active on Control — it is now active on Operations (the merged
view), so detection log data flows to the combined view.

| Poller | Interval | Active when |
|--------|----------|-------------|
| stats | 2000ms | Always |
| tracks | 1000ms | Operations |
| target | 1000ms | Operations |
| rf | 2000ms | Operations |
| detections | 3000ms | Operations |

The Operations view update timer stays at 500ms to refresh panel content.

## Files to Delete

| File | Reason |
|------|--------|
| `hydra_detect/web/templates/monitor.html` | Merged into operations |
| `hydra_detect/web/static/js/monitor.js` | Merged into operations.js |
| `hydra_detect/web/static/css/monitor.css` | Merged into operations.css |
| `hydra_detect/web/templates/control.html` | Renamed to operations.html |
| `hydra_detect/web/static/js/control.js` | Renamed to operations.js |
| `hydra_detect/web/static/css/control.css` | Renamed to operations.css |

## Files to Create

| File | Source |
|------|--------|
| `hydra_detect/web/templates/operations.html` | Based on control.html + lock indicator from monitor.html + stream status elements |
| `hydra_detect/web/static/js/operations.js` | Based on control.js + lock indicator update logic from monitor.js |
| `hydra_detect/web/static/css/operations.css` | Based on control.css + lock indicator styles from monitor.css |

## Files to Modify

| File | Change |
|------|--------|
| `hydra_detect/web/templates/base.html` | Remove monitor tab, rename control tab to Operations, remove monitor overlay elements, update script/css references, rename stream status element IDs, update thumbnail click target and title |
| `hydra_detect/web/static/js/app.js` | Remove HydraMonitor references, rename HydraControl to HydraOperations, remove idle timer / isIdle() / lastActivity, update default hash to #operations, update poller activation, update stream watcher element IDs |
| `hydra_detect/web/static/js/panels.js` | Rename container from control-panels to operations-panels, add default tier state (tier 3 collapsed on first visit), add localStorage migration (delete old keys) |
| `hydra_detect/web/static/css/topbar.css` | Update view class selectors from view-monitor/view-control to view-operations, update MJPEG stream positioning |
| `hydra_detect/web/server.py` | Update template include from control.html to operations.html |
| `tests/test_web_api.py` | Update TestSPAShell assertions: replace `view-monitor`/`view-control` checks with `view-operations` |

## Testing

### Manual Testing Checklist

- [ ] Operations view loads as default (hash `#operations`)
- [ ] Video stream fills left 60% on desktop
- [ ] All 6 panels visible in right column
- [ ] Tier 1 panels (Vehicle, Target) expanded by default
- [ ] Tier 2 panels (Pipeline, RF) expanded by default
- [ ] Tier 3 panels (Detection Config, Log) collapsed by default
- [ ] Panels collapse/expand on chevron click
- [ ] Panels reorder via drag handle
- [ ] Panel state persists across page reload (localStorage)
- [ ] Target lock indicator appears on video when lock is active
- [ ] Lock/Strike/Release buttons work from Target Control panel
- [ ] Vehicle mode buttons work from Vehicle Telemetry panel
- [ ] Strike confirmation modal works
- [ ] Settings view works unchanged
- [ ] Settings thumbnail shows live feed and links to Operations
- [ ] Compact layout works below 1280px (video stacks on top)
- [ ] LIVE/OFFLINE pill updates correctly
- [ ] Stream lost recovery works
- [ ] No panels auto-hide after inactivity
- [ ] Review page still works at `/review`
- [ ] Old localStorage keys are cleaned up on first load

### Automated Tests

- Existing `test_web_api.py` tests pass unchanged (no backend changes) except:
  - `TestSPAShell::test_index_serves_base_html` — update assertions to check
    for `view-operations` instead of `view-monitor`/`view-control`
  - `TestSPAShell::test_index_includes_static_css` — update to check for
    `operations.css` instead of `monitor.css`/`control.css`

## Out of Scope

- Settings view redesign
- Review page changes
- New API endpoints
- Backend/pipeline changes
- Mobile-optimized layout (beyond existing compact breakpoint)
- Presentation Mode updates (may need future work to hide panel column)
