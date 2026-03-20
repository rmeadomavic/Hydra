# Insights-Driven Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hardware context to CLAUDE.md and create two custom skills to eliminate the top friction sources from usage insights.

**Architecture:** Three independent changes — append sections to an existing markdown file, create two new skill directories with SKILL.md files. No code logic, no tests, no dependencies between tasks.

**Tech Stack:** Markdown files only. Claude Code skills framework (SKILL.md with YAML frontmatter).

**Spec:** `docs/superpowers/specs/2026-03-19-insights-improvements-design.md`

---

### Task 1: Append Hardware Environment, Serial Conventions, and Debugging Rules to CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (append after line 110, the closing ``` of Common Commands)

- [ ] **Step 1: Append the three new sections to CLAUDE.md**

Add the following after the closing ``` of the `## Common Commands` section (after line 110):

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

## Serial / MAVLink Conventions

- `SERIAL5` = TELEM3 on this Pixhawk 6C setup
- HDZero DisplayPort protocol = **42** (not 33)
- ArduPilot does **NOT** support `ENCAPSULATED_DATA` messages
- Always verify serial port mappings against the `/hydra` skill or
  `docs/pixhawk-setup.md` (if it exists) before changing ArduPilot parameters

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

- [ ] **Step 2: Verify the file reads correctly**

Read `CLAUDE.md` and confirm:
- The three new sections appear after `## Common Commands`
- No formatting issues or duplicate sections
- Existing content is untouched

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add hardware environment, serial conventions, and debugging rules to CLAUDE.md"
```

---

### Task 2: Create `/jetson-check` skill

**Files:**
- Create: `.claude/skills/jetson-check/SKILL.md`

- [ ] **Step 1: Create the skill directory and file**

Create `.claude/skills/jetson-check/SKILL.md` with this exact content:

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

- [ ] **Step 2: Verify the skill file**

Read `.claude/skills/jetson-check/SKILL.md` and confirm:
- YAML frontmatter has all 4 fields (name, description, user-invocable, disable-model-invocation)
- All 8 checks are listed with specific commands
- Output format instruction is present

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/jetson-check/SKILL.md
git commit -m "feat: add /jetson-check skill for pre-session hardware verification"
```

---

### Task 3: Create `/deploy-jetson` skill

**Files:**
- Create: `.claude/skills/deploy-jetson/SKILL.md`

- [ ] **Step 1: Create the skill directory and file**

Create `.claude/skills/deploy-jetson/SKILL.md` with this exact content:

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

- [ ] **Step 2: Verify the skill file**

Read `.claude/skills/deploy-jetson/SKILL.md` and confirm:
- YAML frontmatter has all 4 fields
- All 7 steps are listed with specific commands
- Rollback guidance is present
- Validation step includes all 3 checks (service, web UI, camera)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/deploy-jetson/SKILL.md
git commit -m "feat: add /deploy-jetson skill for remote deploy and validation"
```
