# RF Hunt Full Integration — Design Spec

**Date:** 2026-03-19
**Status:** Approved
**Scope:** Wire rtl_power as first-class RSSI source, improve web UI panel, add Leaflet map, update docs + Docker

## Background

The RF hunt module has a complete state machine, gradient navigator, Kismet integration, web API, and UI panel. However, Kismet's rtl_433 source can only decode specific protocols — it cannot see FHSS radios (SiK, CRSF, ELRS) which are the primary targets in drone operations.

Integration testing on the Jetson discovered and fixed 5 Kismet 2025 compatibility bugs and proved that `rtl_power` (raw FFT power measurement) successfully detects FHSS signals. An `RtlPowerClient` drop-in and demo scripts exist but are not wired into the production pipeline or web UI.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| RSSI source selection | Web UI per-hunt choice with config default | Operator picks source based on target radio type |
| Threshold handling | Separate defaults per source | Kismet reports dBm, rtl_power reports relative dB — different scales |
| Dongle conflict | Mutex — one source at a time | rtl_power and Kismet can't share the RTL-SDR dongle |
| Map tiles | Auto-cache online, coordinate grid fallback offline | Field deployment may lack internet |
| Delivery | 4 independent layers | Each testable and shippable on its own |

## Layer 1: rtl_power as First-Class Source

### 1.1 Config (`config.ini`)

Add to `[rf_homing]`:
```ini
rssi_source = kismet              # "kismet" or "rtl_power"
rtl_power_tolerance_mhz = 5.0    # scan bandwidth for rtl_power
```

Existing Kismet settings remain. When `rssi_source = rtl_power`, the pipeline skips Kismet startup entirely.

### 1.2 Hunt Controller Refactor (`hunt.py`)

**Change:** Accept an injected RSSI client instead of creating `KismetClient` internally.

Constructor signature becomes:
```python
def __init__(self, mavlink, *, rssi_client, kismet_manager=None, ...)
```

Internal rename: `self._kismet` becomes `self._rssi_client` throughout.

The `_poll_rssi` restart logic checks `self._kismet_manager is not None` before attempting Kismet restart — this naturally skips restart when using rtl_power (manager will be None).

**Poll failure recovery:** Add `self._consecutive_poll_failures: int = 0` to the controller. In `_poll_rssi`, increment on `None` return, reset on success. If failures exceed `max_consecutive_poll_failures` (default: 10), abort the hunt. This catches dongle-unplugged scenarios for both Kismet and rtl_power.

**`_last_rssi` tracking:** Update `_last_rssi` in both `_do_search()` and `_do_homing()` (currently only updated in homing). This is needed for the Layer 2 RSSI chart to show readings during search phase.

**State history:** Add `self._state_history: deque = deque(maxlen=10)` to `__init__`. Append `(state, elapsed_sec)` in `_set_state()`. Expose via `get_status()` for the Layer 2 timeline.

### 1.3 Pipeline Integration (`pipeline.py`)

RF init block becomes:
```python
rssi_source = cfg.get("rf_homing", "rssi_source", fallback="kismet")

if rssi_source == "rtl_power":
    rssi_client = RtlPowerClient(
        tolerance_mhz=cfg.getfloat("rf_homing", "rtl_power_tolerance_mhz", fallback=5.0),
    )
    kismet_manager = None
else:
    kismet_manager = KismetManager(...)
    kismet_manager.start()
    rssi_client = KismetClient(...)

controller = RFHuntController(mavlink, rssi_client=rssi_client, kismet_manager=kismet_manager, ...)
```

The web `_handle_rf_start` handler also creates the appropriate client when starting a hunt from the UI with a different source than config default.

**Dongle mutex on source switch:** When `_handle_rf_start` receives a source different from what's currently running, it must release the dongle first:
- Switching Kismet → rtl_power: stop KismetManager before creating RtlPowerClient.
- Switching rtl_power → Kismet: start KismetManager before creating KismetClient.
A `_switch_rssi_source()` helper in pipeline.py manages this handoff.

