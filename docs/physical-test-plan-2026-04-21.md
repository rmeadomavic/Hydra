# Physical Test Plan — 2026-04-21

Focused task list for the next Jetson session. Ordered to minimize
cable churn: each block is self-contained and can be stopped between.

Inputs folded in:
- Open items from `docs/jetson-hardware-testing-checklist.md`
- Phase-2 gaps from `docs/week4-test-plan.md`
- New verification needed after PR #139 preflight work + PR #140 UI
- Servo plan from 2026-04-21 chat (techid-day pan demo)

Items marked **(known PASS)** were confirmed 2026-03-17 per the hardware
checklist notes table — skip unless regression-hunting.

--

## Block A — Bench bring-up (Jetson + laptop only, ~20 min)

Just power. No Pixhawk, no props. Confirms the base stack is healthy
before adding anything else.

- [ ] Cold boot Jetson → dashboard reachable on laptop browser within 30s
- [ ] `bash scripts/jetson_preflight.sh` — 0 FAILs (exercises the PR #139
      additions: TTY detection, fail-safe defaults, disk, Kismet probe)
- [ ] USB webcam (C270 or C920) auto-detected; live feed on `/`
- [ ] `curl -s http://localhost:8080/api/stats | jq '.fps'` ≥ 5
- [ ] `curl -s http://localhost:8080/api/health` → 200
- [ ] Walk through the 4-tab dashboard (Ops | TAK | Config | Settings)
      — nothing red, nothing empty that shouldn't be

**PR #140 verification (designator-line fade):**
- [ ] Point camera at a person. Context-menu a non-locked track →
      dim dashed range lines appear from bbox to frame edges; vanish
      when menu closes
- [ ] Context-menu → Lock → lines **fade in over ~220ms** (not a pop),
      peak alpha reads clearly against the video without looking costume-y
- [ ] Lock a second track → fade restarts on the new target
- [ ] Unlock → lines clear cleanly

--

## Block B — Pixhawk over UART (+ Steam Deck QGC, ~30 min)

Wire up TELEM2 per `jetson-hardware-testing-checklist.md` §1. UART
wiring and heartbeat were **(known PASS)** 2026-03-17; this block
closes the items that were still open.

- [ ] **GPS outdoor fix** — was N/A indoors on 2026-03-17. Take it outside,
      confirm `/api/stats.gps.fix_type >= 3` and lat/lon match Mission
      Planner within ~1 m
- [ ] Steam Deck QGC via RFD 900x → telemetry visible (attitude, GPS, batt)
- [ ] STATUSTEXT alert from a Hydra detection lands in QGC notification
      bar within ~2 s
- [ ] WiFi UDP link as backup: `mavproxy.py -master=/dev/ttyTHS1 -baudrate=921600 -out udp:<steamdeck-ip>:14550`
- [ ] Lock/unlock from QGC MAVLink Inspector via `MAV_CMD_USER_1` / `USER_3`
      — Hydra logs the command, target state updates on the dashboard

--

## Block C — Pan servo for techid demo (~20 min, add after B)

This is the new servo work from this session. See config.ini:107,153
and CLAUDE.md "Approach mode safety invariant" before wiring.

**Pixhawk params** (Mission Planner or QGC full param tree):
- [ ] `SERVO10_FUNCTION = 1` (RCPassThru) or `0` (disabled). **Not** a
      flight function.
- [ ] `BRD_PWM_COUNT` covers channel 10 (set to ≥ 10 if not already)

