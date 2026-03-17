---
title: "Autonomous strike"
sidebarTitle: "Autonomous strike"
icon: "robot"
description: "Auto-engage targets meeting all qualification criteria with geofencing"
---

The autonomous strike controller can auto-engage targets without operator input. It evaluates every tracked object against a set of qualification criteria and only initiates a strike when **all five conditions are met simultaneously**.

<Danger>
  **Autonomous strike is off by default.** This feature requires explicit configuration and should only be enabled in controlled environments with appropriate safety measures. The operator is responsible for setting correct geofence boundaries and class whitelists before enabling.
</Danger>

## Qualification criteria

All five criteria must pass before an autonomous strike is initiated:

<Steps>
  <Step title="Controller is enabled">
    The `enabled` flag must be `true` in the `[autonomous]` section of `config.ini`. Default is `false`.
  </Step>
  <Step title="Vehicle is in an allowed mode">
    The vehicle's current flight mode must match one of the modes listed in `allowed_vehicle_modes`. By default, only `AUTO` is permitted. This prevents autonomous strikes during manual flight or RTL.
  </Step>
  <Step title="Vehicle is inside the geofence">
    The vehicle's current GPS position must be within the configured geofence. You can define either a circle (center point + radius) or a polygon. If both are defined, the polygon takes priority.
  </Step>
  <Step title="No active cooldown">
    A minimum time must have elapsed since the last autonomous strike. This prevents rapid re-engagement and is controlled by `strike_cooldown_sec` (default: 30 seconds).
  </Step>
  <Step title="Target qualifies">
    A tracked target must meet all of the following: its class label is in the `allowed_classes` whitelist, its detection confidence is at or above `min_confidence`, and it has been continuously tracked for at least `min_track_frames` consecutive frames.
  </Step>
</Steps>

If any single criterion fails, no strike is issued. The controller continues monitoring.

## Configuration

```ini
[autonomous]
enabled = true
geofence_lat = 34.05
geofence_lon = -118.25
geofence_radius_m = 200.0
min_confidence = 0.85
min_track_frames = 5
allowed_classes = mine, buoy
strike_cooldown_sec = 30.0
allowed_vehicle_modes = AUTO
```

### Full configuration reference

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable autonomous strike controller |
| `geofence_lat` | `0.0` | Circle geofence center latitude |
| `geofence_lon` | `0.0` | Circle geofence center longitude |
| `geofence_radius_m` | `100.0` | Circle geofence radius in metres |
| `geofence_polygon` | *(empty)* | Polygon geofence as `lat,lon;lat,lon;...` (overrides circle) |
| `min_confidence` | `0.85` | Minimum detection confidence for auto-strike |
| `min_track_frames` | `5` | Consecutive frames a target must be tracked |
| `allowed_classes` | *(all)* | Comma-separated class labels allowed for auto-strike |
| `strike_cooldown_sec` | `30.0` | Seconds between autonomous strikes |
| `allowed_vehicle_modes` | `AUTO` | Vehicle must be in one of these modes |

## Geofencing

The geofence constrains where autonomous strikes can occur.

<Tabs>
  <Tab title="Circle geofence">
    Define a center point and radius:

    ```ini
    [autonomous]
    geofence_lat = 34.05
    geofence_lon = -118.25
    geofence_radius_m = 200.0
    ```

    The vehicle must be within the specified radius of the center point for autonomous strikes to be permitted.
  </Tab>
  <Tab title="Polygon geofence">
    Define a polygon as a semicolon-separated list of lat/lon pairs:

    ```ini
    [autonomous]
    geofence_polygon = 34.05,-118.25;34.06,-118.25;34.06,-118.24;34.05,-118.24
    ```

    When a polygon is defined, it overrides the circle geofence.
  </Tab>
</Tabs>

## Audit logging

All autonomous strike actions are logged to the `hydra.audit` logger. Every log entry includes: timestamp, target track ID, class, and confidence, vehicle position at the time of strike, geofence evaluation result, and cooldown state.

This provides a complete accountability trail for post-mission review. Audit logs are written regardless of whether the strike was executed or rejected due to a failed criterion.
