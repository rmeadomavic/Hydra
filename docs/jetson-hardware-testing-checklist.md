# Jetson Hardware Testing Checklist

Systematic testing plan for Hydra Detect on Jetson Orin Nano with Pixhawk 6C,
HDZero video, QGroundControl on Steam Deck, and SDR integration.

## Prerequisites

- [x] Verify Jetson UART enabled: `sudo cat /proc/tty/driver/serial` - confirm `/dev/ttyTHS1`
- [x] Gather UART wiring supplies (dupont jumpers, GND wire)
- [ ] Identify SDR model and install drivers (tested path: RTL-SDR via `apt install rtl-sdr`; HackRF setup is not yet automated)
- [ ] Run test suite baseline: `python -m pytest tests/ -v` - all green

--

## 1. GPIO/UART Connection to Pixhawk

**Goal:** Replace USB-C bench link with UART for field deployment.

**Wiring:** Jetson 40-pin header UART (`/dev/ttyTHS1`) → Pixhawk TELEM2

| Jetson Pin | Pixhawk TELEM2 Pin | Notes |
|------|--------|----|
| Pin 8 (TX)  | Pin 2 (TX label) | Pixhawk "TX" is its output → Jetson receives |
| Pin 10 (RX) | Pin 3 (RX label) | Pixhawk "RX" is its input → Jetson sends |
| Pin 6 (GND) | Pin 6 (GND)      | Common ground required |

> **Note:** The Pixhawk TELEM2 JST-GH pin labels are from the Pixhawk's perspective.
> "TX" means the Pixhawk transmits on that pin, so it connects to the Jetson's RX.
> Both are 3.3V logic — no level shifter needed.

**Pixhawk side** is already configured (verified over USB 2026-03-17):
- `SERIAL2_PROTOCOL = 2` (MAVLink2)
- `SERIAL2_BAUD = 921` (921600)

### Tests

- [x] Wire TX→RX, RX→TX, GND→GND between Jetson and Pixhawk TELEM2
- [x] Update `config.ini`: `connection_string = /dev/ttyTHS1`, `baud = 921600`
- [x] Confirm MAVLink heartbeat received over UART
  - Quick test: `mavproxy.py -master=/dev/ttyTHS1 -baudrate=921600`
  - Hydra test: run pipeline, check logs for `heartbeat` / `vehicle connected`
- [ ] Verify GPS data stream (2 Hz) - compare lat/lon with Mission Planner values
- [x] Test reconnection: unplug/replug UART cable → pipeline survived, alerts resumed
- [x] Update Docker run command: `--privileged` handles device access
- [x] Update `hydra-detect.service` if using systemd — already uses `--privileged`

### Pass Criteria
- Heartbeat received within 10 seconds of connection
- GPS coordinates match Mission Planner within ~1m
- Reconnection completes within 30 seconds after cable replug
- No crash or data corruption during disconnect

--

## 2. Camera & Video Setup

**Goal:** USB webcam for Hydra detection, HDZero for FPV + OSD overlay.

### Why Two Cameras?

