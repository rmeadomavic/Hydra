# Adversarial Analysis — discipline, tooling, calibration

This directory exists because pattern-matched code review misses the bugs
that matter most in Hydra: physics failures, emergent multi-module coupling,
and operator-adversarial config. Existing review tooling (`safety-review`,
`/review`, `simplify`, `grill-me`) is single-pass and checklist-driven; it
catches known-bad patterns and misses unknown-bad physics.

The `/adversarial` skill (`.claude/skills/adversarial/SKILL.md`) forces
three rounds against a target, each run by a **fresh** subagent so the
second round can actually disagree with the first and the third can find
what both missed. This README is the operator-facing reference.

## When to run

Before merging a PR that touches any path in **ADVERSARIAL_PATHS**:

```
hydra_detect/pipeline.py
hydra_detect/pipeline/
hydra_detect/autonomous.py
hydra_detect/approach.py
hydra_detect/guidance.py
hydra_detect/mavlink_io.py
hydra_detect/rf/hunt.py
hydra_detect/rf/navigator.py
hydra_detect/rf/signal.py
hydra_detect/servo_tracker.py
hydra_detect/dogleg_rtl.py
hydra_detect/geo_tracking.py
```

The `adversarial-required` CI job detects touches to these paths and flags
PRs missing a report. On day one it is a **soft** check (red status, not a
required merge gate) while the team calibrates report quality. Flip it to
required in branch protection once R3 is consistently surfacing findings
the human review missed.

## How to run

From the repo root:

```
/adversarial                                   # diff main...HEAD
/adversarial hydra_detect/rf/navigator.py      # single file
/adversarial HEAD~3..HEAD                      # git range
/adversarial #143                              # GitHub PR
```

The skill writes the report to
`/tmp/reports/adversarial_<slug>_<timestamp>.md`. To attach to a PR, either:

1. Copy the report to `docs/adversarial/<pr-number>.md` and commit it, or
2. Link the `/tmp/reports/...` path in the PR body (only works for reports
   generated on the same machine the reviewer uses — option 1 is preferred
   for auditability).

## How to read a report

Each report has three rounds plus a consolidated register.

- **Round 1 (Pattern Match)** — baseline. Equivalent to what existing tools
  produce. If R1 is empty, the checklist tools are happy. Does not mean the
  change is safe.
- **Round 2 (Counter-Critique)** — argues against R1. Each R1 finding gets
  a one-sentence push-back; then R2 produces second-order findings
  assuming R1's defenses are the obvious targets an adversary would route
  around.
- **Round 3 (Orthogonal Sweep)** — names the framing both rounds shared
  and produces findings invisible to that framing. Four mandatory
  categories:
  - **No-sim coverage** — physics-dependent change without flight/RF sim
    test.
  - **Emergent coupling** — safe in isolation, unsafe at another module's
    spec edge.
  - **Operator-adversarial** — schema-valid but physically nonsensical
    config.
  - **Consistency-bias signal** — R1 and R2 agreed on severity ranking;
    flag as suspicious.

The consolidated register tags each finding with a **gate**:
- `blocker` — fix before merge.
- `gate` — requires sim/field test or property-based harness before merge.
- `follow-up` — file an issue; merge can proceed.
- `accepted` — known risk, documented and acknowledged.

## Known ceiling

Prompt-based reasoning cannot find:
- Numerical-stability bugs (gradient ascent oscillation in multipath,
  floating-point cancellation in geodetic projection).
- Real-time timing failures that only appear under Jetson load + MAVLink
  heartbeat pressure + detection at 5 FPS.
- ML model behavior shifts under adversarial inputs (adversarial patches,
  identical-uniform ID swaps).

When R3 flags one of these, the gate is `gate`, not `blocker`. Clearing
the gate requires an actual test — a `hypothesis` property test under
`tests/adversarial/`, a SITL replay, or field-data replay. The framework
flags these honestly rather than pretending it can reason its way through
them.

## Calibration set

These seven targets are known-dangerous surfaces where unit tests pass and
reviews approve but physics/operator reality fails. A release of the
`/adversarial` skill must surface at least five of the seven expected R3
findings when run against these targets. If it does not, the R3 prompt in
`adversarial-review` is still pattern-matching and needs tightening.

| # | Target | Expected R3 finding |
| --- | --- | --- |
| 1 | `hydra_detect/rf/navigator.py:153` | 8-bearing rotation has no oscillation damping or convergence timeout; multipath null → 5–10 min spin |
| 2 | `hydra_detect/rf/hunt.py:545,594` | GPS=None is guarded; GPS-drifted (spoofed / multipath) is not — poisons gradient ascent silently |
| 3 | `hydra_detect/guidance.py:123,139` | `target_bbox_ratio=0` → div/zero → NaN velocity; `max/min` clamp passes NaN through (NaN comparisons are always False) |
| 4 | `hydra_detect/approach.py:119-171` | `start_drop`/`start_strike` don't verify GUIDED mode before arming (invariant documented in `CLAUDE.md:380`); `start_pixel_lock:179-187` does — inconsistent |
| 5 | `hydra_detect/dogleg_rtl.py:145-154` | Hard-coded 60s offset-waypoint timeout → silent SMART_RTL from wrong position when wind/distance push beyond budget |
| 6 | `hydra_detect/geo_tracking.py:58-84` | GPS freshness / spoofing not checked; `tan(vfov/2)` unstable near nadir; reported target position degrades to garbage in urban canyons |
| 7 | `hydra_detect/pipeline/facade.py:1332-1350` | Watchdog skipped when `_cam_lost=True` (intentional, issue #122) but frozen / black-frame camera still ticks `_last_frame_time` → pipeline "healthy" while blind |

The common pattern: **schema passes, unit tests pass, code review passes,
physics or operator-adversarial reality does not.**

## Anti-consistency check

Run `/adversarial` twice against the same target. If R3 findings overlap
more than ~80%, the fresh-subagent isolation is leaking context or the R3
prompt is too deterministic. File an issue; tighten the prompt or vary
framing cues.

## What this framework is NOT

- Not a replacement for `safety-review`, `/review`, `simplify`, or
  `grill-me`. It sits on top; Round 1 delegates to them.
- Not an automatic fixer. Reports only. Fixes are deliberate, per-finding.
- Not a required merge gate yet. Soft status on day one.
- Not a general adversarial-ML framework. Scope is code/design review
  discipline, not model robustness.
