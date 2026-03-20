# Pixel-Lock Servo Tracking — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Author:** Claude + sorcc

## Problem

Hydra can detect, track, and make autonomous strike decisions — but the only
physical actuators are `adjust_yaw()` (MAV_CMD_CONDITION_YAW, requires GPS +
armed vehicle) and `flash_servo()` (light bar blink). There is no way to:

1. Physically aim a servo (turret, gimbal, spotlight, antenna) at a tracked target
2. Actuate a payload mechanism (release, marker, flag) on a strike command
3. Demonstrate the detect → track → decide → act pipeline on a bench without
   GPS, without arming, without a running vehicle

## Solution

A **pixel-lock servo tracker** that maps the target's horizontal offset in the
camera frame directly to PWM output on configurable Pixhawk servo channels.

**Pixel-lock** = closed-loop control where the camera is the feedback sensor.
The target's pixel offset from frame center drives the actuator. No GPS, no IMU,
no range estimation. Works indoors, on a bench, at any range.

Two servo roles:

| Servo | Purpose | Trigger |
|-------|---------|---------|
| **Pan** | Track target horizontally — maps `error_x` to PWM | Every frame while target is locked |
| **Strike** | Actuate payload mechanism — pulse on/off | Strike command (manual or autonomous) |

## Architecture

```
Pipeline loop (existing)
    │
    ├── error_x computed from locked target bbox center vs frame center
    │       (already computed at pipeline.py:554-556)
    │
    ├── adjust_yaw(error_x)          ← existing, vehicle heading correction
    │       skipped if servo_tracker.replaces_yaw is True
    │
    └── servo_tracker.update(error_x) ← NEW, direct PWM to pan servo
            │
            └── mavlink.set_servo(pan_channel, pwm)  ← existing method


Strike command path (existing)
    │
    ├── _handle_strike_command(track_id)
    │       ├── existing: estimate GPS → command_guided_to()
    │       └── NEW: servo_tracker.fire_strike()
    │               └── mavlink.set_servo(strike_channel, fire_pwm)
    │                       └── (after duration) set_servo(strike_channel, safe_pwm)
    │
    └── AutonomousController.evaluate() → strike_cb()
            └── same path — autonomous strikes also fire the servo
```

## Module: `hydra_detect/servo_tracker.py`

### Class: `ServoTracker`

```python
class ServoTracker:
    """Pixel-lock servo controller — maps camera error to PWM output."""

    def __init__(
        self,
        mavlink: MAVLinkIO,
        *,
        # Pan servo
        pan_channel: int = 1,
        pan_pwm_center: int = 1500,
        pan_pwm_range: int = 500,
        pan_invert: bool = False,
        pan_dead_zone: float = 0.05,
        pan_smoothing: float = 0.3,
        # Strike servo
        strike_channel: int = 2,
        strike_pwm_fire: int = 1900,
        strike_pwm_safe: int = 1100,
        strike_duration: float = 0.5,
        # Integration
        replaces_yaw: bool = False,
    ): ...
```

### Pan Servo Mapping

Proportional mapping from normalised error to PWM:

```
error_x ∈ [-1.0, +1.0]   (target position relative to frame center)

smoothed = alpha * error_x + (1 - alpha) * prev_smoothed
           where alpha = pan_smoothing (0.3 default)

if abs(smoothed) < dead_zone:
    pwm = pan_pwm_center
else:
    offset = smoothed * pan_pwm_range
    if pan_invert: offset = -offset
    pwm = clamp(pan_pwm_center + offset, 500, 2500)

mavlink.set_servo(pan_channel, pwm)
```

**Why proportional, not rate-based:** The webcam is fixed (not mounted on the
servo). Proportional gives the most dramatic, intuitive demo: target walks left,
servo points left. Rate-based (`adjust_yaw`) is for vehicle steering where the
camera moves with the vehicle.

**Smoothing:** EMA with alpha=0.3 prevents jitter from frame-to-frame bbox noise
while keeping response snappy. Alpha closer to 1.0 = more responsive but
jittery. Alpha closer to 0.0 = smoother but sluggish.

### Strike Servo

On strike command:
1. Set strike channel to `strike_pwm_fire`
2. After `strike_duration` seconds, set to `strike_pwm_safe`
3. Uses existing `flash_servo()` pattern (daemon thread for the delay)

### Safety

