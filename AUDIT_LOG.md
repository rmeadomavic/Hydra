# Hydra Dashboard Audit Log

## Session: 2026-03-31 (morning UI polish pass)

### STATUS: COMMITTED TO MAIN — PENDING DEPLOY

After the Jetson's `git reset --hard` rolled back UI polish commits, a full
audit identified 15 issues. All fixed in a single batch commit.

| # | Priority | Issue | Fix |
|---|----------|-------|-----|
| 1 | HIGH | Battery shows "0.0V 0%" in red | Show "--" when battery_v < 0.5 (no sensor) |
| 2 | HIGH | Position text orange | Remove inline `color:var(--warning)` from template |
| 7 | HIGH | Active tab pill green fill gone | CSS: `background: var(--ogt-green)` on `.topbar-tab.active` |
| 8 | HIGH | OSD mode shows raw internal values | Human labels: "Basic", "Enhanced", "MSP DisplayPort" |
| 9 | HIGH | Release Lock enabled with 0 tracks | Disable button when track count is 0 |
| 3 | MEDIUM | Status dots (CAM/MAV/GPS) gone | Re-added to topbar with color-coded indicators |
| 4 | MEDIUM | Track counter badge gone | Re-added `0 TRACKS` badge to topbar |
| 5 | MEDIUM | Footer shows only "UNCLASSIFIED" | 3-column: callsign+uptime \| UNCLASSIFIED \| SORCC |
| 6 | MEDIUM | LOITER mode not highlighted | Added `.mode-active` class toggled per vehicle_mode |
| 10 | MEDIUM | LOW BW / LOW LIGHT no tooltips | Added `title` attributes explaining each badge |
| 11 | MEDIUM | Settings nav has no icons | Added emoji icons to each nav button |
| 12 | MEDIUM | Geofence 0,0 shows no warning | Warning banner when lat/lon both 0.0 |
| 13 | LOW | Video OSD text hard to read | Semi-transparent dark backdrop behind HUD |
| 14 | LOW | Mission start allows blank name | Validation: toast + focus if empty |
| 15 | LOW | Detection log empty with no message | "No detections yet" empty state |

---

## Session: 2026-03-30 (overnight ~12:46 AM → ~8:30 AM)

### STATUS: ALL FIXES MERGED TO MAIN

Test suite: **954 passed**, 1 skipped, 0 failures

---

### Critical Fix: Video Stream (VIDEO LOST)

**Problem:** MJPEG stream at `/stream.mjpeg` never delivered frames to browser
despite camera working at 9+ FPS.

**Root cause:** Starlette's `BaseHTTPMiddleware` deadlocks `StreamingResponse`
for infinite streams. Two middleware classes wrapped every response including
the MJPEG endpoint.

**Fix:** Replaced MJPEG with snapshot polling:
- `GET /stream.jpg` returns single JPEG frame as regular `Response`
- JS polls at ~30 fps via `img.src = '/stream.jpg?t=<timestamp>'`
- 33ms server-side cache prevents re-encoding on rapid polls
- Middleware converted from `BaseHTTPMiddleware` to pure ASGI

**Files:** `server.py`, `app.js`

---

### Security Fixes

| Fix | Files | Impact |
|-----|-------|--------|
| 3 stored XSS in review_export.py | `review_export.py` | Script-tag breakout, innerHTML injection, popup HTML |
| Remove `'unsafe-inline'` from CSP | `server.py` | Browser blocks injected scripts |
| Extract all inline scripts to external JS | 4 templates + 4 new JS files | Enables CSP enforcement |
| SRI hashes on Leaflet CDN | `review.html`, `review_export.py` | Prevents CDN tampering |
| Guard 22 `request.json()` calls | `server.py` | Returns 400 on malformed input (was 500) |
| Same-origin auth bypass | `server.py` | Dashboard works without token; external API still requires it |
| TAK port validation | `server.py` | `int("abc")` crash → 400 error |
| Abort callback try/except | `server.py` | Instructor RTL never fails silently |
| Auth memory leak fix | `server.py` | Prune empty IP entries from `_auth_failures` |

