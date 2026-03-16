# Jetson Hardware Testing Checklist

Systematic testing plan for Hydra Detect on Jetson Orin Nano with Pixhawk 6C,
HDZero video, QGroundControl on Steam Deck, and SDR integration.

## Prerequisites

- [ ] Verify Jetson UART enabled: `sudo cat /proc/tty/driver/serial` — confirm `/dev/ttyTHS1`
- [ ] Gather UART wiring supplies (dupont jumpers, GND wire)
- [ ] Identify SDR model and install drivers (`apt install rtl-sdr` or `hackrf`)
- [ ] Run test suite baseline: `python -m pytest tests/ -v` — all green

---

## 1. GPIO/UART Connection to Pixhawk

**Goal:** Replace USB-C bench link with UART for field deployment.

**Wiring:** Jetson 40-pin header UART (`/dev/ttyTHS1`) → Pixhawk TELEM2

| Jetson Pin | Pixhawk TELEM2 | Notes |
|------------|----------------|-------|
| TX         | RX             | Cross-connect |
| RX         | TX             | Cross-connect |
| GND        | GND            | Common ground required |

**Pixhawk side** is already configured (see `docs/pixhawk-setup.md`):
- `SERIAL2_PROTOCOL = 2` (MAVLink2)
- `SERIAL2_BAUD = 921` (921600)

### Tests

- [ ] Wire TX→RX, RX→TX, GND→GND between Jetson and Pixhawk TELEM2
- [ ] Update `config.ini`: `connection_string = /dev/ttyTHS1`, `baud = 921600`
- [ ] Confirm MAVLink heartbeat received over UART
  - Quick test: `mavproxy.py --master=/dev/ttyTHS1 --baudrate=921600`
  - Hydra test: run pipeline, check logs for `heartbeat` / `vehicle connected`
- [ ] Verify GPS data stream (2 Hz) — compare lat/lon with Mission Planner values
- [ ] Test reconnection: unplug/replug UART cable → Hydra should reconnect (exponential backoff)
- [ ] Update Docker run command: `--device /dev/ttyTHS1:/dev/ttyTHS1`
- [ ] Update `hydra-detect.service` if using systemd

### Pass Criteria
- Heartbeat received within 10 seconds of connection
- GPS coordinates match Mission Planner within ~1m
- Reconnection completes within 30 seconds after cable replug
- No crash or data corruption during disconnect

---

## 2. HDZero Video Input to Jetson

**Goal:** Use HDZero Nano 90 camera (via Freestyle V2 VTX) as detection source.

### Hardware

- **Camera:** HDZero Nano 90
- **VTX:** HDZero Freestyle V2 (has analog CVBS output pad)
- **Capture path:** VTX CVBS output → USB UVC capture dongle → Jetson USB
- **Receivers available:** HDZero Monitor, Goggles 1, Goggles 2

### Wiring (on-vehicle)

The Nano 90 feeds the Freestyle V2 VTX digitally. The VTX has a **CVBS analog
output** pad — wire this to a cheap USB UVC capture dongle plugged into the
Jetson. This lets Hydra process the same feed the pilot sees in goggles.

| Source | Destination | Notes |
|--------|-------------|-------|
| VTX analog out (CVBS) | Capture dongle (yellow RCA / bare wire) | Video signal |
| VTX GND | Capture dongle GND | Common ground |
| Capture dongle USB | Jetson USB port | V4L2 device |

### Tests

- [ ] Wire VTX CVBS output to USB capture dongle
- [ ] Plug dongle into Jetson USB port
- [ ] Verify device appears: `v4l2-ctl --list-devices` → note `/dev/videoN` index
  - Typical name: "USB Video", "AV TO USB2.0", or "Macrosilicon"
- [ ] Verify auto-detect works: set `source = auto` in config.ini, start Hydra
  - Check logs for "Auto-detected capture card: /dev/videoN"
  - If webcam also connected, auto prefers webcam — set `source = N` to force
- [ ] Verify live video on web dashboard (`http://<jetson-ip>:8080`)
- [ ] Check web API device list: `curl http://<jetson-ip>:8080/api/camera/sources`
  - Capture card should appear with `"type": "capture"`
- [ ] Test runtime switching: webcam → capture card via web UI or API
- [ ] Measure capture latency (point camera at stopwatch, compare with dashboard)
- [ ] Run detection pipeline — verify YOLO detects at CVBS resolution (720x480)
- [ ] Note FPS with HDZero capture vs USB webcam (benchmark comparison)
- [ ] Test with HDZero powered off — Hydra should show reconnection warnings, not crash

