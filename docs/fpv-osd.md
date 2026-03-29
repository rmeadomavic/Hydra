# FPV OSD

Hydra pushes detection telemetry onto your FPV feed. Three modes available, each with different hardware requirements and capabilities.

## Mode Comparison

| Mode | Hardware Required | Setup Effort | Display Quality |
|------|------------------|-------------|-----------------|
| `statustext` | Any ArduPilot FC with OSD chip | Config flag only | Basic text line |
| `named_value` | FC with OSD chip + Lua scripting | Lua script on SD card | Structured fields |
| `msp_displayport` | Dedicated serial UART to HDZero VTX | Wiring + serial config | Full canvas control |

## Mode 1: statustext

The simplest option. Hydra sends STATUSTEXT messages over MAVLink. The flight controller's OSD chip renders them in the message panel area of your FPV feed.

**Works with**: Any ArduPilot FC with an onboard OSD chip (MAX7456 or AT7456E). Matek H743, SpeedyBee F405-Wing, etc.

**Does not work with**: Pixhawk 6C (no OSD chip).

### What it looks like

```
T:3 12fps 35ms LK#5TRK
```

That reads: 3 active tracks, 12 FPS, 35ms inference, locked on track #5 in track mode.

### Configuration

```ini
[osd]
enabled = true
mode = statustext
update_interval = 2.0
```

No other setup required. The FC OSD must be enabled in ArduPilot parameters (`OSD_TYPE=1`, `OSD1_ENABLE=1`).

## Mode 2: named_value

Sends structured data as `NAMED_VALUE_FLOAT` and `NAMED_VALUE_INT` MAVLink messages. A Lua script on the flight controller decodes these and renders them at specific OSD positions.

Richer display than statustext. Shows individual fields with labels. Can include stale-link warnings.

### Setup

1. Copy `scripts/hydra_osd.lua` to the `APM/scripts/` directory on the FC's SD card.
2. Set ArduPilot parameters:
   ```
   SCR_ENABLE = 1
   SCR_HEAP_SIZE = 65536
   OSD_TYPE = 1
   OSD1_ENABLE = 1
   ```
3. Set Hydra config:
   ```ini
   [osd]
   enabled = true
   mode = named_value
   update_interval = 2.0
   ```
4. Reboot the flight controller.

> [!WARNING]
> The Lua script sends STATUSTEXT at 5 Hz to update the OSD. This floods the GCS log. To disable the Lua script without removing it: set `SCR_ENABLE = 0` in ArduPilot params.

### Named values sent

The OSD module sends these named values over MAVLink. Names are limited to 10 characters by the MAVLink spec.

| Name | Type | Content |
|------|------|---------|
| `HYD_FPS` | float | Pipeline FPS |
| `HYD_INF` | float | Inference time (ms) |
| `HYD_TRK` | int | Active track count |
| `HYD_LCK` | int | Locked track ID (0 = none) |
| `HYD_CLS` | int | Top detection class ID |
| `HYD_CONF` | float | Top detection confidence |

## Mode 3: msp_displayport

Speaks MSP v1 DisplayPort protocol over a dedicated serial UART directly to an HDZero VTX. Bypasses the flight controller entirely. Draws detection telemetry on the HD OSD canvas (default 50 columns by 18 rows).

### Wiring

Connect a Jetson UART TX pin to the VTX RX pad. No FC involvement.

```mermaid
graph LR
    style J fill:#385723,color:#fff
    style V fill:#A6BC92,color:#000

    J[Jetson UART TX] -->|serial| V[HDZero VTX RX pad]
```

The VTX must support MSP DisplayPort input. HDZero VTXes support this natively.

### Configuration

```ini
[osd]
enabled = true
mode = msp_displayport
serial_port = /dev/ttyUSB0    ; serial device to VTX
serial_baud = 115200
canvas_cols = 50              ; HD OSD canvas width
canvas_rows = 18              ; HD OSD canvas height
update_interval = 0.5
```

### ArduPilot Serial Setup

If routing through the FC (alternative wiring):

```
SERIALn_PROTOCOL = 42    ; MSP DisplayPort protocol
SERIALn_BAUD = 115
MSP_OPTIONS = 0           ; NOT 1
OSD_TYPE = 5              ; MSP
```

> [!TIP]
> HDZero DisplayPort protocol number is 42 in ArduPilot, not 33. This is a common source of confusion. `MSP_OPTIONS` should be 0 (not 1) for HDZero compatibility.

### Canvas Layout

The MSP DisplayPort module draws a compact HUD on the OSD canvas:

```
Row 0:  T:3  12fps  35ms
Row 1:  person 0.87 #5
Row 2:  LCK #5 FOLLOW
Row 16: GPS:3D  34.05,-118.25
Row 17: HYDRA-1-USV
```

The canvas size is configurable. Default 50x18 matches standard HD OSD dimensions.

## Compatibility Matrix

| Flight Controller | statustext | named_value | msp_displayport |
|------------------|------------|-------------|-----------------|
| Matek H743 | Yes (AT7456E) | Yes | Yes (via serial) |
| SpeedyBee F405-Wing | Yes (AT7456E) | Yes | Yes (via serial) |
| Pixhawk 6C | No (no OSD chip) | No | Yes (direct to VTX) |
| Any FC + HDZero | N/A | N/A | Yes (direct wiring) |

If your FC has no OSD chip, use `msp_displayport` mode with direct wiring to the VTX, or rely on the web dashboard overlay.
