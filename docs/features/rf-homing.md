---
title: "RF homing"
sidebarTitle: "RF homing"
icon: "signal"
description: "Locate RF signal sources using Kismet RSSI gradient ascent navigation"
---

Hydra can autonomously locate RF signal sources by flying search patterns and following RSSI gradients toward the source. This requires [Kismet](https://www.kismetwireless.net/) running on the companion computer with a compatible radio.

## How it works

The RF hunt controller runs as a background thread with a four-state machine:

1. **IDLE**: waiting for a hunt to be started
2. **SEARCHING**: flying a search pattern (lawnmower or spiral) and polling RSSI
3. **HOMING**: RSSI above threshold, following the gradient toward the source
4. **CONVERGED**: RSSI above convergence threshold, source located

The transition from SEARCHING to HOMING happens when the measured RSSI crosses `rssi_threshold_dbm`. The transition from HOMING to CONVERGED happens when it crosses `rssi_converge_dbm`.

## Modes

<Tabs>
  <Tab title="WiFi mode">
    Hunt a specific WiFi access point by its BSSID (MAC address). Kismet polls its WiFi device list and returns the RSSI for the target BSSID.

    **Requirements:**
    - A monitor-mode WiFi adapter (e.g. Alfa AWUS036ACH)
    - Kismet configured with the WiFi adapter as a data source

    ```ini
    [rf_homing]
    enabled = true
    mode = wifi
    target_bssid = AA:BB:CC:DD:EE:FF
    kismet_host = http://localhost:2501
    kismet_user = kismet
    kismet_pass = kismet
    search_pattern = lawnmower
    search_area_m = 100.0
    rssi_threshold_dbm = -80.0
    rssi_converge_dbm = -40.0
    ```
  </Tab>
  <Tab title="SDR mode">
    Hunt a specific radio frequency using an RTL-SDR dongle. Kismet polls its RTL-SDR data source and returns the signal strength at the target frequency.

    **Requirements:**
    - An RTL-SDR dongle (e.g. RTL-SDR Blog V4)
    - Kismet configured with the RTL-SDR as a data source

    ```ini
    [rf_homing]
    enabled = true
    mode = sdr
    target_freq_mhz = 915.0
    kismet_host = http://localhost:2501
    kismet_user = kismet
    kismet_pass = kismet
    search_pattern = lawnmower
    search_area_m = 100.0
    rssi_threshold_dbm = -80.0
    rssi_converge_dbm = -40.0
    ```
  </Tab>
</Tabs>

## Search patterns

When an RF hunt starts, the vehicle flies a search pattern centered on its current position (or a configured start point).

| Pattern | Description |
|---------|-------------|
| `lawnmower` | Back-and-forth parallel legs covering a rectangular area |
| `spiral` | Outward spiral from the center |

The pattern is configured by `search_pattern`, with `search_area_m` controlling the total area and `search_spacing_m` controlling the distance between legs.

```ini
[rf_homing]
search_pattern = lawnmower
search_area_m = 100.0
search_spacing_m = 20.0
search_alt_m = 15.0
```

## Gradient ascent

Once the RSSI crosses the threshold, the controller switches from the search pattern to gradient ascent navigation. It takes steps of `gradient_step_m` metres in the direction of increasing signal strength, continuously polling RSSI at `poll_interval_sec` intervals.

The controller declares convergence when the RSSI reaches `rssi_converge_dbm`, at which point the vehicle holds position over the estimated source location.

## Web dashboard interface

The [web dashboard](/features/dashboard) provides a full RF hunt interface: configure hunt parameters, start and stop hunts, monitor RSSI readings in real time, and view the current hunt state.

The RF hunt is also controllable via the REST API:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/rf/status` | Current hunt state and RSSI readings |
| `POST` | `/api/rf/start` | Start an RF hunt with given parameters |
| `POST` | `/api/rf/stop` | Stop the active RF hunt |

## Full configuration reference

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable RF source localization |
| `mode` | `wifi` | `wifi` (hunt by BSSID) or `sdr` (hunt by frequency) |
| `target_bssid` | *(empty)* | MAC address to locate (WiFi mode) |
| `target_freq_mhz` | `915.0` | Frequency in MHz to locate (SDR mode) |
| `kismet_host` | `http://localhost:2501` | Kismet REST API URL |
| `kismet_user` | `kismet` | Kismet username |
| `kismet_pass` | `kismet` | Kismet password |
| `search_pattern` | `lawnmower` | Search pattern: `lawnmower` or `spiral` |
| `search_area_m` | `100.0` | Search area size in metres |
| `search_spacing_m` | `20.0` | Grid spacing between search legs |
| `search_alt_m` | `15.0` | Search altitude in metres |
| `rssi_threshold_dbm` | `-80.0` | RSSI level to switch from search to homing |
| `rssi_converge_dbm` | `-40.0` | RSSI level to declare source found |
| `gradient_step_m` | `5.0` | Step size for gradient ascent |
| `poll_interval_sec` | `0.5` | RSSI polling interval |
| `arrival_tolerance_m` | `3.0` | Distance to consider a waypoint reached |
