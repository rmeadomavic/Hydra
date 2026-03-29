---
title: "Target control"
sidebarTitle: "Target control"
icon: "crosshairs"
description: "Lock, track, and strike targets from the web dashboard"
---

The target control panel on the [web dashboard](/features/dashboard) lets you interact with tracked objects in real time. Three modes, each escalating in autonomy.

## Hold Position

Tells the vehicle to stop and hold where it is.

- **Drones (ArduCopter)**: switches to `LOITER` mode
- **Rovers and boats (ArduRover)**: switches to `HOLD` mode

The system auto-detects the correct hold mode from the vehicle's mode mapping. Triggered via the dashboard button or `POST /api/vehicle/loiter`.

## Keep in Frame

Lock onto a tracked object and Hydra will send yaw corrections every frame to keep it centered in the camera.

1. Select a target from the active track list in the dashboard
2. Click **Keep in Frame**
3. The pipeline sends `CONDITION_YAW` commands to the flight controller based on the target's pixel offset from frame center and the camera's horizontal field of view

This works on any ArduPilot vehicle. On drones with a gimbal, you can also enable `guided_roi_on_detect` in `config.ini` to point the gimbal at detections automatically.

```ini
[mavlink]
guided_roi_on_detect = false
```

## Strike

Strike mode navigates the vehicle toward a target's estimated GPS position. This is the most aggressive action and requires confirmation.

<Steps>
  <Step title="Estimate target GPS">
    Hydra estimates the target's ground position using the vehicle's current GPS coordinates, heading, and the target's bearing offset calculated from its pixel position and the camera's horizontal field of view.
  </Step>
  <Step title="Switch to GUIDED">
    The vehicle is commanded into `GUIDED` mode, giving Hydra direct waypoint control.
  </Step>
  <Step title="Send waypoint">
    A waypoint is sent via `SET_POSITION_TARGET_GLOBAL_INT` at the estimated target position. The distance is controlled by `strike_distance_m` in `config.ini`.
  </Step>
  <Step title="Track during approach">
    While the vehicle is in transit, the pipeline continues tracking the target and sending yaw corrections to keep it centered.
  </Step>
  <Step title="GCS alerts">
    STATUSTEXT messages are sent to your ground control station throughout the sequence.
  </Step>
</Steps>

```ini
[mavlink]
strike_distance_m = 20.0
```

<Warning>
  **GCS override is always available.** You can retake control at any time from Mission Planner or any other ground control station. Changing the flight mode from the GCS immediately overrides Hydra's commands.
</Warning>

## API endpoints

Target control is fully accessible via the REST API:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/target` | Current target lock state |
| `POST` | `/api/target/lock` | Lock a track for Keep in Frame (`{"track_id": 5}`) |
| `POST` | `/api/target/unlock` | Release target lock |
| `POST` | `/api/target/strike` | Send strike command (`{"track_id": 5, "confirm": true}`) |
| `POST` | `/api/vehicle/loiter` | Command vehicle to hold position |
