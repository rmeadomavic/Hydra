# RF Hunt Usability — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Author:** Claude + sorcc

## Problem

Two usability gaps make RF hunt hard to demo and hard to interpret:

1. **Kismet must be running before RF hunt starts.** Currently the web UI
   "Start Hunt" button calls `_handle_rf_start()`, which creates a new
   `RFHuntController` — but only passes `kismet_manager=self._kismet_manager`.
   That field is `None` unless `[rf_homing] enabled = true` was set at startup
   AND Kismet started successfully during `__init__`. If the operator didn't
   pre-configure `rf_homing.enabled` or Kismet wasn't running at boot, the hunt
   fails with "Cannot reach Kismet". The fix: create and start KismetManager
   on-demand inside `_handle_rf_start()`.

2. **RF data is text-only.** The Operations view RF panel shows three stats
   (RSSI, Samples, WP) and a single color bar. There is no time-series chart
   showing RSSI trend and no spatial visualization showing where readings were
   taken. For demos and field use, operators need to see signal strength changing
   over time and on a map.

## Solution

### Feature A: Kismet Auto-Start

Create `KismetManager` lazily in `_handle_rf_start()` when
`self._kismet_manager is None`. This allows RF hunt to work from the web UI
without pre-configuring `[rf_homing] enabled = true`.

**Pipeline change (`_handle_rf_start`):**

```python
def _handle_rf_start(self, params: dict) -> bool:
    if self._mavlink is None:
        logger.error("RF hunt requires MAVLink")
        return False

    # Stop any existing hunt
    if self._rf_hunt is not None:
        self._rf_hunt.stop()

    # Auto-start Kismet if no manager exists
    if self._kismet_manager is None:
        self._kismet_manager = KismetManager(
            source=self._cfg.get("rf_homing", "kismet_source", fallback="rtl433-0"),
            capture_dir=self._cfg.get("rf_homing", "kismet_capture_dir", fallback="./output_data/kismet"),
            host=self._cfg.get("rf_homing", "kismet_host", fallback="http://localhost:2501"),
            user=self._cfg.get("rf_homing", "kismet_user", fallback="kismet"),
            password=self._cfg.get("rf_homing", "kismet_pass", fallback="kismet"),
            log_dir=self._cfg.get("logging", "log_dir", fallback="./output_data/logs"),
            max_capture_mb=self._cfg.getfloat("rf_homing", "kismet_max_capture_mb", fallback=100.0),
        )
        if not self._kismet_manager.start():
            logger.error("Kismet auto-start failed — RF hunt aborted")
            self._kismet_manager = None
            return False
        logger.info("Kismet auto-started for RF hunt")

    # Build a new controller ... (existing code unchanged)
```

**Shutdown integration:** `_shutdown()` already calls
`self._kismet_manager.stop()` if it exists — no change needed. Same for
`_handle_rf_stop()`, which stops the hunt controller but correctly leaves
Kismet running for potential restarts.

**No config changes.** The existing `[rf_homing]` section provides Kismet
config (source, host, user, password). These are read with sensible fallbacks.
The only change is that `kismet_manager` is created lazily instead of only at
startup.

### Feature B: RF Signal Visualization

Two new visualizations in the RF panel, powered by RSSI history data.

#### Data Layer

**RFHuntController changes:**

Add a ring buffer that stores recent RSSI readings with timestamps and GPS
positions. The navigator already stores all samples in a deque — but the
navigator's deque uses `time.monotonic()` timestamps which aren't useful for
the web UI, and the deque can hold 20,000 items.

Instead, add a lightweight history deque directly on RFHuntController:

```python
# In __init__:
self._rssi_history: deque[dict] = deque(maxlen=300)

# Called from _do_search() and _do_homing() with the raw RSSI value.
# Accepts optional lat/lon to avoid redundant get_lat_lon() calls
# when the caller has already read GPS.
def _record_rssi(
    self, rssi: float,
    lat: float | None = None, lon: float | None = None,
) -> None:
    if lat is None or lon is None:
        lat, lon, _ = self._mavlink.get_lat_lon()
    with self._lock:
        self._rssi_history.append({
            "t": time.time(),  # wall clock for display
            "rssi": round(rssi, 1),
            "lat": round(lat, 7) if lat is not None else None,
            "lon": round(lon, 7) if lon is not None else None,
        })
```