**Servo wiring:**
- [ ] Servo signal → Pixhawk AUX 10. **BEC power**, not FC rail
      (unless it's a tiny 9g)
- [ ] Common GND between servo supply and Pixhawk

**Hydra config:**
- [ ] `config.ini` → `[servo_tracking] enabled = true`
- [ ] Tune `pan_pwm_center` / `pan_pwm_range` for your gimbal's
      mechanical range (start conservative, 1500 ± 300, widen if stiff)
- [ ] Verify `pan_channel = 10` doesn't collide with the vehicle's
      reserved channels — pipeline will auto-disable and log if it does

**Functional tests:**
- [ ] Pipeline boots with servo_tracking on, no errors in
      `curl -s 'http://localhost:8080/api/logs?lines=50&level=WARNING'`
- [ ] At idle (no lock, no track), servo sits at `pan_pwm_center`
- [ ] Lock a target that moves horizontally in-frame → servo pans to
      keep bbox centroid near frame center (dead zone `pan_dead_zone = 0.05`
      is fine to leave at default)
- [ ] `pan_invert = true` flips direction — test and pick the sign that
      matches your rig
- [ ] Sudden large lateral motion doesn't overshoot / oscillate — if it
      does, raise `pan_smoothing` (currently 0.3)
- [ ] Unlock → servo returns to center smoothly
- [ ] `/api/abort` → servo safe state (driven by atexit handler, commit
      `4ed1fe0`) → verify the `SERVO_OUTPUT_RAW` for channel 10 returns
      to center in Mission Planner's Status tab

**Demo read:** Does it *look* like a targeting payload from 3 m away?
If the servo hunts visibly even when the target is still, bump
`pan_dead_zone` up a touch.

--

## Block D — Safety interlocks (~20 min, no vehicle motion)

Re-verifies what the 2026-04-20 safety review audited in code, on real
hardware. None of this needs props moving.

- [ ] `POST /api/abort` with **no auth header** returns 200 (public path,
      must never block the instructor)
- [ ] `POST /api/approach/strike/<id>` **without** `confirm=true` → 400
- [ ] `POST /api/approach/drop/<id>` **without** `confirm=true` → 400
- [ ] Strike with SW arm not set (no `arm_channel` in config) → blocked,
      audit log line present in `hydra.audit`
- [ ] Strike with SW arm set but HW arm RC channel low → blocked
      (see approach.py:469-476)
- [ ] TAK GeoChat with no HMAC (when `[tak] hmac_secret` is set) →
      rejected, `TAK_CMD_REJECTED reason=hmac_missing` in audit log
- [ ] Autonomy mode toggle: `POST /api/autonomy/mode {"mode":"dryrun"}`
      → SIM pill visible on dashboard, no live commands issued

--

## Block E — Peripherals (pick what your demo needs, ~30-60 min)

Only do what you'll actually show. Each is independent.

### E.1 FPV OSD (if running HDZero in the demo)
- [ ] Wire Pixhawk SERIAL5/TELEM3 TX → Freestyle V2 VTX RX pad (MSP)
- [ ] FC params: `OSD_TYPE=3`, `SERIALn_PROTOCOL=42` (HDZero MSP,
      **not 33**), `SERIALn_BAUD=115`
- [ ] `[osd] enabled = true`, `mode = statustext`
- [ ] Trigger a detection → text in goggles within ~200 ms
- [ ] Power-cycle HDZero → Hydra keeps detecting, no crash

### E.2 TAK on ATAK
- [ ] `[tak] enabled = true`, takserver reachable from Jetson
- [ ] Open ATAK on Steam Deck or phone → Hydra's self-marker appears
      with configured callsign
- [ ] Detection markers appear at correct GPS (needs GPS fix from Block B)
- [ ] GeoChat command from ATAK → dashboard reflects action; audit log
      captures sender + command

### E.3 RF hunt (skip unless demoing RF)
- [ ] `rtl_test -t` — dongle enumerates, no USB errors
- [ ] `systemctl status kismet` (warn-only item from PR #139 preflight)
- [ ] `[rf_homing] enabled = true`, dashboard RF tile shows a state
      other than `unavailable`
- [ ] Place a 433 MHz transmitter (or any RTL-433 device) in range →
      Kismet packet count climbs, Hydra RSSI tile updates

--

## Block F — Endurance + thermal (~30 min, runs while you do other prep)

Backgrounded. Kick it off and come back.

- [ ] `tegrastats --interval 5000 > /tmp/tegrastats.log &` — leave
      running 30 min under full pipeline load
- [ ] FPS stays ≥ 5 the whole time
- [ ] GPU temp peaks below 85 °C (throttle ~80 °C on Orin Nano)
- [ ] Shared RAM usage doesn't grow unbounded (bounded collections invariant)
- [ ] At end: `curl http://localhost:8080/api/logs?lines=200&level=ERROR`
      returns nothing new
- [ ] Log rotation: check `output_data/logs/hydra.log` is ≤ 5 MB and
      rotated backups exist if you've been running a while

--

## Demo-day dry run (~15 min, do this last)

Full show, end to end, as if the audience is watching. Time it.

- [ ] Power off, power on → first detection visible in ≤ 45 s
- [ ] Point at a person → track picks up, class chip reads `PERSON 87%`
- [ ] Right-click track → dim designator lines appear (Block A verify)
- [ ] Click Lock → lines fade in, reticle breathes (Block A verify)
- [ ] Servo pans to follow target as you walk (Block C verify)
- [ ] Hit `/api/abort` from a shortcut → servo centers, mode reverts
- [ ] TAK marker on ATAK shows target location (if E.2 is in demo)
- [ ] Power off cleanly (systemctl stop, then cut power) → next boot
      is clean, config.ini intact

--

## Known-skip (already passing)

Don't re-run unless you suspect regression:
- UART wiring + heartbeat, pipeline-over-UART, UART reconnect (2026-03-17)
- RTL-SDR enumerates, Kismet installed + `rtl433-0` source runs (2026-03-17)
- Kismet REST API + `KismetClient.check_connection()` (2026-03-17)

## Deferred / out of scope for this session

- CoT clock-skew validation (pre-existing gap from 2026-04-20 safety review)
- Autonomy inhibit REST endpoint (property setter exists, no POST yet)
- HackRF Kismet setup (automation targets RTL-SDR only)
- Multi-vehicle stress test (Phase 3 of week4-test-plan — needs 2+ Jetsons)

--

## Abort criteria

Stop and debug if any of these hit:
- FPS < 5 at any point during Block A or F
- `/api/abort` ever returns non-200
- Servo oscillates >1 Hz at rest with no moving target
- Thermal throttle triggers before 20 min (bad airflow / power mode)
- Any autonomy action fires in dryrun mode