---

### UI/UX Fixes

| Fix | Files |
|-----|-------|
| View-switch stream pause/resume | `app.js` |
| Settings form load (auth bypass for GET /api/config/full) | `server.py` |
| Settings responsive layout (<1280px) | `settings.css` |
| Camera tab initial render (requestAnimationFrame) | `settings.js` |
| Stream quality auth bypass (LOW BW toggle) | `server.py` |
| Thumbnail direct polling in settings view | `app.js` |
| Double-click fullscreen video | `app.js`, `topbar.css` |
| Touch targets for track buttons (20px → 36px) | `operations.css` |
| Text overflow ellipsis on panel values | `operations.css` |
| Steam Deck breakpoint smoothing | `operations.css` |
| iPad 1024px CSS breakpoint | `operations.css` |
| Dropdown refresh every 30s | `operations.js` |
| Power Mode "Loading..." retry | `operations.js` |
| Rickroll window.open fix | `settings.js` |
| YouTube iframe CSP (frame-src) | `server.py` |
| API token prompt on 401 | `app.js` |

---

### Detection Pipeline Fixes

| Fix | Files |
|-----|-------|
| Overlay bounds clamping (crash on edge targets) | `overlay.py` |
| Alert deduplication by label per frame | `pipeline.py` |
| Track list DOM diffing (prevents wrong-target-lock race) | `operations.js` |
| Image save rate limiting (1/sec max) | `detection_logger.py` |

---

### Config & Schema Fixes

| Fix | Files |
|-----|-------|
| `dogleg_bearing` schema: FLOAT → STRING | `config_schema.py` |
| 5 missing keys added (arm_channel, etc.) | `config_schema.py` |
| Dead `[drop] enabled` key removed | `config.ini` |

---

### Infrastructure

| Addition | Files |
|----------|-------|
| GitHub Actions CI (lint + tests) | `.github/workflows/ci.yml` |
| Deploy script | `scripts/deploy.sh` |
| Pixhawk buzzer API | `mavlink_io.py`, `pipeline.py`, `server.py` |
| Temp ZIP cleanup after export | `server.py` |
| Log review capped at 50k records | `server.py` |
| Flaky test fixed (cooldown monkeypatch) | `test_autonomous.py` |
| Root-skip for chmod test | `test_config_api.py` |
| 8 XSS tests for review_export | `test_review_export.py` |
| 2 auth bypass tests | `test_web_api.py` |

---

### Documentation Updated

- `CLAUDE.md` — video architecture, API hardening, overlay, security, deployment
- `docs/deployment.md` — deploy script, stash requirement, container naming
- `docs/dashboard.md` — snapshot polling, fullscreen, same-origin auth
- `docs/api-reference.md` — `/stream.jpg`, legacy MJPEG note, `/api/vehicle/beep`
- `docs/development.md` — CI, CSP, auth patterns, XSS prevention

---

### Known Issues (Not Fixed)

1. **review.html uses `innerHTML`** for summary/legend — escaped with `esc()` but
   could be converted to DOM methods for purity
2. **`style-src 'unsafe-inline'`** remains in CSP — Leaflet needs inline styles
   for marker positioning; removing would break the map
3. **No test for Pixhawk buzzer** — `play_tune()` untested (requires MAVLink mock)
4. **Docker layer caching** — `COPY hydra_detect/` can use stale cache if file
   timestamps don't change. Use `--no-cache` for reliable deploys.

---

### Deployment Checklist

```bash
cd ~/Hydra
git stash                    # Local config.ini changes block git pull
git pull origin main
sudo docker build --no-cache -t hydra-detect:latest .
sudo systemctl restart hydra-detect
# Wait 20s for YOLO model load
curl --max-time 3 -s -o /dev/null -w "%{http_code}" http://localhost:8080/stream.jpg
# Should return 200
```
