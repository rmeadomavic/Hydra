# Pixhawk Prerequisites: UGV (ArduRover)

Platform: Axial SCX6 Trail Honcho (1/6 scale) running ArduRover on Pixhawk 6C.

Run the preflight from the companion (Jetson) against the flight controller over the
companion UART:

```
python scripts/pixhawk_preflight.py --profile ugv --conn /dev/ttyTHS1 --baud 921600
```

(`/dev/ttyTHS1` at 921600 is the SCX6 instructor build's Jetson↔FC link on TELEM3/SERIAL5.
On reference wiring with the companion on TELEM2, use that port instead.)

> **ArduRover action codes** (verified against AP source): `0` Warn/Report only · `1` RTL ·
> `2` Hold · `3` SmartRTL · `4` SmartRTL-or-Hold · `5` Terminate. **Value `2` is Hold, not
> RTL** — earlier revisions of this doc had this backwards.

---

## Required Parameters

These must be set correctly before running Hydra. Wrong values cause silent failures: the system starts but autonomous behavior will not work safely.

| Parameter | Expected | Why |
|---|---|---|
| `FENCE_ENABLE` | `1` | Geofence required for any autonomous behavior. Without it the rover can drive beyond the operating area with no recovery. |
| `FENCE_ACTION` | `2` (Hold) | Stop in place on breach. Preferred over RTL for shoothouse/tunnel lanes so the rover stops at the boundary instead of driving home across the lane. |
| `FENCE_RADIUS` | `300` | Circular fence radius (m). Covers the SORCC operating area; tighten per site. |
| `FENCE_MARGIN` | `2` | 2 m breach margin before the action escalates. Avoids a hard stop exactly at the boundary. |
| `BATT_MONITOR` | `4` | Analog voltage + current (Holybro PM02 on POWER1). Without it the battery failsafe has nothing to measure and never fires. |
| `BATT_VOLT_PIN` | `8` | Pixhawk 6C POWER1 voltage pin (PM02). |
| `BATT_CURR_PIN` | `4` | Pixhawk 6C POWER1 current pin (PM02). |
| `BATT_VOLT_MULT` | `18.18` | PM02 voltage-divider multiplier. |
| `BATT_AMP_PERVLT` | `36.36` | PM02 current scaling (A/V). |
| `BATT_LOW_VOLT` | `20.4` | Low-battery trigger, 3.4 V/cell on a 6S Molicel P42A Li-ion pack. Li-ion floor is 2.5 V/cell — do **not** reuse LiPo 3.5/3.3 numbers. |
| `BATT_CRT_VOLT` | `19.2` | Critical trigger, 3.2 V/cell. If crawl-stall sag nuisance-trips, lower to 18.6. |
| `BATT_FS_LOW_ACT` | `2` (Hold) | Hold on low battery. Inert without `BATT_LOW_VOLT` set. |
| `BATT_FS_CRT_ACT` | `2` (Hold) | Hold on critical battery. |
| `BATT_CAPACITY` | `12600` | 6S3P P42A pack, 3 x 4200 mAh. |
| `SERIAL2_PROTOCOL` | `2` | Companion link must be MAVLink2. See the SERIAL warning below. |
| `SERIAL2_BAUD` | `921` | 921600 baud on the companion link. **See warning below.** |

> ⚠ **SERIAL warning — reference wiring only.** `SERIAL2_*` assumes the companion is on
> TELEM2/SERIAL2 (Hydra default). The **SCX6 puts the Jetson on TELEM3/SERIAL5** at 921600
> because all five UARTs are allocated (CRSF, SiK, GPS, OSD, Jetson) — and on that build
> **SERIAL2 is the 433 MHz SiK radio at 57600**. Applying `SERIAL2_BAUD 921` there breaks
> the SiK link. Set MAVLink2 + 921600 on whichever UART your companion actually uses.

---

## Recommended Parameters

Not required to start Hydra, but strongly recommended. A mismatch generates a `[WARN]` in the preflight report and does not block the run.

| Parameter | Recommended | Why |
|---|---|---|
| `FS_GCS_ENABLE` | `2` | GCS-heartbeat failsafe. If the Hydra companion loses MAVLink, the rover recovers rather than freezing in place. |
| `FS_THR_ENABLE` | `1` | RC-loss failsafe. RTL or hold on RC loss. Do not disable. |

---

## Stream Rates

Minimum rates on the companion port. These affect how quickly Hydra receives GPS, telemetry, and attitude data from the autopilot.

Set via `SRx_*` where `x` maps to your connection port. `SR1_*` maps to TELEM2 when the
companion is on SERIAL2 with SRx overrides. If your companion is on a different UART (e.g.
SERIAL5 on the SCX6), use the matching `SRx_*` index.

| Parameter | Minimum Hz | Why |
|---|---|---|
| `SR1_POSITION` | `5` | GPS position for TAK markers and geo-tracking. Below 5 Hz the map track lags. |
| `SR1_EXTRA1` | `4` | Attitude/heading. Used for OSD orientation. |
| `SR1_EXTRA2` | `2` | Battery voltage and current. Used for battery warnings. |
| `SR1_RAW_SENS` | `2` | IMU data. Required if RF hunt mode is active. |

---

## Failsafe Expectations

- **RC Loss:** `FS_THR_ENABLE = 1`. RTL or hold on RC loss. Do not disable.
- **GCS Loss:** `FS_GCS_ENABLE = 2`. Recover after missed heartbeats rather than freezing.
- **Battery:** `BATT_MONITOR = 4` plus `BATT_LOW_VOLT`/`BATT_CRT_VOLT` thresholds, with
  `BATT_FS_LOW_ACT = 2` and `BATT_FS_CRT_ACT = 2` — both **Hold** (value `2` is Hold on
  ArduRover; `1` is RTL). An action without a voltage threshold never fires.
- **Geofence:** `FENCE_ACTION = 2` (Hold) on breach. Confirm `FENCE_RADIUS` matches your
  operating area.

---

## Servo / Relay Assignments

Engagement actions (drop, arm) are operator-configured at mission time. Hydra reads
these from `config.ini [drop]`. The preflight does not require or validate specific servo assignments.

> Note: on the SCX6, `CH5` / `SERVO5` drives the **2-speed transmission shift**, not a
> diff lock. Do not repurpose it for engagement.

```
# Example SORCC UGV engagement setup (not validated by preflight)
SERVO9_FUNCTION = 0    # GPIO — Hydra relay control
RELAY_PIN       = 13   # AUX1 on Pixhawk 6C
```

Set `[drop] relay_pin` in `config.ini` to match.

---
