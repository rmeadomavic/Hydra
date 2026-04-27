# Pixhawk Prerequisites: USV (ArduRover, Boat Mode)

Platform: Bonzai Enforcer 48" running ArduRover in boat frame mode on Pixhawk 6C.

Run `python scripts/pixhawk_preflight.py --profile usv --conn /dev/ttyACM0` to validate
these params live against your flight controller.

---

## Required Parameters

These must be set correctly before running Hydra. The USV profile is stricter than
the UGV profile because a boat on water has no brakes. Wrong params can result in an unrecoverable situation.

| Parameter | Expected | Why |
|---|---|---|
| `FRAME_CLASS` | `2` | Value 2 = Boat frame. ArduRover defaults to wheeled skid-steer (value 1). Without this, steering geometry is wrong and failsafe behavior is incorrect for a single-thruster + rudder platform. |
| `FENCE_ENABLE` | `1` | Geofence required for any autonomous behavior. Critical on water: a boat that exits the operating area cannot be physically stopped. |
| `SERIAL2_PROTOCOL` | `2` | Companion computer port (TELEM2) must be MAVLink2 for Hydra connectivity. |
| `SERIAL2_BAUD` | `921` | 921600 baud. The Enforcer's RF link is already a latency bottleneck. Don't add serial lag from a slow baud rate. |

---

## Recommended Parameters

| Parameter | Recommended | Why |
|---|---|---|
| `BATT_FS_LOW_ACT` | `2` | RTL on low battery. On water a dead boat drifts. Value 2 = RTL. Value 1 = Hold (stays in place, still dangerous). |
| `FENCE_ACTION` | `1` | RTL when fence is breached. Report-only (value 0) provides no recovery on water where there's no physical barrier to stop drift. |
| `FENCE_MARGIN` | `2` | 2-meter breach margin. Boats have more momentum than rovers; the margin gives the autopilot time to arrest heading before hard-land escalation. |
| `FS_GCS_ENABLE` | `2` | GCS heartbeat failsafe. Loss of Hydra MAVLink on water means the boat has no situational awareness. Should RTL. |
| `PILOT_STEER_TYPE` | `1` | Two-paddle steering (separate throttle + rudder channels). Ensures the RC pilot can override Hydra commands cleanly. Value 0 (skid steer) is incorrect for a single-thruster + rudder boat. |

---

## Stream Rates

Minimum rates required on the companion port (SERIAL2). Match the `SRx_*` index to your
actual companion port mapping (see UGV doc for indexing notes).

| Parameter | Minimum Hz | Why |
|---|---|---|
| `SR1_POSITION` | `5` | GPS position for TAK markers and geo-tracking. Below 5 Hz the map track lags. |
| `SR1_EXTRA1` | `4` | Attitude/heading. Required for OSD and approach mode orientation. |
| `SR1_EXTRA2` | `2` | Battery voltage. Required for battery warnings and failsafe monitoring. |
| `SR1_RAW_SENS` | `2` | IMU data. Required if RF hunt mode is active. |

---

## Failsafe Expectations

- **RC Loss:** Confirm `FS_THR_ENABLE = 1`. On water, losing RC with no failsafe means the boat runs free until the battery dies.
- **GCS Loss:** `FS_GCS_ENABLE = 2` triggers RTL after missed heartbeats. This fires if the Jetson or its WiFi drops.
- **Battery:** `BATT_FS_LOW_ACT = 2`. RTL on low battery. On water the boat cannot be walked back.
- **Geofence:** `FENCE_ACTION = 1` (RTL). Set `FENCE_RADIUS` to the radius of the operating waterway. `FENCE_ALT_MAX` is not applicable for surface vehicles but should be set to a safe value (e.g. 10m) to prevent false triggers from GPS altitude noise.

---

## Servo / Relay Assignments

Engagement actions (drop, arm) are operator-configured at mission time and not validated
by the preflight check. Document your setup for reference:

```
# Example SORCC USV engagement setup (not validated by preflight)
SERVO9_FUNCTION  = 0   # GPIO — Hydra relay (payload drop)
SERVO10_FUNCTION = 0   # GPIO — Hydra relay (arm activation, if equipped)
RELAY_PIN        = 13  # AUX1 on Pixhawk 6C
```

Set `[drop] relay_pin` in `config.ini` to match.

---

## Notes

- `FRAME_CLASS = 2` is the most commonly missed param for USV setups. Mission Planner
  sometimes shows the vehicle type as "Rover" even when FRAME_CLASS is wrong. Verify
  in the Full Parameter List, not the vehicle type selector.
- Verify `FRAME_TYPE` as well. For the Enforcer (single thruster, rudder steering):
  `FRAME_TYPE` should be the value appropriate for your propulsion layout. Check the
  ArduPilot Rover FRAME_TYPE docs for your specific hull configuration.
  **TODO: Confirm exact FRAME_TYPE value for Bonzai Enforcer with Hydra platform SME.**
- `SERIAL2_BAUD = 921` encodes 921600 baud in ArduPilot's compressed format.
- The `PILOT_STEER_TYPE = 1` recommendation assumes standard two-channel RC input
  (throttle + rudder). If your Enforcer uses a mixer-based single-stick input, adjust accordingly.
