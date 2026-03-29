---
title: "HDZero OSD setup"
description: "Get Hydra Detect telemetry displayed on HDZero goggles via ArduPilot's MSP DisplayPort OSD."
sidebarTitle: "HDZero OSD"
icon: "glasses"
---

How to get Hydra Detect telemetry on your HDZero goggles via ArduPilot's MSP DisplayPort OSD.

## Overview

HDZero VTXs render OSD using **MSP DisplayPort**, a serial protocol where the flight controller sends character/position data to the VTX, which composites it onto the digital video feed. This is different from analog FCs that use an onboard MAX7456 chip.

Hydra sends detection data to the FC over MAVLink. The FC's OSD engine then forwards it (along with normal flight data) to the HDZero VTX via MSP.

```
Jetson --MAVLink--> FC --MSP DisplayPort--> HDZero VTX --> Goggles
```

## Requirements

- **Flight controller** running ArduPilot 4.3+ with a spare UART
- **HDZero VTX** (Freestyle V2, Race V3, Whoop Lite, or any MSP-capable unit)
- **HDZero goggles** (or compatible monitor)
- Hydra Detect running on the companion computer with MAVLink connected to the FC

<Note>
This guide uses the FC as the OSD source. You do NOT need an AT7456E/MAX7456 analog OSD chip on the FC. MSP DisplayPort works on any ArduPilot FC with a free serial port (including Pixhawk).
</Note>

## Wiring

Connect a spare FC UART TX pin to the HDZero VTX RX (MSP) pad.

| FC Pin | HDZero VTX Pin | Notes |
|--------|---------------|-------|
| UART TX (e.g. TX6) | RX (MSP) | OSD data to VTX |
| GND | GND | Common ground |

The VTX only receives OSD data. You only need the TX to RX line (no RX to TX needed for display-only OSD).

Check your VTX documentation for which pad accepts MSP input. On most HDZero VTXs this is labelled **RX** or **MSP**.

## ArduPilot configuration

<Steps>

<Step title="Enable MSP OSD output">

Set the serial port you wired to the VTX as MSP DisplayPort. On Pixhawk 6C, TELEM3 = SERIAL5:

```
SERIAL5_PROTOCOL = 42    ; MSP DisplayPort (not 33!)
SERIAL5_BAUD     = 115   ; 115200 baud
```

<Warning>
Use protocol **42** (MSP DisplayPort), not 33. Protocol 33 is standard MSP and will not render OSD.
</Warning>

</Step>

<Step title="Configure OSD type">

```
OSD_TYPE      = 5        ; MSP DisplayPort HD (not 3!)
OSD1_TXT_RES  = 1        ; HD resolution (50x18)
MSP_OPTIONS   = 0        ; Must be 0
```

</Step>

<Step title="Enable the OSD screen">

```
OSD1_ENABLE = 1
OSD1_MESSAGE_EN = 1      ; Enable the message panel (shows STATUSTEXT)
```

</Step>

<Step title="(Optional) Enable Lua scripting for named_value mode">

If you want the richer `named_value` OSD mode instead of simple STATUSTEXT:

```
SCR_ENABLE    = 1
SCR_HEAP_SIZE = 65536
```

Copy `scripts/hydra_osd.lua` to `APM/scripts/` on the FC SD card. The script updates the OSD at 1 Hz.

<Warning>
**Pixhawk 6C users:** The `statustext` mode only shows detection alerts in Mission Planner, NOT on the goggles. Use `named_value` mode with this Lua script for HDZero OSD display.
</Warning>

</Step>

<Step title="Reboot the FC">

All parameter changes require a reboot to take effect.

</Step>

</Steps>

## Hydra configuration

<Tabs>

<Tab title="STATUSTEXT mode (simplest)">

No Lua script needed. Detection info appears in the OSD message panel wherever ArduPilot places `OSD1_MESSAGE` on screen.

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

</Tab>

<Tab title="named_value mode (richer display)">

Requires the Lua script on the FC (see step 4 above). Adds stale-link warnings and structured data fields.

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

</Tab>

</Tabs>

## Troubleshooting

<AccordionGroup>

<Accordion title="No OSD text on goggles">

1. Verify `OSD_TYPE = 5` (not 3!) and reboot the FC
2. Check that `SERIAL5_PROTOCOL = 42` (not 33!) on the correct UART
3. Verify `MSP_OPTIONS = 0` and `OSD1_TXT_RES = 1`
4. Confirm wiring: FC TX to VTX RX, common GND
5. In Mission Planner OSD tab, verify the message panel is positioned on screen
6. Test without Hydra first. Normal ArduPilot OSD elements (battery, GPS, etc.) should appear. If they don't, the issue is FC-to-VTX wiring or parameters.

</Accordion>

<Accordion title="OSD shows flight data but no Hydra text">

1. Check that `[osd] enabled = true` in `config.ini`
2. Verify MAVLink is connected (`[mavlink] enabled = true`)
3. Check Hydra logs for OSD-related messages
4. If using `named_value` mode, confirm the Lua script is loaded. You should see `"Hydra OSD script loaded"` in the FC messages.

</Accordion>

<Accordion title='"HYDRA: NO LINK" stuck on screen'>

The Lua script hasn't received data from the Jetson in over 3 seconds:

1. Check that Hydra is running and the MAVLink connection is active
2. Verify `update_interval` isn't set too high
3. Check the MAVLink serial connection between Jetson and FC

</Accordion>

<Accordion title="Flickering or garbled OSD">

- Lower the update rate: set `update_interval = 0.5` or higher
- Ensure the MSP serial baud matches on both FC and VTX (115200 is standard)
- Check for electrical noise on the serial line. Use twisted pair or shielded wire for the UART connection.

</Accordion>

</AccordionGroup>

## HDZero VTX firmware

Keep your VTX firmware up to date. MSP DisplayPort support has improved across HDZero firmware releases. Update via the HDZero goggles menu or the HDZero firmware tool.

## Analog vs. HDZero OSD comparison

| | Analog (MAX7456) | HDZero (MSP DisplayPort) |
|--|-----------------|--------------------------|
| **OSD_TYPE** | `1` | `5` |
| **SERIAL_PROTOCOL** | N/A (video passthrough) | `42` |
| **FC requirement** | Must have MAX7456/AT7456E chip | Any FC with a spare UART |
| **Wiring** | Video signal passthrough on FC | Serial TX to VTX RX |
| **Latency** | Sub-millisecond (hardware overlay) | ~1 frame (VTX composites digitally) |
| **Resolution** | 30x16 characters | 50x18 characters (HD) |
| **Pixhawk compatible** | No (no OSD chip) | Yes |