### 1.4 Web API Changes

`POST /api/rf/start` accepts optional field:
- `rssi_source`: `"kismet"` or `"rtl_power"` (default: config value)

`GET /api/rf/status` response gains:
- `rssi_source`: which source is active

Validation: if `rssi_source=rtl_power` and rtl_power binary not found, return 503.

**Source-aware threshold validation:** Widen server-side validation ranges to encompass both scales: `rssi_threshold_dbm` accepts -100 to +30, `rssi_converge_dbm` accepts -90 to +30. The hunt controller already clamps values, and the web UI pre-fills appropriate defaults per source, so wide server validation is safe.

### 1.5 Web UI Source Selector

Operations panel RF config section gains a "Source" dropdown above mode selector. On change:
- Labels swap: "RSSI Threshold (dBm)" ↔ "Signal Threshold (dB)"
- Defaults swap: Kismet (-80/-40 dBm) ↔ rtl_power (0/+8 dB)
- Slider ranges adjust accordingly

### 1.6 Install (`hydra-setup.sh`)

No new dependencies — `rtl_power` ships with `rtl-sdr` package already installed. Update the status echo to mention rtl_power support.

### 1.7 RSSI Client Protocol

Add `hydra_detect/rf/rssi_protocol.py` defining the interface both clients satisfy:
```python
class RSSIClient(Protocol):
    def check_connection(self) -> bool: ...
    def get_rssi(self, *, mode: str, bssid: str | None, freq_mhz: float | None) -> float | None: ...
    def reset_auth(self) -> None: ...
    def close(self) -> None: ...
```

The `RFHuntController` type-hints `rssi_client: RSSIClient`. This aids mypy and documents the contract for future sources (HackRF, WiFi iw, etc.).

### 1.8 Tests

- Unit test `RtlPowerClient` (mock subprocess, verify interface compatibility)
- Update `test_rf_hunt.py` to use `rssi_client=` parameter
- Integration test: `RtlPowerClient` → hunt controller with mock MAVLink (already proven in demo)

## Layer 2: Improved RF Panel

### 2.1 RSSI Time-Series Chart

Canvas-based sparkline in the RF status section. Shows last 60 RSSI readings. Color regions:
- Green: below search threshold
- Yellow: between threshold and converge
- Red: above converge threshold

Implementation: `<canvas>` element, ~40 lines of JS. No library needed. Data comes from polling `/api/rf/status` — add `last_rssi` field to response.

### 2.2 State Timeline

Horizontal bar below the state badge showing transitions with timestamps. Data: new `state_history` field in `/api/rf/status` response (list of `{state, elapsed_sec}`). Bounded to last 10 transitions.

### 2.3 Waypoint Progress Bar

Replace "4/31" text with a visual progress bar (`<div>` with width percentage). Keep the fraction as overlay text.

### 2.4 Source Indicator

Badge next to the state badge: "KISMET" (blue) or "RTL_POWER" (orange).

## Layer 3: Leaflet Map

### 3.1 Leaflet Integration

Bundle Leaflet.js locally in `hydra_detect/web/static/js/vendor/leaflet.min.js` and `static/css/vendor/leaflet.min.css` (~55KB total). This ensures the map works fully offline without CDN access. Add to operations.html:
- `<link>` for local Leaflet CSS
- `<script>` for local Leaflet JS
- `<div id="rf-map">` container, 300px tall, below RF status section

### 3.2 Tile Strategy

Default: OpenStreetMap tiles (auto-cached by browser on first view).
Fallback: On tile load error, switch to `L.CRS.Simple` with a coordinate grid overlay (lat/lon lines every 0.001 degrees). Detection: count tile errors; if > 5 in first 10 seconds, switch to grid mode.

### 3.3 Map Layers

