# Hydra Week 4 Field Validation Test Plan

**Timeline:** Week 3 starts March 31. Operators on Hydra Week 4 (~April 7).
**Window:** 9 days for maintainer validation before field use.

## Phase 1 — Bench Test (March 31 - April 2)

Solo, indoors. Validate every operator-facing feature works before going to the field.

### 1.1 Boot and Auto-Start
- [ ] Power on Jetson cold — Hydra starts automatically via systemd/Docker
- [ ] Dashboard accessible from laptop browser within 30 seconds of boot
- [ ] Dashboard accessible from phone browser (mobile layout)
- [ ] Pre-flight checklist shows green for all subsystems
- [ ] Callsign visible in dashboard title bar

### 1.2 Camera
- [ ] USB camera auto-detected on boot (Logitech C270 and C920)
- [ ] MJPEG stream visible on dashboard at stable FPS
- [ ] Unplug camera mid-stream — "VIDEO LOST" overlay appears within 2 seconds
- [ ] STATUSTEXT: `HYDRA: CAM LOST` sent to Mission Planner
- [ ] Replug camera — stream recovers, "CAM RESTORED" sent
- [ ] Pre-flight checklist shows camera fail when unplugged at boot

### 1.3 MAVLink Connection
- [ ] Pixhawk detected on correct serial port (/dev/ttyTHS1 for UART)
- [ ] GPS position displayed on dashboard
- [ ] Vehicle mode displayed on dashboard
- [ ] STATUSTEXT messages appear in Mission Planner
- [ ] Pre-flight checklist shows MAVLink fail when disconnected
- [ ] Degraded mode works (detection only, no MAVLink warnings)

### 1.4 Detection
- [ ] YOLO model loads from models/ directory
- [ ] Detections appear as bounding boxes on MJPEG stream
- [ ] Track IDs assigned and persist across frames
- [ ] Detection class and confidence visible on overlay
- [ ] Model dropdown shows all .pt files in models/ folder
- [ ] Model hot-swap from dropdown works without restart

### 1.5 Dashboard Controls
- [ ] Lock track — track highlighted, MAVLink command sent
- [ ] Unlock — lock released
- [ ] Settings page: all tunables editable
- [ ] Settings save persists across restart
- [ ] Restart-required settings clearly marked
- [ ] Restart button works (if implemented)

### 1.6 Config Validation
- [ ] Intentional bad value (yolo_confidence=90) — clear error on pre-flight
- [ ] Wrong serial port — clear error, degraded mode
- [ ] Missing model file — clear error, pipeline blocks
- [ ] Unknown config key (typo) — warning logged

### 1.7 Safety
- [ ] Servo channel validation: configure strike_channel=1 on USV profile — startup blocks with error
- [ ] Config.ini corrupted (truncate file) — falls back to backup with warning
- [ ] Factory reset restores known-good config (if implemented)

### 1.8 Logging
- [ ] Detection JSONL written with chain-of-custody hashes
- [ ] verify_log.py passes on clean log
- [ ] Annotated frame images saved
- [ ] Log export from dashboard (if implemented)

## Phase 2 — Single Vehicle Field Test (April 2-4)

**Platform:** USV (Enforcer) on Broadacres Lake. Stable platform, recoverable, low risk.
**Personnel:** Kyle + one assistant for safety boat.

### 2.1 Basic Operations
- [ ] Boot Jetson on USV battery power
- [ ] Dashboard accessible from shore laptop over WiFi
- [ ] Real camera produces detections on water (kayak target, person on shore)
- [ ] TAK markers appear on ATAK with correct GPS positions
- [ ] TAK callsign matches config (e.g., HYDRA-1-USV)
- [ ] STATUSTEXT alerts appear in Mission Planner with callsign prefix

### 2.2 Camera Resilience
- [ ] Pull USB camera cable during operation — CAM LOST alert fires
- [ ] Reconnect — stream recovers automatically
- [ ] Vibration from motor doesn't trigger false camera loss

### 2.3 WiFi Range
- [ ] Walk away from USV — note distance where dashboard video stales
- [ ] Stale video overlay appears (if implemented)
- [ ] Control API (lock/unlock) still works when video is degraded
- [ ] Walk back — video recovers