300 samples at 0.5s polling = 2.5 minutes of history. Each entry is ~100 bytes
so the buffer is ~30 KB max.

**New method `get_rssi_history()`:**

```python
def get_rssi_history(self) -> list[dict]:
    """Return RSSI history for visualization (thread-safe)."""
    with self._lock:
        return list(self._rssi_history)
```

**Records raw RSSI (not smoothed).** The sparkline shows the actual signal
readings. The threshold dashed lines on the chart are reference markers, not
exact decision boundaries. This matches how operators think about RSSI — they
want to see the real signal, not a filtered version.

**Integration points:**
- `_do_search()` — inside the `if rssi is not None:` block, call
  `self._record_rssi(rssi)` **before** the threshold check, so all detected
  signals are recorded (including the transition sample that switches to HOMING).
- `_do_homing()` — inside the `if rssi is not None:` block, call
  `self._record_rssi(rssi, lat=lat, lon=lon)` passing the already-read GPS
  position to avoid a redundant `get_lat_lon()` call.
- `_do_lost()` — no recording (vehicle is returning to last known position,
  no valid hunt data).
- `get_status()` — no change (history is a separate endpoint).

**History lifecycle:** Since `_handle_rf_start()` creates a new
`RFHuntController` each time, the history deque is fresh on every hunt start.
No explicit clear needed.

#### API Layer

**New endpoint: `GET /api/rf/rssi_history`**

```python
@app.get("/api/rf/rssi_history")
async def api_rf_rssi_history():
    cb = stream_state.get_callback("get_rf_rssi_history")
    if cb:
        return cb()
    return []
```

**Pipeline wiring:** Add callback in `start()`:

```python
get_rf_rssi_history=self._get_rf_rssi_history,
```

```python
def _get_rf_rssi_history(self) -> list[dict]:
    if self._rf_hunt is not None:
        return self._rf_hunt.get_rssi_history()
    return []
```

Response format:

```json
[
    {"t": 1710856800.5, "rssi": -72.3, "lat": 35.1234567, "lon": -80.9876543},
    {"t": 1710856801.0, "rssi": -68.1, "lat": 35.1234570, "lon": -80.9876540}
]
```

#### Visualization Layer

Both charts render in the existing RF panel (operations.html), below the
current stats grid and above the Start/Abort buttons.

**1. RSSI Sparkline (SVG)**

A time-series line chart showing RSSI over the last 2.5 minutes.

```
┌─────────────────────────────────────────┐
│ -40 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │  converge threshold
│                              ╱──        │
│ -60 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄╱──╱┄┄┄┄┄┄┄┄ │
│                     ╱──╱                │
│ -80 ┄┄┄┄┄┄┄──╱──╱──╱┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │  detect threshold
│      ╱──╱──╱                            │
│ -100 ╱┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
└─────────────────────────────────────────┘
  -2:30                              now
```

- Y axis: RSSI in dBm (-100 to -20 range)
- X axis: relative time (newest right)
- Dashed horizontal lines at detect threshold and converge threshold
- Green line when signal improving, amber when flat, red when dropping
- SVG rendered inline in `<div id="ctrl-rf-rssi-chart">` — no canvas needed
  for a simple polyline
- Re-rendered every 2 seconds (same polling cycle as stats)

**2. GPS Signal Map (Canvas)**

A scatter plot of RSSI readings on a local coordinate grid.

```
┌─────────────────────────────────────────┐
│                                         │
│        ● ● ●                            │
│      ● ● ● ● ●                         │
│    ●   ●   ● ● ●                       │
│      ● ● ● ● ●                 ●       │
│        ● ● ●             ●  ●  ●       │
│                        ●  ●  ●  ●  ●   │
│                      ●  ●  ●  ●        │
│                         ●  ●           │
│                            ▲            │
└─────────────────────────────────────────┘
  ● = RSSI reading (color = strength)
  ▲ = current vehicle position
```

- Canvas element `<canvas id="ctrl-rf-signal-map">`
- Each reading plotted as a colored dot:
  - Red: below detect threshold
  - Yellow: between detect and converge
  - Green: above converge threshold
