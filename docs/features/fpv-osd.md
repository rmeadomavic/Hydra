---
title: "FPV OSD overlay"
sidebarTitle: "FPV OSD"
icon: "tv"
description: "Detection telemetry on your FPV feed via analog OSD chip or HDZero MSP DisplayPort"
---

Hydra can push detection telemetry directly onto your FPV video feed. For analog systems, this uses the FC's onboard OSD chip (MAX7456 / AT7456E). For HDZero digital systems, it uses MSP DisplayPort over a serial UART — no OSD chip needed.

## What it looks like

On your goggles or monitor, you see a compact status line:

```
T:3 12fps 35ms LK#5TRK
```

That reads as:

- **T:3**: 3 active tracks
- **12fps**: pipeline running at 12 frames per second
- **35ms**: inference time per frame
- **LK#5TRK**: target lock active on track #5, currently tracking

Composited by the FC's OSD engine with minimal delay.

## Compatible hardware

**Analog OSD (MAX7456 chip):**
- Matek H743, SpeedyBee F405-Wing, any FC with AT7456E/MAX7456

**HDZero MSP DisplayPort (no OSD chip needed):**
- Pixhawk 6C, Cube, or any ArduPilot FC with a spare UART
- See the [HDZero OSD setup guide](/setup/hdzero-osd) for wiring and parameters

<Warning>
**Pixhawk 6C:** Has no onboard OSD chip. The `statustext` mode only shows alerts in Mission Planner, NOT on goggles. Use `named_value` mode with the Lua script for HDZero OSD display. See [HDZero OSD setup](/setup/hdzero-osd).
</Warning>

## Modes

Two OSD modes depending on how much setup you want:

<Tabs>
  <Tab title="Statustext mode">
    The simple option. Detection info appears in the OSD message panel, the same area that shows ArduPilot status messages.

    **Setup:** Just set the config flag. No changes needed on the flight controller.

    ```ini
    [osd]
    enabled = true
    mode = statustext
    ```

    | Pros | Cons |
    |------|------|
    | Zero FC-side setup | Shares space with other status messages |
    | Works on any OSD-equipped FC | Less control over display formatting |
  </Tab>
  <Tab title="Named value mode">
    The richer option. Uses a Lua script on the flight controller for a dedicated display with stale-link warnings when Hydra stops sending updates.

    **Setup:** Requires copying a Lua script to the FC and setting a few ArduPilot parameters.

    ```ini
    [osd]
    enabled = true
    mode = named_value
    ```

    | Pros | Cons |
    |------|------|
    | Dedicated display area | Requires Lua script on FC |
    | Stale-link warnings | Needs `SCR_ENABLE` and OSD parameters |
    | Richer formatting | Slightly more setup effort |
  </Tab>
</Tabs>

## Lua script setup (named_value mode)

If you choose `named_value` mode:

<Steps>
  <Step title="Copy the Lua script">
    Copy `scripts/hydra_osd.lua` from the Hydra repository to the `APM/scripts/` directory on the flight controller's SD card.
  </Step>
  <Step title="Set ArduPilot parameters">
    ```
    SCR_ENABLE = 1
    SCR_HEAP_SIZE = 65536
    OSD_TYPE = 5             ; MSP DisplayPort HD (use 1 for analog MAX7456)
    OSD1_TXT_RES = 1         ; HD resolution
    MSP_OPTIONS = 0
    OSD1_ENABLE = 1
    OSD1_MESSAGE_EN = 1
    SERIAL5_PROTOCOL = 42    ; MSP DisplayPort on TELEM3 (Pixhawk 6C)
    SERIAL5_BAUD = 115       ; 115200
    ```
  </Step>
  <Step title="Update config.ini">
    ```ini
    [osd]
    enabled = true
    mode = named_value
    ```
  </Step>
  <Step title="Reboot the flight controller">
    Power-cycle the FC so the Lua scripting engine starts and loads the OSD script.
  </Step>
</Steps>

## Configuration

```ini
[osd]
enabled = false
mode = statustext
update_interval = 0.2
```

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Turn FPV OSD overlay on or off (requires MAVLink connection to FC) |
| `mode` | `statustext` | `statustext` (simple) or `named_value` (requires Lua script on FC) |
| `update_interval` | `0.2` | Seconds between OSD updates. Lower is snappier but chattier on the MAVLink bus |
