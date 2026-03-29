# Hydra Autonomous Parameter Auto-Tuner — Design Spec

**Date:** 2026-03-29
**Status:** Approved
**Scope:** Standalone optimizer that tunes Hydra's guidance, approach, and tracking
parameters against ArduPilot SITL using synthetic target injection and Bayesian
optimization. Drone (ArduCopter) first, extensible to USV/UGV/fixed-wing.

---

## Problem

Hydra has ~17 tunable parameters that control how the vehicle follows, approaches,
and engages targets. These interact nonlinearly — high yaw gain with low deadzone
causes oscillation, conservative forward gain with aggressive speed limits wastes
time. Each SORCC platform (drone, boat, rover, fixed-wing) has different dynamics.
Manual field tuning is slow, risky, and not repeatable.

## Solution

A Bayesian optimization loop (Optuna) that:
1. Proposes candidate parameter sets
2. Runs each candidate in a headless ArduPilot SITL trial with synthetic targets
3. Measures fitness (strike success, speed, stability, accuracy, efficiency)
4. Converges on optimal parameters per platform and scenario

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  autotune.py                     │
│                                                  │
│  Optuna Study (TPE sampler, SQLite storage)      │
│    │                                             │
│    ├─ suggest_params() → candidate config.ini    │
│    │                                             │
│    ├─ TrialRunner                                │
│    │   ├─ spawn SITL (sim_vehicle.py)            │
│    │   ├─ spawn Hydra (--sim + candidate config) │
│    │   ├─ inject synthetic target                │
│    │   ├─ monitor telemetry (2nd MAVLink conn)   │
│    │   ├─ collect metrics until done             │
│    │   └─ cleanup processes (try/finally)        │
│    │                                             │
│    ├─ compute_fitness(metrics) → score           │
│    └─ report trial to Optuna                     │
│                                                  │
│  After N trials:                                 │
│    └─ export best config per scenario            │
└─────────────────────────────────────────────────┘
```

## Components

### 1. Parameter Space

The tunable knobs Optuna explores. Ranges are informed by physical plausibility
and current defaults.

| Parameter | Range | Type | Default |
|-----------|-------|------|---------|
| `guidance.fwd_gain` | 0.5 — 5.0 | float | 2.0 |
| `guidance.lat_gain` | 0.5 — 5.0 | float | 1.5 |
| `guidance.vert_gain` | 0.3 — 3.0 | float | 1.0 |
| `guidance.yaw_gain` | 5.0 — 60.0 | float | 30.0 |
| `guidance.max_fwd_speed` | 1.0 — 10.0 | float | 5.0 |
| `guidance.max_lat_speed` | 0.5 — 5.0 | float | 2.0 |
| `guidance.max_yaw_rate` | 10.0 — 90.0 | float | 45.0 |
| `guidance.deadzone` | 0.01 — 0.15 | float | 0.05 |
| `guidance.target_bbox_ratio` | 0.05 — 0.3 | float | 0.15 |
| `approach.follow_speed_min` | 0.5 — 5.0 | float | 2.0 |
| `approach.follow_speed_max` | 3.0 — 15.0 | float | 10.0 |
| `approach.follow_distance_m` | 5.0 — 30.0 | float | 15.0 |
| `tracker.track_thresh` | 0.3 — 0.8 | float | 0.5 |
| `tracker.track_buffer` | 10 — 60 | int | 30 |
| `tracker.match_thresh` | 0.5 — 0.95 | float | 0.8 |
| `autonomous.min_confidence` | 0.5 — 0.95 | float | 0.85 |
| `autonomous.min_track_frames` | 2 — 15 | int | 5 |

17 parameters total. Optuna's TPE sampler handles this dimensionality well
(validated up to ~50 dimensions in literature).

Parameters NOT tuned (fixed for safety):
- `geofence_*` — safety boundary, not a performance knob
- `strike_cooldown_sec` — operational policy
- `allowed_classes` — mission-specific
- `require_operator_lock` — safety policy
- `min_altitude_m` — hard floor

### 2. Synthetic Target Injector

Module: `tools/autotune/injector.py`

Creates fake `TrackedObject` data as if YOLO detected a target, bypassing the
detector entirely. This isolates control loop tuning from model accuracy.

**Behavior:**
- Places a virtual target at a known GPS offset from SITL's current position
- Computes pixel-space bounding box based on vehicle-to-target distance and
  camera HFOV geometry:
  ```
  apparent_size = (real_target_width / distance) * (frame_width / 2) / tan(hfov/2)
  bbox_center = project_gps_to_pixel(target_gps, vehicle_gps, vehicle_heading, hfov)
  ```
- As the vehicle approaches, the bbox grows naturally (target gets larger in frame)
- Feeds `TrackedObject` directly into the pipeline's tracking/approach path
- Configurable noise and occlusion:
  - `jitter_px`: random bbox position noise (default 0)
  - `occlusion_prob`: probability of dropping a frame (default 0.0)
  - `confidence_range`: [min, max] detection confidence (default [0.85, 0.95])

**Interface:**
```python
class SyntheticInjector:
    def __init__(self, target_gps: tuple[float, float],
                 target_class: str = "person",
                 real_width_m: float = 0.5,
                 jitter_px: float = 0.0,
                 occlusion_prob: float = 0.0)

    def generate(self, vehicle_gps: tuple[float, float],
                 vehicle_heading: float,
                 frame_size: tuple[int, int],
                 hfov_deg: float) -> TrackedObject | None:
        """Returns a TrackedObject or None (occlusion)."""