The HDZero Freestyle V2 + Nano 90 is a **fully digital** video system. The
Nano 90 connects to the VTX via a proprietary digital link - there is no analog
CVBS output pad on the Freestyle V2 to tap into. The firmware source
([hd-zero/hdzero-vtx](https://github.com/hd-zero/hdzero-vtx)) confirms the
Freestyle V2 does not include the TP9950 analog video decoder chip that some
other HDZero VTXs use for legacy analog camera input.

This means the HDZero feed **cannot be routed into the Jetson on-vehicle**.
Instead, each camera serves a different role:

| Camera | Purpose | Connection |
|----|-----|------|
| **USB webcam** (C270/C920) | Hydra detection source (Jetson processes this) | USB → Jetson |
| **HDZero Nano 90** | Pilot FPV view + OSD overlay in goggles | Digital → Freestyle V2 VTX → Goggles/Monitor |

The two cameras can be pointed independently - the webcam aims at the detection
area while the Nano 90 gives the pilot a flight/navigation view.

> **Future option:** An analog FPV system would make it easier to share one
> camera for both detection and FPV (analog CVBS can be split to a USB capture
> dongle). But analog FPV loses HDZero's digital quality and MSP OSD capability.

### Hardware

- **Detection camera:** USB webcam (Logitech C270 or C920)
- **FPV camera:** HDZero Nano 90 → Freestyle V2 VTX
- **FPV receivers:** HDZero Monitor, Goggles 1, Goggles 2
- **OSD path:** Jetson → MAVLink → Pixhawk 6C → MSP DisplayPort → Freestyle V2 VTX → Goggles

### Tests - USB Webcam (Detection Source)

- [ ] Plug USB webcam into Jetson
- [ ] Verify auto-detect: `source = auto` in config.ini → check logs for device name
- [ ] Verify live video on web dashboard (`http://<jetson-ip>:8080`)
- [ ] Run detection pipeline - verify YOLO detects at configured resolution
- [ ] If both webcam and other V4L2 devices present, confirm auto-detect picks webcam
- [ ] Test camera disconnect/reconnect - Hydra should reconnect with backoff, not crash
- [ ] Check web API: `curl http://<jetson-ip>:8080/api/camera/sources`
  - Webcam should appear with `"type": "webcam"`

### Tests - HDZero FPV + OSD

See [Section 7: OSD Overlay Testing](#7-osd-overlay-testing-fpv-goggles) for
full OSD test plan. Quick validation:

- [ ] Verify Nano 90 + Freestyle V2 video appears in goggles/monitor
- [ ] Wire Pixhawk UART TX → Freestyle V2 VTX RX pad (MSP)
- [ ] Set FC params: `OSD_TYPE=3`, `SERIALn_PROTOCOL=33`, `SERIALn_BAUD=115`
- [ ] Set Hydra config: `[osd] enabled = true`, `mode = statustext`
- [ ] Trigger a detection → verify OSD text appears in goggles
- [ ] Test with HDZero powered off - Hydra should continue detecting, just no OSD

### Pass Criteria
- USB webcam auto-detected and producing detections at ≥5 FPS
- HDZero FPV feed visible in goggles with OSD overlay
- Camera disconnect handled gracefully (no crash, auto-reconnect)
- Both systems run simultaneously without resource contention

--

## 3. QGroundControl on Steam Deck

**Goal:** Use Steam Deck as portable GCS via QGC instead of Mission Planner.

### Tests

- [ ] Install QGroundControl on Steam Deck (AppImage or Flatpak)
- [ ] Connect QGC to Pixhawk via RFD 900x radio
  - Plug ground RFD 900x radio into Steam Deck USB
  - QGC should auto-detect serial at 57600 baud
- [ ] Verify QGC receives telemetry: attitude, GPS position, battery voltage
- [ ] Test WiFi UDP as alternative link:
  - On Jetson: `mavproxy.py -master=/dev/ttyTHS1 -baudrate=921600 -out udp:<steamdeck-ip>:14550`
  - Or configure ArduPilot to output on both TELEM1 and TELEM2
- [ ] Map Steam Deck gamepad buttons in QGC (mode switch, arm/disarm)

### Pass Criteria
- QGC displays live telemetry (attitude indicator, GPS, battery)
- Mode changes from QGC are reflected on vehicle
- Gamepad controls responsive and correctly mapped

--

## 4. Alerts & Status Messages via QGC

**Goal:** Understand how Hydra alerts display in QGC vs Mission Planner.

### Key Differences: QGC vs Mission Planner

| Feature | Mission Planner | QGroundControl |
|-----|--------|--------|
| STATUSTEXT display | Messages tab (persistent log) | Toast notification bar (transient) |
| Message persistence | Scrollable history | Disappears after timeout |
| Custom MAV_CMD | Actions tab, easy to send | MAVLink Inspector or custom buttons |
| NAMED_VALUE display | MAVLink Inspector | MAVLink Inspector only |
| OSD integration | N/A (MP is desktop) | N/A (QGC is GCS, not OSD) |

### Tests

- [ ] Trigger Hydra detection → observe STATUSTEXT in QGC notification bar
- [ ] Document where alerts appear and how long they persist
- [ ] Test different severity levels (config.ini `severity` 0-7):
  - 0 = EMERGENCY, 2 = WARNING, 6 = INFO - note which QGC shows/hides
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

--

## 5. SDR / RF Exploration

**Goal:** Get SDR working with Kismet for RF hunt integration.

### Setup (verified 2026-03-17 on Jetson Orin Nano, JetPack 6.2.1)

```bash
# 1. Install RTL-SDR drivers
sudo apt-get install -y rtl-sdr

# 2. Add udev rules so non-root users can access the dongle
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/20-rtlsdr.rules
sudo udevadm control --reload-rules && sudo udevadm trigger

# 3. Install rtl_433 (required by Kismet's RTL-SDR capture helper)
sudo apt-get install -y rtl-433

# 4. Install Kismet from official repo
wget -O - https://www.kismetwireless.net/repos/kismet-release.gpg.key --quiet \
  | gpg --dearmor | sudo tee /usr/share/keyrings/kismet-archive-keyring.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/kismet-archive-keyring.gpg arch=arm64] \
  https://www.kismetwireless.net/repos/apt/release/jammy jammy main" \
  | sudo tee /etc/apt/sources.list.d/kismet.list
sudo DEBIAN_FRONTEND=noninteractive apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y kismet

# 5. Set Kismet credentials (must match config.ini [rf_homing] kismet_user/pass)
sudo mkdir -p /root/.kismet
echo -e "httpd_username=kismet\nhttpd_password=kismet" | sudo tee /root/.kismet/kismet_httpd.conf
# Also set in site config so it takes priority:
echo -e "httpd_username=kismet\nhttpd_password=kismet" | sudo tee /etc/kismet/kismet_site.conf

# 6. Start Kismet with RTL-433 source (use rtl433-0, NOT rtlsdr-0)
sudo kismet -c rtl433-0 --no-ncurses --daemonize
```

**Known issues:**
- Kismet install prompts for suid-root helpers interactively. Use `DEBIAN_FRONTEND=noninteractive` or pre-set debconf answers.
- Source name must be `rtl433-0` (not `rtlsdr-0`) — Kismet's RTL-SDR support works via the rtl_433 capture helper.
- **Kismet 2025 auth:** Uses cookie-based sessions, NOT HTTP Basic Auth on every request. Hydra's `kismet_client.py` handles this automatically (establishes session via `/session/check_session`, then uses cookies). Older Kismet versions still work via basic auth fallback.
- The automated setup path currently targets RTL-SDR hardware; HackRF needs separate manual Kismet setup.

### Tests

- [x] Verify SDR device on Jetson: `rtl_test` (NooElec NESDR Smart v5, R820T tuner)
- [ ] USB passthrough into Docker container: `--privileged` handles this
- [x] Install Kismet on Jetson (REST API on `localhost:2501`)
- [x] Run Kismet with SDR source — `rtl433-0` source running, 0 packets (no transmitters in range)
- [ ] Configure Hydra `[rf_homing]` section in config.ini with Kismet endpoint
- [x] Test RSSI data feed from Kismet into Hydra RF hunt module — `KismetClient.check_connection()` PASS
- [ ] (Stretch) Try `dump1090` for ADS-B reception — `kismet-capture-rtladsb-v2` is installed
- [ ] (Stretch) Spectrum survey of test area with gqrx or SigDigger

### Pass Criteria
- SDR device recognized and functional on Jetson
- Kismet receives data from SDR and serves REST API
- Hydra RF hunt module reads RSSI values from Kismet

--

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
  - `http://<jetson-ip>:8080` - verify MJPEG stream works
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

--

## 7. OSD Overlay Testing (FPV Goggles)

**Goal:** Verify Hydra detection data appears in FPV goggles via FC OSD.

See `docs/hdzero-osd-setup.md` for full wiring details.

### STATUSTEXT Mode (simplest, default)

- [ ] Set `[osd] mode = statustext` in config.ini
- [ ] Verify text appears in OSD message panel on goggles
- [ ] Works with Pixhawk 6C via MSP DisplayPort (no MAX7456 chip needed) -
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

--

## 8. Autonomous Strike Safety Validation

**Goal:** Verify all autonomous strike safeguards work on real hardware.

This is the most dangerous feature - every criterion must be tested independently.

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

--

## 9. Detection Logging & Review

**Goal:** Verify detection logs are complete and exportable for post-mission review.

### Log Format

- [ ] JSONL mode: each line is valid JSON (`python -m json.tool`)
- [ ] CSV mode: headers present, consistent column count
- [ ] Logs saved to `log_dir` with timestamped filenames

### Image Snapshots

- [ ] `save_images = true` - full-frame JPEG at configured quality
- [ ] Inspect file size and quality (default 90%)
- [ ] Verify bounding boxes overlaid on saved images
- [ ] `save_crops = true` - cropped object images saved separately
  - [ ] Verify crop dimensions match track bounding box
  - [ ] Test crop for objects near frame edges (no overflow)

### GPS Geo-tagging

- [ ] Detection logs include `lat`, `lon`, `alt` from MAVLink GPS
- [ ] Test with no GPS fix - should log null/NaN, not crash

### Review Export

- [ ] `python -m hydra_detect.review_export /path/to/detections.jsonl -o report.html`
- [ ] Verify standalone HTML works offline
- [ ] Test with large log files (10000+ detections)

### Pass Criteria
- All log formats parseable and complete
- Images readable and correctly annotated
- GPS coordinates present when fix is available

--

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

- [ ] Test yolov8n (fastest, ~6MB) - baseline FPS
- [ ] Test yolov8s (balanced) - FPS delta
- [ ] Test yolov8m (heavier) - FPS delta, memory impact
- [ ] Document model-size vs FPS vs temperature tradeoffs

### Pass Criteria
- FPS ≥5 sustained in MAXN mode with yolov8n
- Temperature stays below 85°C (or degrades gracefully)
- No OOM with any tested model on 8GB Jetson

--

## 11. Web API & Dashboard Under Load

**Goal:** Verify web interface doesn't degrade detection performance.

### MJPEG Stream

- [ ] Open `/stream.mjpeg` in browser during detection
- [ ] Measure stream latency (object in frame → visible on dashboard)
- [ ] Test from multiple clients simultaneously (Steam Deck + laptop)
- [ ] Verify no dropped frames under load

### API Endpoints

- [ ] `/api/stats` - data updates in near real-time
- [ ] `/api/camera/sources` - lists available video devices correctly
- [ ] `/api/review/logs` - lists all detection log files
- [ ] `/api/review/log/{filename}` - parses JSONL and CSV correctly

### Security

- [ ] If `api_token` is set, mutation endpoints reject unauthenticated requests
- [ ] Read-only endpoints (`/api/stats`, `/stream.mjpeg`) work without auth
- [ ] Path traversal protection on log file endpoints

### Pass Criteria
- Dashboard + API access doesn't drop detection FPS below 5
- All endpoints return correct data
- Auth enforced on control endpoints when token is set

--

## 12. Preflight & Docker Validation

**Goal:** Verify deployment tooling works before field testing.

- [ ] Run `bash scripts/jetson_preflight.sh` - all checks PASS (0 FAILs)
  - Python, pip, NVIDIA utilities, OpenCV, FastAPI
  - Camera device, serial device, dialout group
  - config.ini present, model files exist
- [ ] Verify Docker device passthrough works for all devices simultaneously:
  - `-device /dev/video0` (camera)
  - `-device /dev/ttyTHS1` (UART to Pixhawk)
  - `-device /dev/bus/usb` (SDR if enabled)
- [ ] Test `sudo systemctl restart hydra-detect` - port 8080 available within 5s
- [ ] Verify fail-safe defaults in config.ini:
  - `[autonomous] enabled = false`
  - `[osd] enabled = false`
  - `[rf_homing] enabled = false`

--

## Notes & Observations

_Use this section to record findings during testing._

| Date | Test | Result | Notes |
|---|---|----|----|
| 2026-03-17 | UART heartbeat (ttyTHS1, 921600) | PASS | Heartbeat in <2s. Initial wiring was straight-through — must cross TX/RX. |
| 2026-03-17 | Hydra pipeline over UART | PASS | Full pipeline running: detection alerts sent to Pixhawk over TELEM2 UART. |
| 2026-03-17 | GPS over UART | N/A | Returns 0,0 indoors (no fix). Needs outdoor test. |
| 2026-03-17 | UART reconnection | PASS | Pulled GND wire ~5s, replugged. No crash, alerts resumed. |
