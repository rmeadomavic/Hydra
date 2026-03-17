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

- `curl` (needed for Tailscale install; install if missing)
- Python3, pip
- Docker installed, user in `docker` group
- `nvidia-container-toolkit` installed — if missing, offer to install:
  `sudo apt-get install -y nvidia-container-toolkit && sudo systemctl restart docker`
- User in `dialout` group (serial access)
- Camera devices (`/dev/video*`) detected and reported
- Serial devices (`/dev/ttyACM*`, `/dev/ttyUSB*`) detected and reported

If group membership is missing, offer to add (`sudo usermod -aG`) and warn
that a logout/login is required for the change to take effect.

If any critical dependency is missing and the user declines to install, the
script exits with a clear message about what's needed.

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

Create if missing (as the calling user, not root, to avoid permission issues
with Docker volume mounts):
- `models/`
- `output_data/`

### Step 6 — Config

Detect hardware and prompt the instructor:

- **Serial device scan:** Check all `/dev/ttyACM*` and `/dev/ttyUSB*` devices.
  - **One device found:** `"Flight controller detected on /dev/ttyACM0. Enable MAVLink? [Y/n]"`
    - Yes: set `[mavlink] enabled = true`, `connection_string = /dev/ttyACM0`
    - No: set `[mavlink] enabled = false`
  - **Multiple devices found:** Present a numbered list and let the instructor pick:
    ```
    Serial devices found:
      1) /dev/ttyACM0
      2) /dev/ttyACM1
    Which device is the flight controller? [1]:
    ```
  - **No devices:** `"No flight controller found. Disabling MAVLink. [Enter to continue]"`
    - Set `[mavlink] enabled = false`
- **Camera:** Report which `/dev/video*` devices are present (informational only,
  config.ini default of `auto` handles device selection).

**Config modification mechanism:** Use `sed` with section-aware patterns to
modify only the `[mavlink]` section keys. Only `enabled` and `connection_string`
are touched. All other config.ini settings remain at defaults. This will leave
the git working tree dirty, which is expected and acceptable — `config.ini`
contains per-deployment settings.

### Step 7 — Test Run (Optional)

- Prompt: `"Ready to launch Hydra? [Y/n]"`
- **Yes:** Run the Docker command (based on the "Full run with MAVLink" variant
  from `docs/jetson-setup-guide.md`). Dynamically include `--device /dev/ttyACM0`
  only when MAVLink was enabled in Step 6. Print dashboard URL
  (`http://<jetson-ip>:8080`).
- **No:** Print the full docker run command so the instructor can copy/paste later.

Docker run command template:
```bash
docker run --rm --privileged --runtime nvidia \
  --device /dev/video0:/dev/video0 \
  --device /dev/video2:/dev/video2 \
  ${MAVLINK_DEVICE:+--device $MAVLINK_DEVICE:$MAVLINK_DEVICE} \
  -v /usr/sbin/nvpmodel:/usr/sbin/nvpmodel:ro \
  -v /usr/bin/jetson_clocks:/usr/bin/jetson_clocks:ro \
  -v /etc/nvpmodel.conf:/etc/nvpmodel.conf:ro \
  -v /etc/nvpmodel:/etc/nvpmodel:ro \
  -v /var/lib/nvpmodel:/var/lib/nvpmodel \
  -v $(pwd)/models:/models \
  -v $(pwd)/output_data:/data \
  -p 8080:8080 \
  hydra-detect:latest
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Tailscale on host, not in Docker | SSH needs to reach the Jetson OS directly |
| Tailscale SSH via `tailscale set --ssh` | No SSH key management needed for instructors |
| No pre-auth keys | Simpler — instructor clicks auth URL once |
| Idempotent steps | Safe to re-run after failure, reboot, or when re-checking setup |
| Separate from `jetson_preflight.sh` | Preflight stays as a lightweight field-check tool |
| Only touch MAVLink config | Avoids overwriting instructor customizations in config.ini |
| `set -euo pipefail` with `|| true` guards | Fail fast on errors, but interactive prompts and optional steps use `|| true` to avoid unexpected exits |

## Files Changed

| File | Action |
|------|--------|
| `scripts/hydra-setup.sh` | **New** — the main setup script |
| `docs/tailscale-ssh.md` | **New** — reference doc: what Tailscale SSH does, how to connect, troubleshooting |
| `README.md` | **Update** — Getting Started section points to `hydra-setup.sh` |
| `docs/jetson-setup-guide.md` | **Update** — reference the script as the recommended path |

## Re-run / Error Recovery

The script is idempotent — each step checks current state before acting. If a
Docker build fails mid-way (e.g., network timeout), re-running the script will
pick up where it left off thanks to Docker layer caching. If caching causes
issues, the instructor can run `docker build --no-cache` manually.

## Out of Scope

- JetPack flashing (covered by `docs/jetson-initial-setup.md`)
- YOLO model downloads (auto-download on first run)
- Advanced config: autonomous, RF homing, OSD (manual setup)
- Tailscale pre-auth key management
- systemd service installation (remains a separate optional step)