```

### 3. Trial Runner

Module: `tools/autotune/runner.py`

Manages the lifecycle of a single evaluation trial.

**Sequence:**
1. Write candidate `config.ini` to `/tmp/autotune/trial_{id}/config.ini`
2. Start SITL: `sim_vehicle.py -v ArduCopter --no-mavproxy -I {id}`
   on ports `5760 + id*10` (SITL) and `14550 + id` (monitor)
3. Wait for SITL heartbeat (timeout 30s)
4. Arm and takeoff to scenario altitude via MAVLink commands
5. Wait for altitude reached (within 2m, timeout 30s)
6. Start Hydra with `--sim --config /tmp/autotune/trial_{id}/config.ini`
   with MAVLink pointed at the SITL instance
7. After `inject_after_sec`, begin feeding synthetic targets
8. Record telemetry at 10 Hz: lat, lon, alt, heading, speed, mode, armed
9. End trial on first of:
   - Strike event fired (success)
   - `timeout_sec` elapsed (failure)
   - Geofence breach (safety violation — pruned)
   - Altitude below `min_altitude_m` (safety violation — pruned)
   - SITL crash / connection lost (infrastructure error — retry)
10. Kill Hydra and SITL processes in `try/finally`
11. Return `TrialMetrics` dataclass

**Port allocation:**
Each trial gets unique ports to enable future parallelism:
- SITL TCP: `5760 + trial_id * 10`
- Monitor UDP: `14550 + trial_id`
- Hydra web (unused but required): `8080 + trial_id`

**Process cleanup:**
Both SITL and Hydra are tracked by PID. The runner uses `try/finally` with
`process.terminate()` then `process.wait(timeout=5)` then `process.kill()`
to guarantee no orphans. An `atexit` handler catches unexpected exits.

### 4. Telemetry Monitor

Module: `tools/autotune/monitor.py`

A lightweight MAVLink consumer that connects to SITL independently of Hydra's
connection and records vehicle state.

**Recorded at 10 Hz:**
```python
@dataclass
class TelemetryFrame:
    timestamp: float
    lat: float
    lon: float
    alt: float
    heading: float
    groundspeed: float
    mode: str
    armed: bool
```

**Derived metrics (computed post-trial):**
```python
@dataclass
class TrialMetrics:
    success: bool               # strike event reached target
    time_to_engagement: float   # seconds from inject to strike
    track_stability: float      # fraction of frames with stable track
    position_error_m: float     # distance from target at strike moment
    path_efficiency: float      # straight_line_distance / actual_path_distance
    oscillation_count: int      # heading reversals > 15 degrees
    geofence_breach: bool       # hard constraint
    altitude_violation: bool    # hard constraint
    safety_fault: bool          # any safety gate triggered
```

### 5. Fitness Function

Module: `tools/autotune/fitness.py`

```python
def compute_fitness(metrics: TrialMetrics) -> float:
    # Hard constraints — pruned trial
    if metrics.geofence_breach or metrics.altitude_violation or metrics.safety_fault:
        return 0.0

    # Normalize time: 0.0 = timeout (120s), 1.0 = instant
    time_norm = max(0.0, 1.0 - metrics.time_to_engagement / 120.0)

    # Normalize position error: 0.0 = 10m+ off, 1.0 = dead on
    error_norm = max(0.0, 1.0 - metrics.position_error_m / 10.0)

    # Oscillation penalty: 0 reversals = 1.0, 10+ = 0.0
    osc_penalty = max(0.0, 1.0 - metrics.oscillation_count / 10.0)

    score = (
        0.30 * (1.0 if metrics.success else 0.0)   # Did it reach the target?
        + 0.25 * time_norm                           # How fast?
        + 0.20 * metrics.track_stability             # How stable was the lock?
        + 0.10 * error_norm                          # How accurate at terminal?
        + 0.10 * metrics.path_efficiency             # How direct was the path?
        + 0.05 * osc_penalty                         # How smooth?
    )
    return score
```

Weights are configurable via CLI flags for experimentation.

### 6. Scenario Definitions

Directory: `tools/autotune/scenarios/`

Each scenario is a YAML file describing initial conditions and success criteria:

```yaml
# scenarios/follow_strike_50m.yaml
name: "follow_and_strike_50m"
description: "Target 50m north, direct approach"
vehicle:
  type: ArduCopter
  start_lat: 35.0527
  start_lon: -79.4927
  start_alt_m: 30.0
  start_mode: GUIDED
