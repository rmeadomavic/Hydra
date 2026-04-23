---
name: adversarial
description: >
  Three-round adversarial analysis of a target (file, git range, PR, design
  doc). Round 1 pattern-matches known failures. Round 2 is prompted to
  disagree with Round 1. Round 3 looks for what both rounds missed —
  physics, emergent coupling, operator-adversarial use, absence of sim
  coverage. Use before merging changes to safety-critical paths, or when
  the user says "adversarial review", "red-team this", or "three rounds".
---

# /adversarial — Three-Round Adversarial Analysis

Existing review tooling (`safety-review`, `/review`, `simplify`, `grill-me`)
is single-pass and pattern-matched. That works for production patterns. It
does not work for Hydra's experimental surfaces — RF gradient ascent,
ByteTrack under identical uniforms, strike timing against real aerodynamics,
MAVLink priority inversion at 5 FPS — because those failures are physics or
emergent, not pattern-recognizable.

This skill forces three rounds with **fresh subagents** so Round 2 can
actually disagree with Round 1 instead of rationalizing it, and Round 3 can
find what both missed. The skill orchestrates; the `adversarial-review`
subagent does each round.

## When to invoke

- Before merging a PR that touches a path in `ADVERSARIAL_PATHS` (see
  `docs/adversarial/README.md`).
- When the user says "red-team this", "adversarial review", "three rounds",
  "what am I not seeing".
- After any change to physics/control code (`approach.py`, `guidance.py`,
  `autonomous.py`, `rf/navigator.py`, `rf/hunt.py`, `geo_tracking.py`,
  `dogleg_rtl.py`, `servo_tracker.py`).

## Arguments

The user's target is given as an argument. Parse in this order:
1. `#<N>` — GitHub PR number. Use `mcp__github__pull_request_read` to fetch
   diff and metadata.
2. `<a>..<b>` — git range. Use `git diff <a>..<b>` for the target.
3. A path (file or directory) relative to repo root. Target = current
   contents of that path.
4. Bare word or phrase — treat as a design-doc review; require the user to
   paste the doc if not previously in context.

If no argument is given, target = `git diff main...HEAD` (current branch's
changes vs main).

## Protocol

Run three rounds **serially**. Each round is a fresh `adversarial-review`
subagent call — do NOT share context between rounds. Inject prior-round
findings as adversarial input, not as a conclusion to accept.

### Round 1 — Pattern Match
Launch `adversarial-review` subagent with:
- `round: 1`
- `target`: the resolved target
- `prior_findings`: null

Round 1 delegates to existing tooling where possible: `safety-review` agent
for safety-critical diffs, `/review` style for broader codebase impact.
Produces structured findings: `{severity, category, file, line, claim,
evidence}`.

### Round 2 — Counter-Critique
Launch a fresh `adversarial-review` subagent with:
- `round: 2`
- `target`: same target
- `prior_findings`: Round 1's findings verbatim
- System prompt emphasizes: *argue Round 1 is wrong, misses the real risk,
  or is a decoy. Where would an adversary attack given Round 1's defenses
  are the obvious ones?*

Round 2's findings are second-order: things visible only once Round 1's
frame is rejected.

### Round 3 — Orthogonal Sweep
Launch a fresh `adversarial-review` subagent with:
- `round: 3`
- `target`: same target
- `prior_findings`: Round 1 + Round 2 findings
- System prompt emphasizes: *both prior rounds share a framing. Name the
  framing. Then find failures invisible to it — physics, emergent
  multi-module behavior, adversarial operator use, absence of ground-truth
  simulation.*

Round 3 must flag, as first-class findings:
- **No-sim coverage** — physics-dependent change without flight/RF sim test.
- **Emergent coupling** — safe in isolation, unsafe at another module's
  spec edge.
- **Operator-adversarial** — schema-valid but physically nonsensical config.
- **Consistency-bias signal** — R1 and R2 agreed on severity ranking; flag
  as suspicious.

### Consolidation

After Round 3 returns:
1. Merge findings into a single risk register, deduplicated, ranked by
   severity, tagged with originating round.
2. For each finding, emit one of: `blocker` (must fix before merge),
   `gate` (requires sim/field test before merge), `follow-up` (file an
   issue), `accepted` (documented risk).
3. Write the full report to
   `/tmp/reports/adversarial_<slug>_<timestamp>.md` using the structure
   below. `<slug>` is derived from the target (PR number, filename, or
   range).

## Report structure

```
# Adversarial Analysis — <target>
Generated: <ISO timestamp>
Target kind: <pr | range | path | design>

## Summary
- Round 1: <N> findings (<severity distribution>)
- Round 2: <N> findings, challenged <M> of R1
- Round 3: <N> findings, framing identified: <one sentence>
- Consolidated: <blocker count> blocker / <gate count> gate / <follow-up count> follow-up

## Round 1 — Pattern Match
<findings>

## Round 2 — Counter-Critique
Framing challenged: <what R2 pushed back on>
<findings>

## Round 3 — Orthogonal Sweep
Shared framing of R1+R2: <name the frame>
<findings, tagged by orthogonal category>

## Consolidated Risk Register
| Sev | Category | Tag | Claim | Evidence | Recommended gate |
| --- | --- | --- | --- | --- | --- |

## Recommended Gates
- Blocker: …
- Gate (needs sim/field): …
- Follow-up: …
- Accepted risk: …

## Known ceiling
Prompt-based analysis cannot catch: <list — e.g., numerical stability
under multipath, real-world aerodynamic timing>. If any consolidated
finding requires one of these, mark it `gate` and require a hypothesis
property test or field-data replay before merge.
```

## Invocation from the command line

The user runs:
- `/adversarial` → target = `git diff main...HEAD`
- `/adversarial hydra_detect/rf/navigator.py` → target = that file
- `/adversarial HEAD~3..HEAD` → target = git range
- `/adversarial #143` → target = PR 143

After the report is written, print its absolute path and a one-paragraph
summary to the user. Do not paste the full report into chat.

## Calibration

Seven canonical targets are documented in `docs/adversarial/README.md` with
expected R3 findings. If this skill is run against any of them and R3 does
not surface the documented finding, the R3 prompt in the subagent needs
tightening — note the gap in the report's `Known ceiling` section.

## What this skill does NOT do

- Fix anything. Reports only. Fixes are a separate, deliberate action the
  user approves per-finding.
- Run tests, start the Jetson, or deploy. Pure analysis.
- Replace `safety-review`, `/review`, or `grill-me`. Round 1 delegates to
  those where appropriate.
