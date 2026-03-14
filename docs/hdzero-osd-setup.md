# HDZero OSD Setup Guide

How to get Hydra Detect telemetry on your HDZero goggles via ArduPilot's
MSP DisplayPort OSD.

## Overview

HDZero VTXs render OSD using **MSP DisplayPort** — a serial protocol where the
flight controller sends character/position data to the VTX, which composites
it onto the digital video feed. This is different from analog FCs that use an
onboard MAX7456 chip.

Hydra sends detection data to the FC over MAVLink. The FC's OSD engine then
forwards it (along with normal flight data) to the HDZero VTX via MSP.

```
Jetson ──MAVLink──> FC ──MSP DisplayPort──> HDZero VTX ──> Goggles
```

## Requirements

- **Flight controller** running ArduPilot 4.3+ with a spare UART
- **HDZero VTX** (Freestyle V2, Race V3, Whoop Lite, or any MSP-capable unit)
- **HDZero goggles** (or compatible monitor)
- Hydra Detect running on the companion computer with MAVLink connected to the FC

> **Note:** This guide uses the FC as the OSD source. You do NOT need an
> AT7456E/MAX7456 analog OSD chip on the FC — MSP DisplayPort works on any
> ArduPilot FC with a free serial port (including Pixhawk).

## Wiring

Connect a spare FC UART TX pin to the HDZero VTX RX (MSP) pad.

| FC Pin | HDZero VTX Pin | Notes |
|--------|---------------|-------|
| UART TX (e.g. TX6) | RX (MSP) | OSD data to VTX |
| GND | GND | Common ground |

The VTX only receives OSD data — you only need the TX→RX line (no RX→TX
needed for display-only OSD).

Check your VTX documentation for which pad accepts MSP input. On most HDZero
VTXs this is labelled **RX** or **MSP**.

## ArduPilot Configuration

### 1. Enable MSP OSD output

Set the serial port you wired to the VTX as MSP protocol. For example, if you
used SERIAL6:

```
SERIAL6_PROTOCOL = 33    ; MSP DisplayPort
SERIAL6_BAUD     = 115   ; 115200 baud
```

### 2. Configure OSD type

```
OSD_TYPE = 3             ; MSP (DisplayPort)
```

### 3. Enable the OSD screen

```
OSD1_ENABLE = 1
OSD1_MESSAGE_EN = 1      ; Enable the message panel (shows STATUSTEXT)
```

### 4. (Optional) Enable Lua scripting for named_value mode

If you want the richer `named_value` OSD mode instead of simple STATUSTEXT:

```
SCR_ENABLE    = 1
SCR_HEAP_SIZE = 65536
```

Copy `scripts/hydra_osd.lua` to `APM/scripts/` on the FC SD card.

### 5. Reboot the FC

All parameter changes require a reboot to take effect.

## Hydra Configuration

### Option A: STATUSTEXT mode (simplest)

No Lua script needed. Detection info appears in the OSD message panel wherever
ArduPilot places `OSD1_MESSAGE` on screen.

```ini
[osd]
enabled = true
mode = statustext
update_interval = 0.2
```

**What you see on your goggles:**
```
T:3 12fps 35ms LK#5TRK
```

### Option B: named_value mode (richer display)

Requires the Lua script on the FC (see step 4 above). Adds stale-link
warnings and structured data fields.

```ini
[osd]
enabled = true
mode = named_value
update_interval = 0.2
```

**What you see on your goggles:**
```
T:3 12fps 35ms LK#5TRK     (normal)
HYDRA: NO LINK              (if Jetson stops sending for >3s)
HYDRA: WAITING              (before first data arrives)
```

## Troubleshooting

### No OSD text on goggles

1. Verify `OSD_TYPE = 3` and reboot the FC
2. Check that `SERIAL*_PROTOCOL = 33` on the correct UART
3. Confirm wiring: FC TX → VTX RX, common GND
4. In Mission Planner OSD tab, verify the message panel is positioned on screen
5. Test without Hydra first — normal ArduPilot OSD elements (battery, GPS, etc.)
   should appear. If they don't, the issue is FC↔VTX wiring or parameters

### OSD shows flight data but no Hydra text

1. Check that `[osd] enabled = true` in `config.ini`
2. Verify MAVLink is connected (`[mavlink] enabled = true`)
3. Check Hydra logs for OSD-related messages
4. If using `named_value` mode, confirm the Lua script is loaded — you should
   see `"Hydra OSD script loaded"` in the FC messages

### "HYDRA: NO LINK" stuck on screen

The Lua script hasn't received data from the Jetson in over 3 seconds:

1. Check that Hydra is running and the MAVLink connection is active
2. Verify `update_interval` isn't set too high
3. Check the MAVLink serial connection between Jetson and FC

### Flickering or garbled OSD

- Lower the update rate: set `update_interval = 0.5` or higher
- Ensure the MSP serial baud matches on both FC and VTX (115200 is standard)
- Check for electrical noise on the serial line — use twisted pair or shielded
  wire for the UART connection

## HDZero VTX Firmware

Keep your VTX firmware up to date. MSP DisplayPort support has improved across
HDZero firmware releases. Update via the HDZero goggles menu or the HDZero
firmware tool.

## Differences from Analog OSD

| | Analog (MAX7456) | HDZero (MSP DisplayPort) |
|--|-----------------|--------------------------|
| **OSD_TYPE** | `1` | `3` |
| **FC requirement** | Must have MAX7456/AT7456E chip | Any FC with a spare UART |
| **Wiring** | Video signal passthrough on FC | Serial TX → VTX RX |
| **Latency** | Sub-millisecond (hardware overlay) | ~1 frame (VTX composites digitally) |
| **Resolution** | 30×16 characters | 50×18 characters (HD) |
| **Pixhawk compatible** | No (no OSD chip) | Yes |