- On init: strike servo set to `strike_pwm_safe`, pan servo centered
- On target unlock: pan servo returns to center
- On pipeline shutdown: both servos return to safe/center positions
- All PWM values clamped by existing `set_servo()` (500-2500 range)
- Strike servo uses same daemon thread pattern as light bar (no hot-loop blocking)

### Methods

```python
def update(self, error_x: float) -> None:
    """Update pan servo from pixel-lock error. Called every frame."""

def fire_strike(self) -> None:
    """Actuate strike servo (fire → safe after duration)."""

def safe(self) -> None:
    """Return all servos to safe positions. Called on unlock/shutdown."""

def get_status(self) -> dict:
    """Return current state for web API / logging."""
    # Returns: {"pan_pwm": 1500, "strike_armed": False, "error_x": 0.0, ...}

@property
def replaces_yaw(self) -> bool:
    """If True, pipeline should skip adjust_yaw() when servo tracking is active."""
```

## Config: `config.ini`

```ini
[servo_tracking]
enabled = false

# Pan servo — pixel-lock horizontal tracking
pan_channel = 1
pan_pwm_center = 1500
pan_pwm_range = 500
pan_invert = false
pan_dead_zone = 0.05
pan_smoothing = 0.3

# Strike servo — payload actuator
strike_channel = 2
strike_pwm_fire = 1900
strike_pwm_safe = 1100
strike_duration = 0.5

# When true, skip MAV_CMD_CONDITION_YAW (adjust_yaw) while servo tracking
# Use alongside (false) for vehicle + turret, or replace (true) for direct steering
replaces_yaw = false
```

## Pipeline Integration

### `pipeline.py` changes

**Constructor (`__init__`):** Build `ServoTracker` from config, same pattern as
`AutonomousController`:

```python
self._servo_tracker: ServoTracker | None = None
if self._cfg.getboolean("servo_tracking", "enabled", fallback=False):
    self._servo_tracker = ServoTracker(
        self._mavlink,
        pan_channel=self._cfg.getint("servo_tracking", "pan_channel", fallback=1),
        # ... all config knobs ...
    )
```

**Run loop (`_run_loop`):** In the locked-target block (after line 558), add
servo tracking alongside `adjust_yaw`:

```python
if current_lock_id is not None and self._mavlink is not None:
    locked_track = track_result.find(current_lock_id)
    if locked_track is not None:
        # ... compute error_x (existing) ...

        if current_lock_mode == "track":
            if self._servo_tracker is None or not self._servo_tracker.replaces_yaw:
                self._mavlink.adjust_yaw(error_x)
            if self._servo_tracker is not None:
                self._servo_tracker.update(error_x)
        elif current_lock_mode == "strike":
            if self._servo_tracker is None or not self._servo_tracker.replaces_yaw:
                self._mavlink.adjust_yaw(error_x, yaw_rate_max=15.0)
            if self._servo_tracker is not None:
                self._servo_tracker.update(error_x)
    else:
        # Target lost — existing auto-unlock logic
        # Also safe the servos:
        if self._servo_tracker is not None:
            self._servo_tracker.safe()
```

**Strike command (`_handle_strike_command`):** Add servo actuation after the
existing GUIDED waypoint logic:

```python
# After existing strike logic, fire the strike servo
if self._servo_tracker is not None:
    self._servo_tracker.fire_strike()
```

This means:
- **With MAVLink + GPS:** GUIDED waypoint fires AND strike servo fires
- **Without GPS:** GUIDED fails gracefully, strike servo still fires
- **Autonomous strikes:** Same path — `strike_cb` calls `_handle_strike_command`

**Target unlock (`_handle_target_unlock`):** Add servo safe:

```python
if self._servo_tracker is not None:
    self._servo_tracker.safe()
```

**Shutdown (`_shutdown`):** Add servo safe before closing MAVLink:

```python
if self._servo_tracker is not None:
    self._servo_tracker.safe()
```

## Web API

Expose servo status in the existing `/api/stats` response (no new endpoints):

```json
{
    "servo_tracking": {
        "enabled": true,
        "pan_channel": 1,
        "pan_pwm": 1500,
        "strike_channel": 2,
        "strike_armed": false,
        "error_x": 0.0,
        "replaces_yaw": false
    }
}
```

The existing Operations view already shows lock state and target info. The servo
PWM values will be visible in the Vehicle panel's stats. No new UI panels needed
for v1 — the servo is an actuator, not a new data source.

## Deployment Scenarios

### Bench Demo (instructor)

