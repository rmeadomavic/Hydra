# Insights-Driven Improvements — Design Spec

**Date:** 2026-03-19
**Source:** Claude Code Insights report (402 messages, 13 sessions, 5 days)
**Goal:** Fix the top friction sources identified by usage analysis

## Problem

The insights report identified 3 friction categories:

1. **Trial-and-error debugging without research** (15 wrong-approach events) — Claude guesses at hardware fixes instead of researching first
2. **Incorrect hardware/protocol configuration** — Wrong serial ports (SERIAL3 vs SERIAL5), wrong protocols (33 vs 42), wrong MSP bytes
3. **Environment assumptions breaking deployments** — Models in wrong directory, pymavlink version mismatches, chmod vs udev

Root cause: CLAUDE.md lacks hardware-specific context, and repeating workflows aren't codified as skills.

## Changes

### 1. CLAUDE.md Additions

Append three new sections after the existing `## Common Commands` section.

#### `## Hardware Environment`

```markdown
## Hardware Environment

- **Architecture:** Jetson Orin Nano is ARM64/aarch64 — always check architecture
  compatibility before suggesting packages or tools
- **Packages:** Snap packages have known kernel compatibility issues on Jetson —
  prefer `apt` or `pip` installs when possible
- **Permissions:** Use udev rules for persistent `/dev/tty*` permissions, never
  `chmod` (resets on replug/reboot)
- **Models:** ML models belong in the `models/` directory, not the project root —
  always verify download destinations match what the code expects
```

#### `## Serial / MAVLink Conventions`

```markdown
## Serial / MAVLink Conventions

- `SERIAL5` = TELEM3 on this Pixhawk 6C setup
- HDZero DisplayPort protocol = **42** (not 33)
- ArduPilot does **NOT** support `ENCAPSULATED_DATA` messages
- Always verify serial port mappings against the `/hydra` skill or
  `docs/pixhawk-setup.md` (if it exists) before changing ArduPilot parameters
```

#### `## Debugging Rules`

```markdown
## Debugging Rules

- When facing unfamiliar system issues (snap, kernel modules, hardware protocols):
  **research first, fix second**
- Search project docs, git history, and reference materials before attempting any fix
- If your first two approaches fail, **STOP and ask the user** — they likely know
  the answer or can point to docs
- When spawning external processes (rtl_power, Kismet, etc.), always implement
  proper cleanup with `try/finally` or `atexit` handlers to prevent orphaned processes
- Before spawning a subprocess, check for existing instances (`pgrep`, `fuser`)
  to avoid dual-instance problems
```

### 2. Custom Skill: `/jetson-check`

**Location:** `.claude/skills/jetson-check/SKILL.md`
**Convention:** Subdirectory pattern (matches `/hydra` skill), allows companion files.

Pre-session hardware verification skill. Read-only — reports status, does not fix.

**SSH access:** `ssh sorcc@100.109.160.122` (Jetson via Tailscale). If SSH fails,
report the failure and stop — all subsequent checks require connectivity.

**Checks:**

1. SSH connectivity to Jetson via Tailscale
2. Hydra service status (`systemctl status hydra-detect`)
3. Serial device permissions (`/dev/ttyTHS1`, `/dev/ttyUSB*`) — verify udev rules
   exist in `/etc/udev/rules.d/`, not just current permissions
4. MAVLink connection — check if Hydra service logs show heartbeat activity
   (`journalctl -u hydra-detect --no-pager -n 50 | grep -i heartbeat`).
   Do NOT open the serial port directly as it may conflict with a running service.
5. Available disk space (`df -h /`)
6. Python environment has all dependencies (`pip check` in project venv)
7. Camera device detected (`ls /dev/video*`)
8. ML models present in `models/` directory (`ls ~/Hydra/models/*.pt 2>/dev/null`)

**Output format:** Markdown table with columns: Check | Status | Detail

```markdown
| Check | Status | Detail |
|-------|--------|--------|
| SSH | PASS | Connected to 100.109.160.122 |
| Service | FAIL | hydra-detect inactive |
| ... | ... | ... |
```

**SKILL.md content:**