### CVBS Signal Notes

- CVBS from the Freestyle V2 is **NTSC 720×480 or PAL 720×576** (interlaced)
- Most USB capture dongles deinterlace automatically
- Hydra resizes to configured `width × height` (default 640×480), so resolution
  difference shouldn't matter for detection
- Image quality is lower than direct USB webcam — this is expected for analog

### Pass Criteria
- Live video displays on web dashboard with no corruption
- Detection pipeline sustains ≥5 FPS with capture card source
- Auto-detect or manual source selection works reliably
- Capture latency acceptable for use case (document measured value)

---

## 3. QGroundControl on Steam Deck

**Goal:** Use Steam Deck as portable GCS via QGC instead of Mission Planner.

### Tests

- [ ] Install QGroundControl on Steam Deck (AppImage or Flatpak)
- [ ] Connect QGC to Pixhawk via SiK 915 MHz radio
  - Plug ground SiK radio into Steam Deck USB
  - QGC should auto-detect serial at 57600 baud
- [ ] Verify QGC receives telemetry: attitude, GPS position, battery voltage
- [ ] Test WiFi UDP as alternative link:
  - On Jetson: `mavproxy.py --master=/dev/ttyTHS1 --baudrate=921600 --out udp:<steamdeck-ip>:14550`
  - Or configure ArduPilot to output on both TELEM1 and TELEM2
- [ ] Map Steam Deck gamepad buttons in QGC (mode switch, arm/disarm)

### Pass Criteria
- QGC displays live telemetry (attitude indicator, GPS, battery)
- Mode changes from QGC are reflected on vehicle
- Gamepad controls responsive and correctly mapped

---

## 4. Alerts & Status Messages via QGC

**Goal:** Understand how Hydra alerts display in QGC vs Mission Planner.

### Key Differences: QGC vs Mission Planner

| Feature | Mission Planner | QGroundControl |
|---------|----------------|----------------|
| STATUSTEXT display | Messages tab (persistent log) | Toast notification bar (transient) |
| Message persistence | Scrollable history | Disappears after timeout |
| Custom MAV_CMD | Actions tab, easy to send | MAVLink Inspector or custom buttons |
| NAMED_VALUE display | MAVLink Inspector | MAVLink Inspector only |
| OSD integration | N/A (MP is desktop) | N/A (QGC is GCS, not OSD) |

### Tests

- [ ] Trigger Hydra detection → observe STATUSTEXT in QGC notification bar
- [ ] Document where alerts appear and how long they persist
- [ ] Test different severity levels (config.ini `severity` 0-7):
  - 0 = EMERGENCY, 2 = WARNING, 6 = INFO — note which QGC shows/hides