target:
  offset_north_m: 50.0
  offset_east_m: 0.0
  class: "person"
  real_width_m: 0.5
  inject_after_sec: 5.0
  jitter_px: 0.0
  occlusion_prob: 0.0
timeout_sec: 120
success_criteria:
  max_distance_m: 3.0
```

**Planned scenarios (start with these, add more):**

| Scenario | Description |
|----------|-------------|
| `follow_strike_50m.yaml` | Direct approach, 50m, no noise |
| `follow_strike_100m.yaml` | Longer approach, tests speed tuning |
| `follow_strike_crosswind.yaml` | Target 50m north + 30m east, tests lateral correction |
| `follow_strike_noisy.yaml` | 50m, jitter_px=20, occlusion_prob=0.1 |
| `follow_strike_close.yaml` | Target 15m away, tests stopping behavior |

When multiple scenarios are specified, the trial runs each and averages scores.
This prevents overfitting to a single geometry.

### 7. Output

Directory: `results/autotune/{study_name}/`

| File | Contents |
|------|----------|
| `study.db` | Optuna SQLite study — reusable, supports resuming |
| `best_drone.ini` | Config fragment with best parameters |
| `history.csv` | All trials: params, metrics, scores |
| `convergence.png` | Score vs trial number |
| `param_importance.png` | Optuna's fANOVA parameter importance |
| `contour.png` | 2D contour plots for top parameter pairs |

### 8. CLI Interface

```bash
# Run 100 trials on the default scenario
python -m tools.autotune --trials 100 --scenario scenarios/follow_strike_50m.yaml

# Run multiple scenarios (averaged fitness)
python -m tools.autotune --trials 200 --scenario scenarios/*.yaml

# Resume a previous study
python -m tools.autotune --resume results/autotune/study_2026-03-29/study.db --trials 50

# Export best config
python -m tools.autotune --export results/autotune/study_2026-03-29/study.db

# Parallel trials (future — design for this now)
python -m tools.autotune --trials 200 --workers 4 --scenario scenarios/*.yaml
```

## Prerequisites

- **ArduPilot SITL** installed with `sim_vehicle.py` in PATH
  - Install: `git clone https://github.com/ArduPilot/ardupilot && Tools/environment_install/install-prereqs-ubuntu.sh`
- **Python packages** (added to a `tools/autotune/requirements.txt`):
  - `optuna>=3.0`
  - `pymavlink>=2.4`
  - `pyyaml`
  - `matplotlib`
- **Hydra codebase** accessible as a Python package (run from repo root)

## Design for Future Extension

### Multi-platform (Phase 2)
- Add `ArduRover`, `ArduPlane` vehicle types to scenarios
- Vehicle-specific parameter spaces (e.g., USV has no `vert_gain`)
- Per-platform Optuna studies stored separately
- Platform dynamics differ enough that transfer learning between studies
  is unlikely to help — each platform gets its own optimization run

### Distributed mode (Phase 2)
- Replace SQLite with PostgreSQL-backed Optuna storage
- Multiple `--workers` each run independent SITL instances on offset ports
- Optuna handles trial distribution natively via its storage backend
- The `TrialRunner` already uses unique ports per trial ID, so parallelism
  is a configuration change, not an architecture change

### CI integration (Phase 3)
- Nightly GitHub Actions workflow runs 50 trials on the current main branch
- Uploads `best_drone.ini` and convergence plots as artifacts
- Regression detection: if best score drops >10% from previous run, flag it

## Constraints

- **No GPU required** — SITL is CPU-only, synthetic targets bypass YOLO
- **Memory:** ~200 MB per SITL instance + ~100 MB for Hydra (no model loaded)
- **Disk:** SQLite study + CSV history, negligible
- **Time estimate:** ~30-60s per trial, so 100 trials = ~1-1.5 hours single-threaded
- **No modifications to Hydra core** — the injector feeds data through the existing
  pipeline interface. The only Hydra change needed is exposing a hook for synthetic
  target injection in the pipeline (a callback or a test-mode flag).

## Files to Create

```
tools/
  autotune/
    __init__.py
    __main__.py          # CLI entry point
    optimizer.py         # Optuna study management
    runner.py            # TrialRunner — process lifecycle
    injector.py          # SyntheticInjector — fake TrackedObject
    monitor.py           # Telemetry recorder
    fitness.py           # Fitness function
    config_writer.py     # Generate candidate config.ini from params
    scenarios/
      follow_strike_50m.yaml
      follow_strike_100m.yaml
      follow_strike_crosswind.yaml
      follow_strike_noisy.yaml
      follow_strike_close.yaml
    requirements.txt
```

## Files to Modify

- `hydra_detect/pipeline.py` — Add a `inject_detection` method or
  `--synthetic-target` flag that accepts TrackedObject data from an external
  source instead of the detector. This is the only core change needed.