```markdown
---
name: jetson-check
description: Pre-session Jetson hardware verification — checks connectivity, service, serial, disk, deps, camera, models
user-invocable: true
disable-model-invocation: false
---

# Jetson Pre-Flight Check

Run all checks via SSH to the Jetson (`ssh sorcc@100.109.160.122`).
If SSH fails, report the failure and stop.

This skill is **read-only** — it reports status but does not fix issues.
Present results as a markdown table (Check | Status | Detail).

## Checks

Run these via SSH commands:

1. **SSH** — verify connectivity (`ssh sorcc@100.109.160.122 echo ok`)
2. **Service** — `systemctl is-active hydra-detect`
3. **Serial perms** — check udev rules exist: `ls /etc/udev/rules.d/*tty* /etc/udev/rules.d/*serial* 2>/dev/null`, then check current perms on `/dev/ttyTHS1` and `/dev/ttyUSB*`
4. **MAVLink** — check service logs for heartbeat: `journalctl -u hydra-detect --no-pager -n 50 | grep -i heartbeat`. Do NOT open the serial port directly.
5. **Disk** — `df -h /` (warn if <10% free)
6. **Python deps** — `cd ~/Hydra && pip check 2>&1 | head -20`
7. **Camera** — `ls /dev/video* 2>/dev/null`
8. **Models** — `ls ~/Hydra/models/*.pt ~/Hydra/models/*.engine 2>/dev/null`

Report all results, then summarize: "X/8 checks passed. [list failures]"
```

### 3. Custom Skill: `/deploy-jetson`

**Location:** `.claude/skills/deploy-jetson/SKILL.md`
**Convention:** Subdirectory pattern.

**SSH access:** `ssh sorcc@100.109.160.122` (Jetson via Tailscale).

End-to-end deploy-and-validate workflow:

1. SSH to Jetson via Tailscale
2. Show current vs incoming changes: `git log --oneline -1` vs `git log --oneline origin/main -1`
3. `cd ~/Hydra && git pull origin main`
4. `pip install -r requirements.txt` (always run — pip is fast when nothing changed)
5. Restart Hydra service (`sudo systemctl restart hydra-detect`)
6. Run smoke tests: service active, web UI responding (`curl -s http://localhost:8080`), camera device detected

If step 5 fails, report the pre-pull commit hash so the user can revert with
`git checkout <hash>`.

**SKILL.md content:**

```markdown
---
name: deploy-jetson
description: Deploy latest code to Jetson — pull, install deps, restart service, validate
user-invocable: true
disable-model-invocation: false
---

# Deploy to Jetson

Deploy and validate Hydra on the Jetson via SSH (`ssh sorcc@100.109.160.122`).

## Steps

Run each step via SSH. Report pass/fail for each.

1. **Connect** — `ssh sorcc@100.109.160.122 echo ok`
2. **Pre-deploy snapshot** — record current commit: `cd ~/Hydra && git rev-parse --short HEAD`
3. **Show changes** — `git log --oneline HEAD..origin/main` (after `git fetch`)
4. **Pull** — `git pull origin main`
5. **Install deps** — `pip install -r requirements.txt`
6. **Restart service** — `sudo systemctl restart hydra-detect`
7. **Validate** — wait 5 seconds, then:
   - `systemctl is-active hydra-detect`
   - `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080` (expect 200)
   - `ls /dev/video* 2>/dev/null`

If restart fails, report: "Service failed. Pre-deploy commit was `<hash>`.
Revert with: `git checkout <hash>`"

Report status of each step, then summarize overall result.
```

## What This Does NOT Change

- **Hooks** — The existing `lint-python.sh` PostToolUse hook already covers post-edit syntax/type checking. No new hooks needed.
- **Settings** — No changes to `settings.json` or `settings.local.json`.
- **Existing skills** — `/hydra` and `/review` are unchanged.

## Success Criteria

- Claude stops using wrong serial port numbers (SERIAL3 vs SERIAL5)
- Claude stops using wrong protocol numbers (33 vs 42)
- Claude researches before attempting hardware fixes instead of guessing
- User can run `/jetson-check` at session start instead of manually explaining verification steps
- User can run `/deploy-jetson` instead of walking through SSH+pull+restart each time