### 2.4 Autonomous Basics
- [ ] Set geofence around test area on lake
- [ ] Verify geofence blocks actions outside boundary (use sim GPS)
- [ ] Lock a target track from dashboard
- [ ] Follow mode (if implemented): USV tracks kayak target, maintains distance
- [ ] Abort button stops all autonomous action, USV enters LOITER

### 2.5 Mission Lifecycle
- [ ] Start Mission button on dashboard
- [ ] Run a detection sortie (5 minutes)
- [ ] End Mission button
- [ ] Review detection logs — events bracketed by mission start/end
- [ ] Export logs from dashboard (if implemented)

### 2.6 Power and Shutdown
- [ ] Note battery voltage over time (MAVLink battery display if implemented)
- [ ] Graceful shutdown via dashboard or systemctl — servo safe state confirmed
- [ ] Hard power cut (disconnect battery) — verify Jetson recovers on next boot
- [ ] Config.ini intact after power cut (atomic writes if implemented)
- [ ] Detection logs intact (verify_log.py tolerates truncated final record)

## Phase 3 — Multi-Vehicle Stress Test (April 4-6)

**Platforms:** 2-3 Jetsons simultaneously (USV + UGV, or USV + UGV + drone)
**Personnel:** All 3 maintainers.

### 3.1 Multi-Instance Identity
- [ ] Each Jetson has unique callsign (HYDRA-1-USV, HYDRA-2-UGV, etc.)
- [ ] TAK markers show correct callsigns on ATAK map
- [ ] STATUSTEXT in Mission Planner shows which vehicle sent each alert
- [ ] Detection logs in separate directories per callsign

### 3.2 TAK Command Isolation
- [ ] Send `HYDRA-1-USV LOCK 3` from ATAK — only USV responds
- [ ] UGV ignores command meant for USV
- [ ] Group command `HYDRA-ALL UNLOCK` (if implemented) — all vehicles respond
- [ ] Duplicate callsign warning if two Jetsons have same callsign

### 3.3 Fleet View (if implemented)
- [ ] /fleet page shows all active Jetsons
- [ ] Status cards update in real-time
- [ ] Abort button on Fleet View stops specific vehicle
- [ ] Works from phone over mesh/Tailscale

### 3.4 Network
- [ ] WiFi: all Jetsons accessible from same AP
- [ ] Tailscale: all Jetsons reachable via stable IPs
- [ ] Dashboard from phone: responsive mobile layout
- [ ] Simultaneous model swap on two vehicles (no conflict)

### 3.5 Platform-Specific
- [ ] UGV (Stampede): Follow mode with person target (if implemented)
  - Operator walks, truck follows
  - Verify speed control (slow when close, faster when far)
  - Verify abort stops vehicle
- [ ] USV (Enforcer): Follow mode with kayak target (if implemented)
  - Verify SmartRTL retraces path on dogleg lake shore
- [ ] Drone (if available): Detection during hover and orbit
  - Verify TAK markers have correct GPS

## Pre-Deployment Checklist (April 6)

Before Week 4 begins, confirm:
- [ ] All Jetsons auto-boot into operational dashboard
- [ ] All Jetsons have unique callsigns configured
- [ ] All Jetsons have correct vehicle profile (--vehicle flag or config)
- [ ] All Jetsons have required YOLO models in models/ folder
- [ ] All Jetsons have config.ini.factory for reset
- [ ] All Jetsons have correct serial port and camera config
- [ ] Pre-flight checklist passes on all Jetsons
- [ ] Mission Planner receives STATUSTEXT from all vehicles
- [ ] ATAK shows TAK markers from all vehicles
- [ ] At least one backup Jetson imaged and ready
- [ ] WiFi AP tested at expected operating range
- [ ] Tailscale VPN active on all Jetsons and operator devices

## Known Risks
- First field use of v2.0 — expect unexpected failures
- WiFi range may limit dashboard usefulness at distance
- USB camera reliability under vibration is untested
- Battery runtime under Hydra load is unknown — measure during Phase 2
- Operator error modes unknown until Week 4 — document everything that breaks

## Post-Test Debrief Template
For each phase, record:
1. What worked as expected
2. What failed and how it was fixed
3. What operators will likely break
4. What needs to change before next deployment
5. New issues to file
