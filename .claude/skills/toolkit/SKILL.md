---
name: toolkit
description: Quick-reference card of all available skills, agents, and plugins. Use when the user asks "what tools do I have", "what skills are available", "what can you do", "show me my plugins", or when you need to recommend the right tool for a task. Also use proactively when starting a complex task to pick the best approach.
---

# Toolkit — Available Tools Reference

When invoked, present this reference card to the user. Tailor recommendations
to the current task if context is available.

## Project Skills (`/skillname`)

| Skill | Purpose | When to use |
|-------|---------|-------------|
| `/hydra` | Full Hydra Detect project context | Starting any Hydra work session |
| `/jetson-check` | Pre-flight hardware verification (9 checks) | Before hardware testing sessions |
| `/jetson-logs` | Fetch live application logs from Jetson | Debugging runtime errors or crashes |
| `/deploy-jetson` | Build Docker, restart service, validate | After code changes, deploying to Jetson |
| `/grill-me` | Stress-test a plan/design with relentless questioning | Before committing to an architecture decision |
| `/toolkit` | This reference card | When you need to know what's available |

## Superpowers Skills

| Skill | Purpose | When to use |
|-------|---------|-------------|
| `/brainstorming` | Collaborative design through Q&A | Before any creative/feature work |
| `/writing-plans` | Turn specs into step-by-step plans | After a design spec is approved |
| `/executing-plans` | Execute plans with review checkpoints | After a plan is written |
| `/dispatching-parallel-agents` | Run independent tasks simultaneously | 2+ non-overlapping tasks |
| `/subagent-driven-development` | Execute plan via background agents | Large plans with independent steps |
| `/frontend-design` | Production-grade UI implementation | Building web components or pages |
| `/feature-dev` | Guided feature development | New features needing codebase analysis |
| `/systematic-debugging` | Structured bug diagnosis | Any bug, test failure, or unexpected behavior |
| `/test-driven-development` | Write tests before code | Features with clear requirements |
| `/code-review` | Review a PR for issues | After completing implementation |
| `/verification-before-completion` | Verify before claiming done | Before committing or creating PRs |
| `/simplify` | Review code for quality/efficiency | After implementation, before merge |
| `/revise-claude-md` | Update CLAUDE.md with session learnings | End of productive sessions |
| `/grill-me` | Relentless design interview | Validating architecture decisions |
| `/skill-creator` | Create or improve skills | Building new workflow automations |

## Specialized Agents (background)

Dispatch via the Agent tool for parallel background work.

| Agent | Purpose | Trigger |
|-------|---------|---------|
| `code-reviewer` | Review code against plan + standards | After completing a feature |
| `code-explorer` | Deep codebase analysis, trace execution | Understanding unfamiliar code |
| `code-architect` | Design feature architectures | Planning new features |
| `safety-review` | Threading, memory, fail-safe audit | Before commits to safety-critical code |
| `config-audit` | Validate config.ini against schema | Before deploys or after config edits |
| `pre-field-test` | Go/No-Go report for field deployment | Before any field exercise |
| `perf-profile` | FPS, latency, RAM, GPU metrics | After model/config changes |
| `mavlink-diag` | MAVLink communication diagnosis | When MAVLink isn't working |
| `rf-diag` | RF hunt subsystem diagnosis | When RF hunt fails or gets stuck |

## MCP Plugins (external integrations)

| Plugin | What it connects to | Status |
|--------|-------------------|--------|
| **Todoist** | Task/project management | Auth needs reconnect |
| **Gmail** | Email drafts, search, read | Available |
| **Google Calendar** | Events, scheduling, free time | Available |
| **Hugging Face** | Models, datasets, training, papers | Available |
| **Mintlify** | Documentation site builder | Available |

## Recommended Pipelines

### New Feature
`/brainstorming` → `/grill-me` → `/writing-plans` → `/dispatching-parallel-agents` → `/code-review` → `/deploy-jetson`

### Bug Fix
`/systematic-debugging` → fix → `/verification-before-completion` → `/deploy-jetson`

### Hardware Session
`/jetson-check` → work → `/jetson-logs` (if issues) → `/deploy-jetson`

### UI/Frontend Work
`/brainstorming` → `/frontend-design` → `/code-review` → `/deploy-jetson`

### End of Session
`/revise-claude-md` → commit → push
