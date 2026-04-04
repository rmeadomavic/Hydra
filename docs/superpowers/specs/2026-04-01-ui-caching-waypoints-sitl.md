# Design Spec: UI Polish, Response Caching, Waypoint Export, SITL Integration

**Date:** 2026-04-01
**Status:** Approved (brainstorming session)

---

## 1. UI Polish — Data Density + Visual Depth

### Goal
Tighten the Hydra dashboard layout for ops-center data density and add subtle
visual depth (Palantir/Anduril aesthetic). Keep existing dark + green palette.

### Changes
- **Reduce whitespace** in operations panel — tighter padding, compact stat cards
- **Add depth** — subtle panel borders, inner glow on active elements, better
  contrast hierarchy between card layers (#0c0c0c → #141414 → #1c1c1c)
- **Typography tightening** — reduce font sizes in stats area, use monospace for
  numbers/data, tighten line-height
- **Status indicators** — sharper color coding, pulsing glow on active alerts
- **Track list density** — more tracks visible without scrolling
- **Consistent with Argus** — shared CSS variable names where possible

### Files
- `hydra_detect/web/static/css/variables.css` — adjust spacing/sizing tokens
- `hydra_detect/web/static/css/operations.css` — density pass
- `hydra_detect/web/static/css/base.css` — depth + glow effects
- `hydra_detect/web/static/css/topbar.css` — tighten header

### Constraints
- No layout/structural changes to HTML — CSS only
- Must remain readable on tablet at arm's length (field use)
- No external fonts or CDN dependencies

---

## 2. Response Caching — Stale Data Fallback

### Goal
Dashboard shows last-known-good data when the pipeline thread is busy, instead
of hanging or showing empty state.

### Pattern (from Argus)
```python
_response_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 30.0  # seconds

def _cached_response(key: str, fetcher: Callable) -> Any:
    now = time.monotonic()
    try:
        data = fetcher()
        _response_cache[key] = (now, data)
        return data
    except (TimeoutError, ConnectionError):
        cached = _response_cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            logger.warning("Serving stale %s (age %.1fs)", key, now - cached[0])
            return cached[1]
        raise
```

### Endpoints to Cache
- `GET /api/stats` — polled every 1s by dashboard
- `GET /api/tracks` — polled every 1s by dashboard

### Files
- `hydra_detect/web/server.py` — add `_cached_response()` helper, wrap the two
  endpoints' callback invocations

### Constraints
- Only cache on timeout/connection errors, NOT on HTTP errors
- Cache is in-memory, bounded (just 2 entries)
- Log when serving stale data with age
- No config needed — hardcoded 30s TTL

---

## 3. Waypoint Export — Detection-Driven Missions

### Goal
Export GPS-located detection tracks as QGC WPL 110 waypoint files for follow-up
missions. Two modes: post-mission (from log files) and live (from active tracks).

### Endpoints

#### A. Live Export — `GET /api/export/waypoints`
- Reads current active tracks from pipeline callback
- Filters to tracks with valid GPS (lat/lon != 0)
- Deduplicates by proximity (merge tracks within 10m)
- Sorts by confidence (highest first)
- Optional query params: `?classes=person,car` (filter by class)
- Returns QGC WPL 110 text file with Content-Disposition header

#### B. Post-Mission Export — `GET /api/review/waypoints/{file}`
- Reads a detection log JSONL file
- Extracts unique GPS-tagged detections
- Same dedup/sort/filter logic
- Same WPL 110 output format

### WPL 110 Format
```
QGC WPL 110
0	1	0	16	0	0	0	0	HOME_LAT	HOME_LON	HOME_ALT	1
1	0	3	16	5	0	0	0	TGT_LAT	TGT_LON	TGT_ALT	1
```
- Seq 0 = home (vehicle's current/last-known position)
- Seq 1+ = waypoints, cmd 16 (NAV_WAYPOINT), param1=5 (5s loiter)
- Frame 3 = MAV_FRAME_GLOBAL_RELATIVE_ALT
- Alt = configurable default (e.g., 15m for drones)

### Files
- `hydra_detect/web/server.py` — two new endpoints
- `hydra_detect/waypoint_export.py` — shared WPL generation logic (dedup, format)

### Constraints
- Max 99 waypoints (Mission Planner limit for some firmwares)
- Home position from MAVLink GPS or sim_gps
- Altitude configurable via query param `?alt_m=15` (default 15)

---

## 4. SITL Integration — Launcher + Automated Testing

### Goal
One-command SITL testing workflow: laptop runs ArduPilot SITL, Jetson runs Hydra,
connected over Tailscale UDP.

### Components

#### A. Config preset — `config.ini` overrides for SITL
Already exists via `--sim` flag. No changes needed.

#### B. SITL connection guide
Already documented in `docs/sitl-testing.md`. Add section for Windows Mission
Planner SITL + Tailscale UDP workflow.

#### C. SITL verification endpoint
Add `GET /api/preflight` check for SITL connectivity:
- Verify MAVLink heartbeat received
- Verify GPS fix type
- Report sim vs real GPS
- Report vehicle type and firmware version

### Files
- `docs/sitl-testing.md` — add Windows/Mission Planner section
- `config.ini` — document SITL UDP config example in comments

### Constraints
- No ArduPilot SITL on Jetson (ARM64, resource constrained)
- SITL runs on developer laptop (Windows/Mac/Linux)
- Connection via Tailscale UDP (no port forwarding needed)

---

## Implementation Order

1. **Response caching** (smallest, highest reliability impact)
2. **Waypoint export** (new feature, well-scoped)
3. **UI polish** (CSS-only, no functional risk)
4. **SITL docs** (documentation only)
