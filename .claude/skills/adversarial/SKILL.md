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

- Before merging a PR that touches a path in ADVERSARIAL_PATHS. The
  authoritative list lives in `.github/workflows/ci.yml` (the
  `adversarial-required` job); `docs/adversarial/README.md` mirrors it for
  reading. Do not maintain a separate list here.
- When the user says "red-team this", "adversarial review", "three rounds",
  "what am I not seeing".

## Arguments

The user's target is given as an argument. Parse in this order:
1. `#<N>` — GitHub PR number. When running laptop-side, use
   `mcp__github__pull_request_read` to fetch diff and metadata. When
   running on the Jetson (no GitHub MCP), fall back to
   `git fetch origin pull/<N>/head:pr-<N> && git diff main...pr-<N>` —
   the Jetson has git but not the Bindify-proxied MCP per CLAUDE.md's
   Partnership section.
2. `<a>..<b>` — git range. Use `git diff <a>..<b>` for the target.
3. A path (file or directory) relative to repo root. Target = current
   contents of that path.
4. Bare word or phrase — treat as a design-doc review; require the user to
   paste the doc if not previously in context.

If no argument is given, target = `git diff main...HEAD` (current branch's
changes vs main).

Before the first subagent call, ensure `/tmp/reports/` exists:
`mkdir -p /tmp/reports`. The skill writes reports there; committed reports
live at `docs/adversarial/<pr-number>.md` and are what CI checks.

## Protocol

Run three rounds **serially**. Each round is a fresh `adversarial-review`
subagent call — do NOT share context between rounds. Inject prior-round
findings as adversarial input, not as a conclusion to accept.

### Round 1 — Pattern Match
Launch `adversarial-review` subagent with:
- `round: 1`
- `target`: the resolved target
- `prior_findings`: null

The subagent cannot nest-invoke `safety-review` or `/review` (Claude Code
subagents don't support nested dispatch), so it applies the same checklists
directly. Produces structured findings: `{severity, category, file, line,
claim, evidence}`.

### Round 2 — Counter-Critique
Launch a fresh `adversarial-review` subagent with:
- `round: 2`
- `target`: same target
- `prior_findings`: Round 1's findings verbatim
- System prompt emphasizes: *for each R1 finding, take one of three
  positions — confirm with added evidence, challenge as wrong/decoy, or
  supersede (name the root cause). Do not manufacture disagreement on
  correct findings. Then: assume an adversary read R1's findings and
  knows where the defenses are. Where do they attack instead?*

Round 2's findings are second-order: things visible only once R1's frame
is questioned. Counter-rationalization on correct R1 findings is this
round's known failure mode — the subagent prompt warns against it
explicitly.

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
2. Tiebreak rules for conflicting rounds:
   - If R2 challenges an R1 blocker but R3 is silent on that finding,
     preserve the R1 finding at its original severity and add the R2
     challenge as a note. Do not silently drop it.
   - If R2 supersedes an R1 finding (same root cause, different diagnosis),
     keep the R2 version and annotate that it subsumes R1's claim.
   - If R2 confirms an R1 finding, merge into one entry citing both rounds.
3. For each entry, emit one of: `blocker` (must fix before merge),
   `gate` (requires sim/field test before merge), `follow-up` (file an
   issue), `accepted` (documented risk).
4. Write the full report to
   `/tmp/reports/adversarial_<slug>_<timestamp>.md` using the structure
   below. For PRs, also offer to copy the report to
   `docs/adversarial/<pr-number>.md` so CI's `adversarial-required` gate
   passes (requires >500 bytes and a `## Round 3` heading). `<slug>` is
   derived from the target (PR number, filename, or range).

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

Seven canonical targets are documented in `docs/adversarial/README.md`
with expected R3 findings. The release threshold is ≥5/7 across the full
set; individual misses on a single target are noted in `Known ceiling`,
not treated as failure. The README documents a known limitation: the
expected findings are published in-repo, which means a subagent may
retrieve rather than reason. Calibration measures "can the framework
describe what the calibration authors saw," not "can it discover what
nobody has seen." Treat the score as a floor, not a ceiling.

## What this skill does NOT do

- Fix anything. Reports only. Fixes are a separate, deliberate action the
  user approves per-finding.
- Run tests, start the Jetson, or deploy. Pure analysis.
- Replace `safety-review`, `/review`, or `grill-me`. It sits on top; Round
  1 applies the same checklists directly (subagents cannot nest-invoke).
