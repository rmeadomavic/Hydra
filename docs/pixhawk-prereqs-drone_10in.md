# Pixhawk Prerequisites: 10-inch Quadcopter (ArduCopter)

Platform: 10" FPV quadcopter running ArduCopter on Pixhawk 6C.

Run `python scripts/pixhawk_preflight.py --profile drone_10in --conn /dev/ttyACM0` to
validate these params live against your flight controller.

---

## Required Parameters

These must be set correctly before running Hydra. A 10-inch airframe operating near personnel with wrong params is a serious hazard.

| Parameter | Expected | Why |
|---|---|---|
| `FENCE_ENABLE` | `1` | Geofence required for any autonomous behavior (GUIDED mode approach, drop, strike). Without it the copter has no automatic altitude or radius boundary during Hydra-commanded sorties. |
| `SERIAL2_PROTOCOL` | `2` | Companion computer port (TELEM2) must be MAVLink2 for Hydra connectivity. |
| `SERIAL2_BAUD` | `921` | 921600 baud. Required for reliable heartbeat + telemetry + command throughput at CULEX tempo (multiple sorties, rapid turnaround). |
| `ARMING_CHECK` | `1` | All arming checks enabled. Disabling checks (value 0) allows arming without GPS fix, healthy EKF, or RC calibration. Unacceptable near personnel. To bypass a specific check (e.g., GPS unavailable indoors), use the bitmask to disable that bit only. |

---

## Recommended Parameters

| Parameter | Recommended | Why |
|---|---|---|
| `FS_GCS_ENABLE` | `1` | GCS heartbeat failsafe enabled. If Hydra's MAVLink connection drops mid-sortie the copter should RTL or land rather than continue flying autonomously. Value 1 = enabled. |
| `BATT_FS_LOW_ACT` | `2` | RTL on low battery. Value 2 = RTL. Without this, a copter with a failing battery flies until motors stop. |
| `FENCE_ACTION` | `1` | RTL when altitude or radius fence is breached. Value 0 = report only, which is not appropriate for operations near people. |
| `FENCE_ALT_MAX` | `120` | **TODO: verify against your NOA/LAANC authorization.** 120m (400ft AGL) is the FAA Part 107 default ceiling. Adjust to your actual airspace authorization. |

---

## Stream Rates

Minimum rates required on the companion port (SERIAL2). ArduCopter requires higher
update rates than Rover for stable GUIDED mode tracking.

| Parameter | Minimum Hz | Why |
|---|---|---|
| `SR1_POSITION` | `5` | GPS position for TAK markers, geo-tracking, and GUIDED mode waypoints. |
| `SR1_EXTRA1` | `4` | Attitude/heading. Required for OSD orientation and approach mode pixel-lock. |
| `SR1_EXTRA2` | `2` | Battery voltage. Required for battery warnings and failsafe monitoring. |
| `SR1_RAW_SENS` | `2` | IMU data. Required if RF hunt mode is active. |

---

## GUIDED Mode: Flight Mode Channel

Hydra's approach modes (follow, drop, strike, pixel-lock) all require the copter to be
in GUIDED mode. ArduCopter uses `FLTMODE_CH` (default: channel 5) for mode switching via RC.

**Verify before each sortie:**
1. GUIDED is mapped to at least one switch position on your RC transmitter.
2. The switch is reachable with one hand while operating the sticks.
3. The instructor can override to LOITER or LAND from their transmitter.

Hydra does not validate the flight mode map in the preflight. Check manually in
Mission Planner (Config > Flight Modes) or QGroundControl.

---

## Failsafe Expectations

- **RC Loss:** `FS_THR_ENABLE = 1`. Copter should RTL or land on RC signal loss, not loiter indefinitely.
- **GCS Loss:** `FS_GCS_ENABLE = 1` (see recommended). Fires when the Jetson or WiFi drops. Configure `FS_GCS_TIMEOUT` (default 5s). Do not set above 10s for field ops.
- **Battery Low:** `BATT_FS_LOW_ACT = 2` (RTL). Configure `BATT_LOW_VOLT` to your battery's safe minimum. For 6S LiPo: typically 21.6V (3.6V/cell).
- **Battery Critical:** `BATT_FS_CRT_ACT = 1` (Land immediately). At critical voltage, RTL may not complete before battery failure. Land in place is safer.
- **EKF Failsafe:** `FS_EKF_ACTION = 2` (Land). EKF failures in flight are a serious condition; landing in place is safer than trying to navigate.
- **Geofence:** `FENCE_ACTION = 1` (RTL). `FENCE_RADIUS` and `FENCE_ALT_MAX` must match your operating area. Confirm before each sortie.

---

## Arming Checks

`ARMING_CHECK = 1` enables all checks. When a check blocks arming, ArduCopter sends a
STATUSTEXT explaining which check failed. Common field-blocking checks and their
bitmask values to temporarily disable (use sparingly, restore after):

| Check | Bitmask bit | Notes |
|---|---|---|
| GPS lock | bit 3 (value 8) | Indoor ops only. Restore before outdoor sorties. |
| Compass | bit 4 (value 16) | Only if using GPS-based heading fallback. |
| RC calibration | bit 6 (value 64) | Only if RC is via MAVLink RC override (companion-only ops). |

To disable only GPS check: compute the bitmask in Mission Planner's arming check GUI rather than manually.

**Do not set `ARMING_CHECK = 0` on a platform operating near students.**

---

## Servo / Relay Assignments

Engagement actions (drop, arm) are operator-configured at mission time. Hydra reads
these from `config.ini [drop]`. The preflight does not check servo assignments because
they only apply during armed operation and vary by loadout.

```
# Example SORCC 10-inch engagement setup (not validated by preflight)
SERVO9_FUNCTION = 0    # GPIO — Hydra relay (payload drop)
RELAY_PIN       = 13   # AUX1 on Pixhawk 6C
```

Set `[drop] relay_pin` in `config.ini` to match.

---

## Notes

- `SERIAL2_BAUD = 921` encodes 921600 baud in ArduPilot's compressed format.
- `ARMING_CHECK = 1` is the integer value for "all checks enabled." The full bitmask is
  `0xFFFF` but ArduPilot treats `1` as "default all enabled" in practice. Verify in
  Mission Planner that the arming check screen shows all items checked.
- `FENCE_ALT_MAX` defaults to 100m in ArduCopter. The 120m recommendation aligns with
  FAA Part 107 but requires appropriate airspace authorization. This is a TODO, not a
  hard requirement. Do not fly above your authorized ceiling.
- The 5" drone profile (`drone_5in`) is not currently implemented. The 10-inch manifest
  covers the SORCC primary quadcopter platform. If the 5" uses different params, create
  a separate profile.
