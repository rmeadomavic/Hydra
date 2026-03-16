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

**Goal:** Use HDZero FPV link as camera source for detection pipeline.

### Tests

- [ ] Identify HDZero VRX output type:
  - Analog CVBS → USB UVC capture dongle needed
  - HDMI out → HDMI-to-USB capture card needed
- [ ] Connect VRX output to Jetson via capture device
- [ ] Verify device appears: `v4l2-ctl --list-devices` → note `/dev/videoN` index
- [ ] Set `source = <device_index>` in `config.ini`
- [ ] Verify live video on web dashboard (`http://<jetson-ip>:8080`)
- [ ] Measure capture latency (point camera at stopwatch, compare with dashboard)
- [ ] Run detection pipeline — verify YOLO detects at HDZero resolution/framerate
- [ ] Note FPS with HDZero vs USB webcam (benchmark comparison)

### Pass Criteria
- Live video displays on web dashboard with no corruption
- Detection pipeline sustains ≥5 FPS
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

## Notes & Observations

_Use this section to record findings during testing._

| Date | Test | Result | Notes |
|------|------|--------|-------|
|      |      |        |       |
