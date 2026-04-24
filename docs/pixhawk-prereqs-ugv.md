# Pixhawk Prerequisites — UGV (ArduRover)

Platform: Traxxas Stampede running ArduRover on Pixhawk 6C.

Run `python scripts/pixhawk_preflight.py --profile ugv --conn /dev/ttyACM0` to validate
these params live against your flight controller.

---

## Required Parameters

These must be set correctly before running Hydra. Wrong values here cause silent failures —
the system will appear to work but autonomous behavior will not function safely.

| Parameter | Expected | Why |
|---|---|---|
| `FENCE_ENABLE` | `1` | Geofence required for any autonomous behavior. Without it the rover can drive beyond the operating area with no automatic recovery. |
| `SERIAL2_PROTOCOL` | `2` | Companion computer port (TELEM2) must be MAVLink2. MAVLink1 works but loses long-parameter and signed-message support. |
| `SERIAL2_BAUD` | `921` | 921600 baud required for Hydra's heartbeat + param + command traffic. 57/115 are too slow under load. |

---

## Recommended Parameters

Not required to start Hydra, but strongly recommended for field operations. A mismatch
generates a `[WARN]` in the preflight report and does not block the run.

| Parameter | Recommended | Why |
|---|---|---|
| `BATT_FS_LOW_ACT` | `2` | RTL on low battery. Without this, the rover continues until the battery dies and loses comms. Value 2 = RTL. |
| `FENCE_ACTION` | `1` | RTL when fence is breached. Value 0 (report only) means the rover ignores the fence boundary. |
| `FENCE_MARGIN` | `2` | 2-meter breach margin before escalation to LAND. Prevents hard stops exactly at the fence boundary. |
| `FS_GCS_ENABLE` | `2` | GCS heartbeat failsafe enabled. If the Hydra companion loses MAVLink, the rover should RTL rather than freeze in place. |

---

## Stream Rates

Minimum rates required on the companion port (SERIAL2). These affect how quickly Hydra
receives GPS, telemetry, and attitude data from the autopilot.

Set via `SRx_*` parameters where `x` maps to your connection port. If the companion is
connected to SERIAL2 (TELEM2), use `SR2_*`. If using a MAVProxy router that maps to SR1,
use `SR1_*`. Match these to whatever SRx index corresponds to the companion port in your setup.

| Parameter | Minimum Hz | Why |
|---|---|---|
| `SR1_POSITION` | `5` | GPS position data for TAK markers and geo-tracking. Below 5 Hz, the map track lags noticeably. |
| `SR1_EXTRA1` | `4` | Attitude/heading. Used for OSD orientation display. |
| `SR1_EXTRA2` | `2` | Battery voltage and current. Used for battery warnings. |
| `SR1_RAW_SENS` | `2` | IMU data. Required if RF hunt mode is active. |

---

## Failsafe Expectations

- **RC Loss:** Set `FS_THR_ENABLE = 1`. The rover should return to launch (RTL) or hold in place — do not set to disabled.
- **GCS Loss:** `FS_GCS_ENABLE = 2` (see recommended above). RTL after 5 seconds of missed heartbeats.
- **Battery:** `BATT_FS_LOW_ACT = 2` triggers RTL at low voltage. `BATT_FS_CRT_ACT = 1` (Hold) or `2` (RTL) for critical.
- **Geofence:** `FENCE_ACTION = 1` (RTL) on breach. Confirm `FENCE_RADIUS` and `FENCE_ALT_MAX` match your operating area.

---

## Servo / Relay Assignments

Engagement actions (drop, arm) are operator-configured at mission time. Hydra reads
these from `config.ini [drop]` — it does not require or validate specific servo assignments.

The preflight does not check servo functions because they are valid only during armed
operation and vary by loadout. Document your team's setup here for reference:

```
# Example SORCC UGV engagement setup (not validated by preflight)
SERVO9_FUNCTION = 0    # GPIO — Hydra relay control
RELAY_PIN       = 13   # AUX1 on Pixhawk 6C
```

Set `[drop] relay_pin` in `config.ini` to match.

---

## Notes

- `SERIAL2_BAUD = 921` encodes 921600 baud in ArduPilot's compressed format (value `921` = 921600).
- If using MAVProxy as a router, set baud on the MAVProxy master port, not on the ArduPilot serial port directly.
- Stream rates set in Mission Planner apply immediately but do not persist across reboots unless saved. Run **Write Params** after adjusting.
- The UGV profile does not check `FLTMODE_CH` because ArduRover does not use flight mode channels the same way ArduCopter does. Mode switching is done via `MODE_CH` (default CH 8 for Rover); verify GUIDED is reachable on your RC transmitter.
