# Design: `hydra-setup.sh` — One-Script Jetson Setup

**Date:** 2026-03-16
**Status:** Approved

## Purpose

A single interactive bash script (`scripts/hydra-setup.sh`) that takes a freshly
cloned Jetson from zero to running Hydra Detect. Idempotent — safe to re-run,
skips steps already completed.

Target audience: instructors reproducing the Hydra build from scratch after
completing JetPack flash and Ubuntu first-boot.

## Flow

### Step 1 — Preflight Checks

Embedded checks (not calling `jetson_preflight.sh` — that stays as a standalone
field-check tool):

- Python3, pip
- Docker installed, user in `docker` group
- `nvidia-container-toolkit` installed
- User in `dialout` group (serial access)
- Camera devices (`/dev/video*`) detected and reported
- Serial devices (`/dev/ttyACM*`, `/dev/ttyUSB*`) detected and reported

If group membership is missing, offer to add (`sudo usermod -aG`) and warn
that a logout/login is required for the change to take effect.

### Step 2 — Tailscale (Optional)

- **Already installed and running:** Print Tailscale IP, skip.
  ```
  [PASS] Tailscale already running (100.109.160.122)
  ```
- **Not installed:** Prompt: `"Would you like to set up Tailscale for remote SSH? [Y/n]"`
  - **Yes:**
    1. Install via official Tailscale install script (`curl -fsSL https://tailscale.com/install.sh | sh`)
    2. Run `sudo tailscale up`
    3. Print the auth URL for the instructor to open in a browser
    4. Wait for connection to establish
    5. Enable Tailscale SSH: `sudo tailscale set --ssh`
    6. Print the assigned Tailscale IP
  - **No:** Skip, print note that remote SSH won't be available.

Tailscale installs on the **host OS**, not inside Docker. SSH needs to reach the
Jetson directly.

### Step 3 — Docker Base Image

- If `dustynv/l4t-pytorch:r36.4.0` already pulled: skip.
- If not: `docker pull dustynv/l4t-pytorch:r36.4.0`

### Step 4 — Docker Build

- If `hydra-detect:latest` image exists: prompt `"Rebuild Hydra image? [y/N]"`
- If not: `docker build --network=host -t hydra-detect:latest .`

### Step 5 — Directories

Create if missing:
- `models/`
- `output_data/`

### Step 6 — Config

Detect hardware and prompt the instructor:

- **Pixhawk detected** (`/dev/ttyACM0` exists):
  `"Pixhawk detected on /dev/ttyACM0. Enable MAVLink? [Y/n]"`
  - Yes: set `[mavlink] enabled = true`, `connection_string = /dev/ttyACM0`
  - No: set `[mavlink] enabled = false`
- **No Pixhawk:**
  `"No flight controller found. Disabling MAVLink. [Enter to continue]"`
  - Set `[mavlink] enabled = false`
- **Camera:** Report which `/dev/video*` devices are present (informational only,
  config.ini default of `auto` handles device selection).

Only the `[mavlink] enabled` and `connection_string` fields are modified.
All other config.ini settings remain at defaults.

### Step 7 — Test Run (Optional)

- Prompt: `"Ready to launch Hydra? [Y/n]"`
- **Yes:** Run the full Docker command with appropriate device mounts,
  print dashboard URL (`http://<ip>:8080`).
- **No:** Print the docker run command so the instructor can run it later.

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Tailscale on host, not in Docker | SSH needs to reach the Jetson OS directly |
| Tailscale SSH via `tailscale set --ssh` | No SSH key management needed for instructors |
| No pre-auth keys | Simpler — instructor clicks auth URL once |
| Idempotent steps | Safe to re-run after failure, reboot, or when re-checking setup |
| Separate from `jetson_preflight.sh` | Preflight stays as a lightweight field-check tool |
| Only touch MAVLink config | Avoids overwriting instructor customizations in config.ini |
| `set -euo pipefail` | Fail fast on errors, no silent failures |

## Files Changed

| File | Action |
|------|--------|
| `scripts/hydra-setup.sh` | **New** — the main setup script |
| `docs/tailscale-ssh.md` | **New** — reference doc: what Tailscale SSH does, how to connect, troubleshooting |
| `README.md` | **Update** — Getting Started section points to `hydra-setup.sh` |
| `docs/jetson-setup-guide.md` | **Update** — reference the script as the recommended path |

## Out of Scope

- JetPack flashing (covered by `docs/jetson-initial-setup.md`)
- YOLO model downloads (auto-download on first run)
- Advanced config: autonomous, RF homing, OSD (manual setup)
- Tailscale pre-auth key management
- systemd service installation (remains a separate optional step)
