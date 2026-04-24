# Adversarial Analysis — discipline, tooling, calibration

This directory exists because pattern-matched code review misses the bugs
that matter most in Hydra: physics failures, emergent multi-module coupling,
and operator-adversarial config. Existing review tooling (`safety-review`,
`/review`, `simplify`, `grill-me`) is single-pass and checklist-driven; it
catches known-bad patterns and misses unknown-bad physics.

The `/adversarial` skill (`.claude/skills/adversarial/SKILL.md`) forces
three rounds against a target, each run by a **fresh** subagent so the
second round can challenge or supersede the first and the third can find
what both missed. This README is the operator-facing reference.

## When to run

Before merging a PR that touches any path in **ADVERSARIAL_PATHS**. The
authoritative list lives in the `adversarial-required` job of
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) and is the
single source of truth — do not maintain a copy here. At time of writing
it covers the hot detection loop (`hydra_detect/pipeline/`), autonomous
+ approach + guidance controllers, MAVLink I/O, RF hunt + navigator +
signal, servo tracker, dogleg RTL, geo-tracking, web server (vehicle
control endpoints), and TAK input (GeoChat commands).

The `adversarial-required` CI job detects touches to those paths and
flags PRs missing a report. Day-one behavior is a **soft** check (red
status, not a required merge gate). To clear the check:

- Commit the report to `docs/adversarial/<pr-number>.md`. CI requires
  the file to be **>500 bytes** AND to contain a `## Round 3` heading.
  An empty file or a copy of another PR's partial output will not pass.

Flip to a required check in branch protection once the team observes,
for at least five consecutive PRs that touch ADVERSARIAL_PATHS, that R3
surfaces at least one finding the human review missed. Owner for that
transition: the fleet lead. Revocation: same person, if false-
positive rate exceeds one per PR over a two-week window.

## How to run

From the repo root:

```
/adversarial                                   # diff main...HEAD
/adversarial hydra_detect/rf/navigator.py      # single file
/adversarial HEAD~3..HEAD                      # git range
/adversarial #143                              # GitHub PR
```

The skill writes the draft report to
`/tmp/reports/adversarial_<slug>_<timestamp>.md`. To attach to a PR,
commit a copy to `docs/adversarial/<pr-number>.md`. The `/tmp/` path
is reviewer-machine-local and not auditable; CI only accepts the
committed file.

## How to read a report

Each report has three rounds plus a consolidated register.

- **Round 1 (Pattern Match)** — baseline. Equivalent to what existing tools
  produce. If R1 is empty, the checklist tools are happy. Does not mean
  the change is safe.
- **Round 2 (Counter-Critique)** — for each R1 finding, the subagent
  either **confirms** with added evidence, **challenges** as wrong/decoy,
  or **supersedes** (same symptom, different root cause). Does not
  manufacture disagreement on correct findings. Then produces
  independent second-order findings assuming an adversary has read R1's
  findings and routes around its defenses.
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

Consolidation tiebreaks: if R2 challenges an R1 blocker and R3 is silent,
preserve R1 at its original severity with the R2 challenge as a note.
If R2 supersedes R1 (same root cause, different diagnosis), keep R2 and
annotate it subsumes R1. If R2 confirms R1, merge into one entry citing
both rounds. These rules are enforced by the skill's consolidation step.

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
flags these honestly rather than pretending it can reason its way
through them.

## Calibration set

These seven targets are known-dangerous surfaces where unit tests pass
and reviews approve but physics/operator reality fails. A release of the
`/adversarial` skill is expected to surface at least five of the seven
expected R3 findings when run across the full set. Individual misses on
one or two targets are noted in `Known ceiling` rather than treated as
failure.

**Self-leak warning.** The expected findings below are published in the
same git tree the subagent can read, so a subagent may retrieve the
answer via repo search rather than reason to it. Calibration measures
"can the framework describe what the calibration authors saw" — it does
not measure "can it discover what nobody has seen." Treat the score as a
floor, not a ceiling. Running `/adversarial` against novel targets
(random PRs, unreviewed modules) is the real test.

| # | Target | Expected R3 finding |
| --- | --- | --- |
| 1 | `hydra_detect/rf/navigator.py` | 8-bearing rotation has no oscillation damping or convergence timeout; multipath null → 5–10 min spin |
| 2 | `hydra_detect/rf/hunt.py` | GPS=None is guarded; GPS-drifted (spoofed / multipath) is not — poisons gradient ascent silently |
| 3 | `hydra_detect/guidance.py` | `target_bbox_ratio=0` → div/zero → NaN velocity; `max/min` clamp passes NaN through (NaN comparisons are always False) |
| 4 | `hydra_detect/approach.py` | `start_drop`/`start_strike` don't verify GUIDED mode before arming; `start_pixel_lock` does — inconsistent |
| 5 | `hydra_detect/dogleg_rtl.py` | Hard-coded 60s offset-waypoint timeout → silent SMART_RTL from wrong position when wind/distance push beyond budget |
| 6 | `hydra_detect/geo_tracking.py` | GPS freshness / spoofing not checked; `tan(vfov/2)` unstable near nadir; reported target position degrades to garbage in urban canyons |
| 7 | `hydra_detect/pipeline/facade.py` | Watchdog skipped when `_cam_lost=True` but frozen / black-frame camera still ticks `_last_frame_time` → pipeline "healthy" while blind |

Line numbers change as the codebase moves. Point the skill at the file;
trust the subagent to locate the current line.

The common pattern: **schema passes, unit tests pass, code review passes,
physics or operator-adversarial reality does not.**

## Anti-consistency check

Run `/adversarial` twice against the same target. If R3 findings overlap
more than ~80%, the fresh-subagent isolation is leaking context or the
R3 prompt is too deterministic. File an issue; tighten the prompt or
vary framing cues.

This check is currently manual; automation is tracked as a follow-up in
the PR #144 merge notes.

## What this framework is NOT

- Not a replacement for `safety-review`, `/review`, `simplify`, or
  `grill-me`. It sits on top; R1 applies the same checklists directly
  (subagents cannot nest-invoke, so delegation is by convention).
- Not an automatic fixer. Reports only. Fixes are deliberate, per-finding.
- Not a required merge gate yet. Soft status on day one; see "When to
  run" for the flip criteria.
- Not a general adversarial-ML framework. Scope is code/design review
  discipline, not model robustness.
- Not a substitute for thinking. The framework is a forcing function for
  an adversarial posture; the analysis quality is still bounded by the
  reviewer's (human or model) reasoning.