- Dot opacity: older readings fade (alpha = 0.3 → 1.0)
- Local coordinate conversion: lat/lon → pixel using bounding box of all
  readings with padding
- Current position marked with a triangle
- Best position marked with a star
- Canvas size: fills panel width, fixed height 200px
- Re-rendered every 2 seconds

**Implementation: all in operations.html JS.** No external dependencies. The
chart rendering is ~80 lines of vanilla JS for the sparkline and ~60 lines for
the scatter plot.

**Polling:** The existing RF status poll (`/api/rf/status`) runs every 2
seconds. Add a parallel fetch to `/api/rf/rssi_history` on the same interval,
but only when the RF panel is visible and hunt state is not "idle" or
"unavailable".

## Architecture

```
RFHuntController._do_search/homing()
    │
    ├── _poll_rssi()                  ← existing
    │       │
    │       └── rssi value
    │
    └── _record_rssi(rssi)            ← NEW: appends to _rssi_history deque
            │
            └── {t, rssi, lat, lon}

Web API
    │
    ├── GET /api/rf/status            ← existing (state, best_rssi, etc.)
    │
    └── GET /api/rf/rssi_history      ← NEW (list of {t, rssi, lat, lon})

Operations JS
    │
    ├── pollRfStatus()                ← existing (updates text stats)
    │
    └── pollRfHistory()               ← NEW (renders sparkline + map)
            │
            ├── renderRssiSparkline(data)
            │       └── SVG polyline in #ctrl-rf-rssi-chart
            │
            └── renderSignalMap(data)
                    └── Canvas dots in #ctrl-rf-signal-map
```

## Files Changed

| File | Change |
|------|--------|
| `hydra_detect/pipeline.py` | Kismet auto-start in `_handle_rf_start()`, add `get_rf_rssi_history` callback |
| `hydra_detect/rf/hunt.py` | Add `_rssi_history` deque, `_record_rssi()`, `get_rssi_history()` |
| `hydra_detect/web/server.py` | Add `GET /api/rf/rssi_history` endpoint |
| `hydra_detect/web/templates/operations.html` | Add chart containers + JS rendering |
| `tests/test_rf_hunt.py` | Tests for `_record_rssi()`, `get_rssi_history()`, Kismet auto-start |

## Testing

### Unit tests

1. **`_record_rssi` appends to deque:** Call with known values, verify
   `get_rssi_history()` returns them.
2. **Ring buffer maxlen:** Add 301 samples, verify only 300 retained and
   oldest dropped.
3. **Thread safety:** Read history while recording from another thread —
   no crash, consistent snapshot.
4. **`get_rssi_history` empty:** Returns `[]` before any hunt starts.

### Integration tests

5. **Kismet auto-start:** Mock `KismetManager.start()` to return True,
   verify `_handle_rf_start()` creates manager and passes it to
   `RFHuntController`.
6. **Kismet auto-start failure:** Mock `KismetManager.start()` to return
   False, verify `_handle_rf_start()` returns False and `_kismet_manager`
   is reset to None.
7. **API endpoint:** GET `/api/rf/rssi_history` returns list of dicts
   when hunt is active, empty list when no hunt.
8. **Shutdown cleans up auto-started Kismet:** Start via auto-start,
   shutdown pipeline, verify `KismetManager.stop()` called.

### Visual validation (bench)

9. Start RF hunt from web UI without pre-configuring `rf_homing.enabled` —
   Kismet should auto-start, hunt should begin.
10. Observe RSSI sparkline updating in real-time as RTL-SDR picks up signals.
11. Observe GPS map populating with colored dots matching RSSI readings.
12. Walk the SDR antenna closer to a signal source — chart should show
    rising RSSI, map dots should turn green.
13. Stop hunt — charts should freeze showing last data.
14. Restart hunt — charts should clear and begin fresh.

## Non-Goals (v1)

- **Persistent history:** RSSI history is in-memory only, lost on pipeline
  restart. The navigator already saves samples to CSV on hunt completion.
- **Waterfall/spectrogram:** Too complex for v1, would need raw IQ data
  from the SDR rather than Kismet-aggregated RSSI.
- **3D visualization:** Altitude not visualized on the map — 2D scatter
  is sufficient for demos.
- **Chart configuration:** No user-tunable chart settings. Fixed 300-sample
  window and 2-second refresh.