| Layer | Visual | Update frequency |
|-------|--------|-----------------|
| Search pattern | Blue polyline (waypoints connected) | Once on hunt start |
| Vehicle position | Arrow marker showing heading | Every poll (2s) |
| RSSI samples | Colored circles (green→yellow→red by strength) | Every poll |
| Best position | Star marker with RSSI label | Every poll |
| Converge radius | Dashed circle at converge threshold distance | Once on hunt start |

### 3.4 API Changes

`GET /api/rf/status` response gains:
- `recent_samples`: `[{lat, lon, rssi}, ...]` — last 200 samples
- `vehicle_lat`, `vehicle_lon`: current position (from MAVLink GPS)

New endpoint:
- `GET /api/rf/waypoints` — returns `[[lat, lon], ...]` for current search pattern. Called once by the UI when a hunt starts; avoids re-serializing 1000+ waypoints every poll cycle.

**Efficient sample access:** Add `get_recent_samples(n: int = 200)` to `GradientNavigator` that slices only the tail of the deque, avoiding a full 20k copy on every poll.

These fields are only populated when a hunt is active.

### 3.5 Map Behavior

- Map auto-centers on vehicle position when hunt starts
- Samples accumulate as colored dots (older dots fade)
- On CONVERGED: map zooms to best position with a pulsing marker
- Map hidden when RF hunt is unavailable/idle (saves DOM resources)

## Layer 4: Docs + Docker

### 4.1 Documentation Updates

**`docs/features/rf-homing.mdx`:**
- Add "RSSI Source" section explaining Kismet vs rtl_power and when to use each
- Add threshold guidance table: Kismet dBm ranges vs rtl_power dB ranges
- Update config reference with new fields
- Add map UI description

**New `docs/guides/rf-hunt-testing.md`:**
- Pre-requisites: RTL-SDR dongle, Kismet installed
- Step 1: Verify dongle (`lsusb`)
- Step 2: Run power scan (`scripts/rf_power_scan.py`)
- Step 3: Run hunt demo (`scripts/rf_hunt_demo.py`)
- Step 4: Run integration tests (`pytest tests/test_rf_integration.py`)
- Step 5: Enable in pipeline (`config.ini` settings)
- Troubleshooting: dongle permissions, port conflicts, Kismet auth

### 4.2 Docker

Add to Dockerfile:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends rtl-sdr && rm -rf /var/lib/apt/lists/*
```

Document in `docs/jetson-setup-guide.md`:
```bash
docker run ... --device /dev/bus/usb ...
```

Kismet stays host-only (large package, needs privileged device access, complex systemd integration).

## File Change Summary

| Layer | Files Modified | Files Created |
|-------|---------------|---------------|
| 1 | `hunt.py`, `pipeline.py`, `server.py`, `operations.html`, `operations.js`, `config.ini`, `hydra-setup.sh`, `rtl_power_client.py` | `rssi_protocol.py`, `tests/test_rf_rtl_power.py` |
| 2 | `server.py`, `operations.html`, `operations.js`, `hunt.py` | — |
| 3 | `operations.html`, `operations.js`, `server.py` | — |
| 4 | `Dockerfile`, `docs/features/rf-homing.mdx` | `docs/guides/rf-hunt-testing.md` |

## Constraints

- **Memory:** RtlPowerClient spawns a subprocess per scan (~1-2s). No persistent memory allocation beyond the hunt controller's existing 20k sample buffer.
- **Dongle exclusivity:** Only one process can use the RTL-SDR at a time. Pipeline enforces mutex by choosing source at hunt start.
- **Real-time:** rtl_power scans take 1-2s. Poll interval must be >= 2s when using rtl_power. The pipeline enforces `poll_interval_sec = max(poll_interval_sec, 2.0)` when `rssi_source = rtl_power` to prevent overlapping subprocess spawns.
- **Leaflet bundle:** ~40KB JS + ~15KB CSS bundled locally in static/. No CDN dependency, no server-side dependencies.
- **Backward compatibility:** Existing configs with no `rssi_source` field default to `kismet` (current behavior unchanged).