```
USB Webcam → Jetson → YOLO → ByteTrack → pixel-lock → set_servo
                                  ↓
                          Pixhawk 6C (UART)
                                  ↓
                          PWM Output Dongle
                                  ↓
                    Ch1: Pan Servo    Ch2: Strike Servo
```

Config: `replaces_yaw = true` (no vehicle, so skip yaw commands)

Demo flow:
1. Point webcam at a person
2. Hydra detects and tracks — light bar flashes, pan servo follows
3. Click "Lock" in web UI — servo actively pixel-locks on target
4. Click "Strike" — strike servo fires, red overlay blinks

### USV Field Deployment (Enforcer boat)

```
USB Webcam → Jetson → YOLO → ByteTrack → pixel-lock → set_servo (spotlight)
                                  ↓            ↓
                          adjust_yaw()    → vehicle heading
                                  ↓
                          Pixhawk 6C (UART)
                                  ↓
                    Ch1: Steering Servo    Ch4: Light Bar    Ch8: Spotlight Pan
```

Config: `replaces_yaw = false`, `pan_channel = 8` (spotlight, not steering)

Both `adjust_yaw()` and servo tracking run simultaneously — ArduPilot steers the
boat while a separate servo aims a spotlight at the target.

### UGV Direct Steering (Stampede rover)

```
USB Webcam → Jetson → YOLO → ByteTrack → pixel-lock → set_servo (steering)
                                  ↓
                          Pixhawk 6C (UART)
                                  ↓
                    Ch1: GroundSteering    Ch2: Payload Release
```

Config: `replaces_yaw = true`, `pan_channel = 1` (direct steering)

Hydra directly steers the rover toward the target. No GPS needed. Purely visual
closed-loop pursuit.

## Testing

### Unit tests (`tests/test_servo_tracker.py`)

1. **Proportional mapping:** error_x=0 → center PWM, error_x=1.0 → center+range,
   error_x=-1.0 → center-range
2. **Dead zone:** error_x within dead_zone → center PWM (no movement)
3. **Invert:** error_x=0.5 with invert=True → same magnitude, opposite direction
4. **Smoothing:** rapid error_x changes → output changes gradually
5. **Clamping:** extreme error_x → PWM stays within 500-2500
6. **Strike fire:** fires at fire_pwm, returns to safe after duration
7. **Safe:** both servos return to safe/center positions

### Integration tests

8. **Pipeline integration:** mock MAVLinkIO, verify set_servo called with correct
   channel and PWM values during locked tracking
9. **replaces_yaw=True:** verify adjust_yaw NOT called when servo tracking active
10. **replaces_yaw=False:** verify both adjust_yaw AND set_servo called
11. **Strike path:** verify strike servo fires during _handle_strike_command
12. **Autonomous path:** verify strike servo fires during autonomous strike
13. **Unlock:** verify servos return to safe on target unlock
14. **Shutdown:** verify servos return to safe on pipeline shutdown

### Hardware validation (bench)

15. Connect pan servo to PWM dongle ch1, move an object across webcam FOV
    → servo tracks left/right
16. Lock target in web UI → servo follows target specifically
17. Strike from web UI → strike servo pulses
18. Walk out of frame → target lost, servo centers, lock released
19. Adjust `pan_smoothing` in config → verify jitter vs responsiveness tradeoff
20. Test `pan_invert` → verify direction reverses

## Files Changed

| File | Change |
|------|--------|
| `hydra_detect/servo_tracker.py` | **NEW** — ServoTracker class |
| `hydra_detect/pipeline.py` | Build ServoTracker, integrate in run loop + strike + unlock + shutdown |
| `config.ini` | Add `[servo_tracking]` section (disabled by default) |
| `tests/test_servo_tracker.py` | **NEW** — unit + integration tests |

## Non-Goals (v1)

- **Vertical (tilt) servo:** Pan only for v1. Tilt adds a second axis and
  requires `error_y` mapping. Easy to add later with same pattern.
- **PID controller:** Proportional-only for v1. PID adds complexity without
  clear benefit for the proportional mapping use case (fixed camera, separate
  servo). Could add later if needed for camera-on-servo configurations.
- **Web UI servo controls:** No manual servo jog panel in v1. The servo is
  driven by pixel-lock, not by the operator. Config changes go through
  config.ini or the Settings page.
- **Multiple pan servos:** One pan channel for v1. Multi-axis turrets can be
  addressed in a future version.