- [ ] Set `osd_mode = statustext` in config.ini (NAMED_VALUE won't display in QGC)
- [ ] Test lock/strike/unlock commands from QGC:
  - MAV_CMD_USER_1 (31010) = Lock, param1 = track_id
  - MAV_CMD_USER_2 (31011) = Strike, param1 = track_id
  - MAV_CMD_USER_3 (31012) = Unlock
  - Use QGC MAVLink Inspector → Send Command, or map to joystick button
- [ ] Measure alert latency: object enters frame → notification on Steam Deck
- [ ] Document findings for future reference

### Pass Criteria
- STATUSTEXT alerts visible in QGC within 2 seconds of detection
- Lock/unlock commands successfully received by Hydra (check Hydra logs)
- Clear understanding of QGC limitations vs MP documented

---

## 5. SDR / RF Exploration

**Goal:** Get SDR working with Kismet for RF hunt integration.

### Tests

- [ ] Verify SDR device on Jetson:
  - RTL-SDR: `rtl_test`
  - HackRF: `hackrf_info`
- [ ] USB passthrough into Docker container: `--device /dev/bus/usb`
- [ ] Install Kismet on Jetson (REST API on `localhost:2501`)
- [ ] Run Kismet with SDR source — verify web UI shows captured signals
- [ ] Configure Hydra `[rf_homing]` section in config.ini with Kismet endpoint
- [ ] Test RSSI data feed from Kismet into Hydra RF hunt module
- [ ] (Stretch) Try `dump1090` for ADS-B reception — aircraft tracking
- [ ] (Stretch) Spectrum survey of test area with gqrx or SigDigger

### Pass Criteria
- SDR device recognized and functional on Jetson
- Kismet receives data from SDR and serves REST API
- Hydra RF hunt module reads RSSI values from Kismet

---

## 6. Integration & Stress Testing

**Goal:** Everything running simultaneously, verify stability.

### Tests

- [ ] Full end-to-end: UART + HDZero + QGC + alerts all running simultaneously
  - Verify FPS ≥5 sustained
  - Verify no resource contention
- [ ] Thermal profiling: `tegrastats` or `jtop` during 30-minute sustained detection
  - Note throttle temperature and sustained clock speeds
- [ ] Memory profiling: monitor shared 8GB CPU/GPU RAM under load
  - Verify no OOM after extended run
- [ ] Access web dashboard from Steam Deck browser alongside QGC
  - `http://<jetson-ip>:8080` — verify MJPEG stream works
- [ ] Failure mode testing:
  - [ ] Yank UART cable → graceful degradation, no crash
  - [ ] Kill/disconnect camera → pipeline handles reconnect
  - [ ] Disconnect WiFi → web dashboard recovers when reconnected
  - [ ] Verify vehicle stays safe in all failure scenarios

### Pass Criteria
- System stable for 30+ minutes under full load
- Jetson stays below thermal throttle (or degrades gracefully)
- Memory usage bounded (no growth over time)
- All failure modes recover without crash or unsafe vehicle behavior

---

## 7. OSD Overlay Testing (FPV Goggles)

**Goal:** Verify Hydra detection data appears in FPV goggles via FC OSD.

See `docs/hdzero-osd-setup.md` for full wiring details.

### STATUSTEXT Mode (simplest, default)

- [ ] Set `[osd] mode = statustext` in config.ini
- [ ] Verify text appears in OSD message panel on goggles
- [ ] Works with Pixhawk 6C via MSP DisplayPort (no MAX7456 chip needed) —
      requires spare UART TX wired to Freestyle V2 VTX RX pad
- [ ] Measure OSD update latency (should be <200ms)

### NAMED_VALUE Mode (richer data, requires Lua)

- [ ] Copy `scripts/hydra_osd.lua` to FC SD card (`APM/scripts/`)
- [ ] Set FC params: `SCR_ENABLE=1`, `SCR_HEAP_SIZE=65536`, `OSD_TYPE=1`, `OSD1_ENABLE=1`
- [ ] Verify OSD displays: track count, FPS, inference time, locked track ID
- [ ] Test "HYDRA: NO LINK" warning when Jetson stops sending for >3s
- [ ] Test "HYDRA: WAITING" before first data arrives

### HDZero MSP DisplayPort

- [ ] Wire FC UART TX → HDZero VTX RX for MSP OSD
- [ ] Set `OSD_TYPE = 3` (MSP DisplayPort)
- [ ] Verify OSD composites onto digital video feed
- [ ] Test with different VTX firmware versions

### Pass Criteria
- Detection data visible in goggles within 200ms of detection
- OSD survives Hydra disconnect/reconnect gracefully

---

## 8. Autonomous Strike Safety Validation

**Goal:** Verify all autonomous strike safeguards work on real hardware.

This is the most dangerous feature — every criterion must be tested independently.

### Qualification Chain (ALL must pass for a strike)

- [ ] Controller enabled in config.ini (`enabled = true`)
- [ ] Vehicle in allowed mode (default: AUTO only)
- [ ] Vehicle inside geofence
  - [ ] Circle mode: test haversine distance at center, edge, and outside
  - [ ] Polygon mode: test ray-casting with 4-vertex square geofence
- [ ] No cooldown active (`strike_cooldown_sec`)
- [ ] Target class in whitelist (`allowed_classes`)
- [ ] Confidence above threshold (default 0.85)
- [ ] Track seen for N consecutive frames (`min_track_frames`, default 5)

### Negative Tests (each should block a strike)

- [ ] Disable controller → no strike
- [ ] Switch to MANUAL mode → no strike
- [ ] Drive outside geofence → no strike
- [ ] Trigger during cooldown → no strike
- [ ] Target class not in whitelist (e.g., "person") → no strike
- [ ] Confidence 0.70 (below 0.85) → no strike
- [ ] Track only 3 frames (below min 5) → no strike

### Audit Logging

- [ ] Verify `hydra.audit` logger captures all strike decisions
- [ ] Format includes: timestamp, track_id, label, confidence, frames, vehicle mode, position

### Pass Criteria
- Every safeguard independently blocks strikes when it should
- Audit log captures full context for every decision
- No false strikes possible when any single criterion fails

---

## 9. Detection Logging & Review

**Goal:** Verify detection logs are complete and exportable for post-mission review.

### Log Format

- [ ] JSONL mode: each line is valid JSON (`python -m json.tool`)
- [ ] CSV mode: headers present, consistent column count
- [ ] Logs saved to `log_dir` with timestamped filenames

### Image Snapshots

- [ ] `save_images = true` — full-frame JPEG at configured quality
- [ ] Inspect file size and quality (default 90%)
- [ ] Verify bounding boxes overlaid on saved images
- [ ] `save_crops = true` — cropped object images saved separately
  - [ ] Verify crop dimensions match track bounding box
  - [ ] Test crop for objects near frame edges (no overflow)

### GPS Geo-tagging

- [ ] Detection logs include `lat`, `lon`, `alt` from MAVLink GPS
- [ ] Test with no GPS fix — should log null/NaN, not crash

### Review Export

- [ ] `python -m hydra_detect.review_export /path/to/detections.jsonl -o report.html`
- [ ] Verify standalone HTML works offline
- [ ] Test with large log files (10000+ detections)

### Pass Criteria
- All log formats parseable and complete
- Images readable and correctly annotated
- GPS coordinates present when fix is available

---

## 10. Jetson Power & Performance Profiling

**Goal:** Characterize performance envelope across power modes.

### Power Modes

- [ ] Check current mode: `sudo nvpmodel -q`
- [ ] Test each available mode (5W, 10W, 15W, MAXN):
  - [ ] Measure detection FPS in each mode
  - [ ] Measure GPU temp at steady state
  - [ ] Note which mode drops below 5 FPS (unacceptable)
- [ ] Verify `jetson_clocks` is running: `sudo jetson_clocks`

### Thermal Zones

- [ ] Monitor CPU temp: `/sys/devices/virtual/thermal/thermal_zone0/temp`
- [ ] Monitor GPU temp: `/sys/devices/virtual/thermal/thermal_zone1/temp`
- [ ] Run 30+ minute detection, log temps every 10s
- [ ] Identify throttle point (~80°C on Orin Nano)

### YOLO Model Size Impact

- [ ] Test yolov8n (fastest, ~6MB) — baseline FPS
- [ ] Test yolov8s (balanced) — FPS delta
- [ ] Test yolov8m (heavier) — FPS delta, memory impact
- [ ] Document model-size vs FPS vs temperature tradeoffs

### Pass Criteria
- FPS ≥5 sustained in MAXN mode with yolov8n
- Temperature stays below 85°C (or degrades gracefully)
- No OOM with any tested model on 8GB Jetson

---

## 11. Web API & Dashboard Under Load

**Goal:** Verify web interface doesn't degrade detection performance.

### MJPEG Stream

- [ ] Open `/stream.mjpeg` in browser during detection
- [ ] Measure stream latency (object in frame → visible on dashboard)
- [ ] Test from multiple clients simultaneously (Steam Deck + laptop)
- [ ] Verify no dropped frames under load

### API Endpoints

- [ ] `/api/stats` — data updates in near real-time
- [ ] `/api/camera/sources` — lists available video devices correctly
- [ ] `/api/review/logs` — lists all detection log files
- [ ] `/api/review/log/{filename}` — parses JSONL and CSV correctly

### Security

- [ ] If `api_token` is set, mutation endpoints reject unauthenticated requests
- [ ] Read-only endpoints (`/api/stats`, `/stream.mjpeg`) work without auth
- [ ] Path traversal protection on log file endpoints

### Pass Criteria
- Dashboard + API access doesn't drop detection FPS below 5
- All endpoints return correct data
- Auth enforced on control endpoints when token is set

---

## 12. Preflight & Docker Validation

**Goal:** Verify deployment tooling works before field testing.

- [ ] Run `bash scripts/jetson_preflight.sh` — all checks PASS (0 FAILs)
  - Python, pip, NVIDIA utilities, OpenCV, FastAPI
  - Camera device, serial device, dialout group
  - config.ini present, model files exist
- [ ] Verify Docker device passthrough works for all devices simultaneously:
  - `--device /dev/video0` (camera)
  - `--device /dev/ttyTHS1` (UART to Pixhawk)
  - `--device /dev/bus/usb` (SDR if enabled)
- [ ] Test `sudo systemctl restart hydra-detect` — port 8080 available within 5s
- [ ] Verify fail-safe defaults in config.ini:
  - `[autonomous] enabled = false`
  - `[osd] enabled = false`
  - `[rf_homing] enabled = false`

---

## Notes & Observations

_Use this section to record findings during testing._

| Date | Test | Result | Notes |
|------|------|--------|-------|
|      |      |        |       |
