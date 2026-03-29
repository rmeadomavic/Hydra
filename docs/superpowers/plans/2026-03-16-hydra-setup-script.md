# hydra-setup.sh Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a single interactive setup script that takes a freshly cloned Jetson from zero to running Hydra Detect, plus Tailscale SSH docs.

**Architecture:** One bash script (`scripts/hydra-setup.sh`) with 7 sequential phases. Each phase checks current state before acting (idempotent). Helper functions at the top for consistent output formatting and prompts. Two new docs files and two doc updates.

**Tech Stack:** Bash, sed, Docker CLI, Tailscale CLI, systemd

**Spec:** `docs/superpowers/specs/2026-03-16-hydra-setup-script-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `scripts/hydra-setup.sh` | Create | Main setup script — all 7 phases |
| `docs/tailscale-ssh.md` | Create | Reference doc for Tailscale SSH (connect, troubleshoot) |
| `README.md` | Modify | Add pointer to `hydra-setup.sh` in Getting Started |
| `docs/jetson-setup-guide.md` | Modify | Add note referencing the automated script |

---

## Chunk 1: The Setup Script

### Task 1: Script skeleton with helpers and preflight checks

**Files:**
- Create: `scripts/hydra-setup.sh`

- [ ] **Step 1: Create the script with shebang, helpers, and Step 1 (preflight)**

Write `scripts/hydra-setup.sh` with:
- `#!/usr/bin/env bash` and `set -euo pipefail`
- Color output helpers: `ok()`, `warn()`, `fail()`, `ask()` (yes/no prompt with default), `info()`
- `HYDRA_DIR` set to the repo root (relative to script location)
- `NEED_RELOGIN=false` flag for tracking group changes
- State variables: `SERIAL_DEVICES=()`, `VIDEO_DEVICES=()`, `MAVLINK_DEVICE=""`, `MAVLINK_ENABLED=false`
- Step 1 — Preflight:
  - Check `curl`, offer `sudo apt-get install -y curl` if missing
  - Check `python3`, `pip` — fail if missing with install instructions
  - Check `docker` — fail if missing
  - Check `nvidia-container-toolkit` via `dpkg -l | grep nvidia-container-toolkit` — offer install if missing
  - Check user in `docker` group — offer `sudo usermod -aG docker $USER`, set `NEED_RELOGIN=true`
  - Check user in `dialout` group — offer `sudo usermod -aG dialout $USER`, set `NEED_RELOGIN=true`
  - Detect `/dev/video*` into `VIDEO_DEVICES` array, report findings
  - Detect `/dev/ttyACM*` and `/dev/ttyUSB*` into `SERIAL_DEVICES` array, report findings
  - If `NEED_RELOGIN=true`, print warning that logout/login is required, ask to continue or exit

```bash
#!/usr/bin/env bash
# Hydra Detect — One-script Jetson setup
# Usage: bash scripts/hydra-setup.sh
set -euo pipefail

# ── Helpers ──────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[PASS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
info() { echo -e "${CYAN}[INFO]${NC} $1"; }

# ask "prompt" default(Y/N) → returns 0 for yes, 1 for no
ask() {
    local prompt="$1" default="${2:-Y}"
    local yn
    if [[ "$default" == "Y" ]]; then
        read -rp "$prompt [Y/n]: " yn
        yn="${yn:-Y}"
    else
        read -rp "$prompt [y/N]: " yn
        yn="${yn:-N}"
    fi
    [[ "$yn" =~ ^[Yy] ]]
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HYDRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$HYDRA_DIR"

NEED_RELOGIN=false
SERIAL_DEVICES=()
VIDEO_DEVICES=()
MAVLINK_DEVICE=""
MAVLINK_ENABLED=false

echo ""
echo "========================================"
echo "  Hydra Detect — Jetson Setup"
echo "========================================"
echo ""

# ── Step 1: Preflight Checks ────────────────────────────────
info "Step 1/7: Preflight checks"
echo ""

# curl
if command -v curl >/dev/null 2>&1; then
    ok "curl is installed"
else
    warn "curl is not installed"
    if ask "Install curl?" "Y"; then
        sudo apt-get update && sudo apt-get install -y curl
        ok "curl installed"
    else
        fail "curl is required for Tailscale install. Exiting."
        exit 1
    fi
fi

# python3
if command -v python3 >/dev/null 2>&1; then
    ok "python3 is installed"
else
    fail "python3 is not installed. Run: sudo apt install -y python3"
    exit 1
fi

# pip
if python3 -m pip --version >/dev/null 2>&1; then
    ok "pip is installed"
else
    fail "pip is not installed. Run: sudo apt install -y python3-pip"
    exit 1
fi

# docker
if command -v docker >/dev/null 2>&1; then
    ok "Docker is installed ($(docker --version | head -c 40))"
else
    fail "Docker is not installed. See: https://docs.nvidia.com/jetson/jetpack/install-jetpack/index.html"
    exit 1
fi

# nvidia-container-toolkit
if dpkg -l nvidia-container-toolkit >/dev/null 2>&1; then
    ok "nvidia-container-toolkit is installed"
else
    warn "nvidia-container-toolkit is not installed"
    if ask "Install nvidia-container-toolkit?" "Y"; then
        sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
        sudo systemctl restart docker
        ok "nvidia-container-toolkit installed"
    else
        fail "nvidia-container-toolkit is required. Exiting."
        exit 1
    fi
fi

# docker group
if id -nG | grep -qw docker; then
    ok "User is in docker group"
else
    warn "User is NOT in docker group"
    if ask "Add $USER to docker group?" "Y"; then
        sudo usermod -aG docker "$USER"
        NEED_RELOGIN=true
        ok "Added $USER to docker group (takes effect after logout/login)"
    fi
fi

# dialout group
if id -nG | grep -qw dialout; then
    ok "User is in dialout group"
else
    warn "User is NOT in dialout group (needed for serial/MAVLink)"
    if ask "Add $USER to dialout group?" "Y"; then
        sudo usermod -aG dialout "$USER"
        NEED_RELOGIN=true
        ok "Added $USER to dialout group (takes effect after logout/login)"
    fi
fi

# Detect video devices
for dev in /dev/video*; do
    [ -e "$dev" ] && VIDEO_DEVICES+=("$dev")
done || true
if [ ${#VIDEO_DEVICES[@]} -gt 0 ]; then
    ok "Camera devices found: ${VIDEO_DEVICES[*]}"
else
    warn "No camera devices found (/dev/video*)"
fi

# Detect serial devices
for dev in /dev/ttyACM* /dev/ttyUSB*; do
    [ -e "$dev" ] && SERIAL_DEVICES+=("$dev")
done || true
if [ ${#SERIAL_DEVICES[@]} -gt 0 ]; then
    ok "Serial devices found: ${SERIAL_DEVICES[*]}"
else
    warn "No serial devices found (/dev/ttyACM*, /dev/ttyUSB*)"
fi

# Relogin warning
if [ "$NEED_RELOGIN" = true ]; then
    echo ""
    warn "Group changes require logout/login to take effect."
    warn "Docker and serial commands may fail until you re-login."
    if ! ask "Continue anyway?" "Y"; then
        info "Re-run this script after logging out and back in."
        exit 0
    fi
fi

echo ""
```

- [ ] **Step 2: Make executable and test preflight on the Jetson**

```bash
chmod +x scripts/hydra-setup.sh
bash scripts/hydra-setup.sh
```

Expected: All checks pass on the current Jetson (curl, python3, pip, docker,
nvidia-container-toolkit, groups all present). Camera and serial devices
detected. Script runs to the end of Step 1 and exits (Steps 2-7 not yet written).

- [ ] **Step 3: Commit**

```bash
git add scripts/hydra-setup.sh
git commit -m "feat: add hydra-setup.sh skeleton with preflight checks"
```

---

### Task 2: Tailscale setup (Step 2)

**Files:**
- Modify: `scripts/hydra-setup.sh`

- [ ] **Step 1: Add Step 2 — Tailscale (optional)**

Append after the Step 1 block:

```bash
# ── Step 2: Tailscale (Optional) ────────────────────────────
info "Step 2/7: Tailscale remote access"
echo ""

if command -v tailscale >/dev/null 2>&1; then
    TS_IP="$(tailscale ip -4 2>/dev/null || true)"
    if [ -n "$TS_IP" ]; then
        ok "Tailscale already running ($TS_IP)"
    else
        info "Tailscale is installed but not connected."
        if ask "Connect Tailscale now?" "Y"; then
            sudo tailscale up
            TS_IP="$(tailscale ip -4 2>/dev/null || true)"
            sudo tailscale set --ssh
            ok "Tailscale connected ($TS_IP) with SSH enabled"
        fi
    fi
else
    if ask "Would you like to set up Tailscale for remote SSH?" "Y"; then
        info "Installing Tailscale..."
        curl -fsSL https://tailscale.com/install.sh | sh
        info "Starting Tailscale — follow the auth URL in your browser..."
        sudo tailscale up
        sudo tailscale set --ssh
        TS_IP="$(tailscale ip -4 2>/dev/null || true)"
        ok "Tailscale connected ($TS_IP) with SSH enabled"
        info "SSH from another machine: ssh $USER@$TS_IP"
    else
        info "Skipping Tailscale. Remote SSH will not be available via Tailscale."
    fi
fi

echo ""
```

- [ ] **Step 2: Test on Jetson (Tailscale already installed)**

```bash
bash scripts/hydra-setup.sh
```

Expected: Preflight passes, then Step 2 prints `[PASS] Tailscale already running (<JETSON_IP>)` and moves on.

- [ ] **Step 3: Commit**

```bash
git add scripts/hydra-setup.sh
git commit -m "feat: add Tailscale optional setup to hydra-setup.sh"
```

---

### Task 3: Docker pull, build, directories (Steps 3-5)

**Files:**
- Modify: `scripts/hydra-setup.sh`

- [ ] **Step 1: Add Steps 3, 4, and 5**

Append after the Step 2 block:

```bash
# ── Step 3: Docker Base Image ───────────────────────────────
info "Step 3/7: Docker base image"
echo ""

BASE_IMAGE="dustynv/l4t-pytorch:r36.4.0"
if docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    ok "Base image already pulled ($BASE_IMAGE)"
else
    info "Pulling base image ($BASE_IMAGE) — this is ~6 GB, may take a while..."
    docker pull "$BASE_IMAGE"
    ok "Base image pulled"
fi

echo ""

# ── Step 4: Docker Build ────────────────────────────────────
info "Step 4/7: Build Hydra Detect image"
echo ""

BUILD_IMAGE=true
if docker image inspect hydra-detect:latest >/dev/null 2>&1; then
    ok "hydra-detect:latest image already exists"
    if ! ask "Rebuild the image?" "N"; then
        BUILD_IMAGE=false
    fi
fi

if [ "$BUILD_IMAGE" = true ]; then
    info "Building hydra-detect:latest (this takes ~2 minutes)..."
    docker build --network=host -t hydra-detect:latest "$HYDRA_DIR"
    ok "hydra-detect:latest built successfully"
fi

echo ""

# ── Step 5: Directories ─────────────────────────────────────
info "Step 5/7: Data directories"
echo ""

mkdir -p "$HYDRA_DIR/models"
mkdir -p "$HYDRA_DIR/output_data"
ok "models/ and output_data/ directories ready"

echo ""
```

- [ ] **Step 2: Test on Jetson**

```bash
bash scripts/hydra-setup.sh
```

Expected: Steps 1-2 pass quickly. Step 3 skips pull if image exists (or pulls
if not). Step 4 detects existing image, asks about rebuild. Step 5 creates dirs
(or confirms they exist). Script exits after Step 5.

- [ ] **Step 3: Commit**

```bash
git add scripts/hydra-setup.sh
git commit -m "feat: add Docker pull, build, and directory setup to hydra-setup.sh"
```

---

### Task 4: Config and test run (Steps 6-7)

**Files:**
- Modify: `scripts/hydra-setup.sh`

- [ ] **Step 1: Add Steps 6 and 7**

Append after the Step 5 block:

```bash
# ── Step 6: Config ──────────────────────────────────────────
info "Step 6/7: Configure MAVLink"
echo ""

CONFIG="$HYDRA_DIR/config.ini"

if [ ${#SERIAL_DEVICES[@]} -eq 0 ]; then
    info "No flight controller found. Disabling MAVLink."
    read -rp "Press Enter to continue..."
    MAVLINK_ENABLED=false
elif [ ${#SERIAL_DEVICES[@]} -eq 1 ]; then
    if ask "Flight controller detected on ${SERIAL_DEVICES[0]}. Enable MAVLink?" "Y"; then
        MAVLINK_DEVICE="${SERIAL_DEVICES[0]}"
        MAVLINK_ENABLED=true
    else
        MAVLINK_ENABLED=false
    fi
else
    echo "Serial devices found:"
    for i in "${!SERIAL_DEVICES[@]}"; do
        echo "  $((i+1))) ${SERIAL_DEVICES[$i]}"
    done
    read -rp "Which device is the flight controller? [1]: " choice
    choice="${choice:-1}"
    idx=$((choice - 1))
    if [ "$idx" -ge 0 ] && [ "$idx" -lt ${#SERIAL_DEVICES[@]} ]; then
        MAVLINK_DEVICE="${SERIAL_DEVICES[$idx]}"
        MAVLINK_ENABLED=true
        ok "MAVLink will use ${MAVLINK_DEVICE}"
    else
        warn "Invalid choice. Disabling MAVLink."
        MAVLINK_ENABLED=false
    fi
fi

# Apply config changes (section-scoped sed to avoid touching other [section] enabled keys)
if [ "$MAVLINK_ENABLED" = true ]; then
    sed -i '/^\[mavlink\]/,/^\[/{s/^enabled = .*/enabled = true/}' "$CONFIG"
    sed -i "/^\[mavlink\]/,/^\[/{s|^connection_string = .*|connection_string = ${MAVLINK_DEVICE}|}" "$CONFIG"
    ok "MAVLink enabled (${MAVLINK_DEVICE}) in config.ini"
else
    sed -i '/^\[mavlink\]/,/^\[/{s/^enabled = .*/enabled = false/}' "$CONFIG"
    ok "MAVLink disabled in config.ini"
fi

# Camera info
if [ ${#VIDEO_DEVICES[@]} -gt 0 ]; then
    info "Camera devices: ${VIDEO_DEVICES[*]} (config.ini source=auto will pick the right one)"
fi

echo ""

# ── Step 7: Test Run ────────────────────────────────────────
info "Step 7/7: Launch Hydra Detect"
echo ""

# Build device flags
DEVICE_FLAGS=""
for dev in "${VIDEO_DEVICES[@]}"; do
    DEVICE_FLAGS+=" --device $dev:$dev"
done
if [ "$MAVLINK_ENABLED" = true ] && [ -n "$MAVLINK_DEVICE" ]; then
    DEVICE_FLAGS+=" --device $MAVLINK_DEVICE:$MAVLINK_DEVICE"
fi

DOCKER_CMD="docker run --rm --privileged --runtime nvidia \
$DEVICE_FLAGS \
  -v $HYDRA_DIR/config.ini:/app/config.ini:ro \
  -v /usr/sbin/nvpmodel:/usr/sbin/nvpmodel:ro \
  -v /usr/bin/jetson_clocks:/usr/bin/jetson_clocks:ro \
  -v /etc/nvpmodel.conf:/etc/nvpmodel.conf:ro \
  -v /etc/nvpmodel:/etc/nvpmodel:ro \
  -v /var/lib/nvpmodel:/var/lib/nvpmodel \
  -v $HYDRA_DIR/models:/models \
  -v $HYDRA_DIR/output_data:/data \
  -p 8080:8080 \
  hydra-detect:latest"

# Determine dashboard URL
JETSON_IP="$(hostname -I | awk '{print $1}')"
TS_IP_DISPLAY="${TS_IP:-}"
DASHBOARD_URL="http://${JETSON_IP}:8080"

echo "Docker run command:"
echo ""
echo "  $DOCKER_CMD"
echo ""
if [ -n "$TS_IP_DISPLAY" ]; then
    info "Dashboard (Tailscale): http://${TS_IP_DISPLAY}:8080"
fi
info "Dashboard (local): $DASHBOARD_URL"
echo ""

if ask "Launch Hydra now?" "Y"; then
    info "Starting Hydra Detect..."
    eval "$DOCKER_CMD"
else
    info "Run the command above when you're ready."
fi

echo ""
echo "========================================"
echo "  Setup complete!"
echo "========================================"
```

- [ ] **Step 2: Test the full script on Jetson**

```bash
bash scripts/hydra-setup.sh
```

Expected: All 7 steps execute. Step 6 detects serial devices, prompts about
MAVLink. Step 7 prints the docker run command and offers to launch. Answer "n"
to launch for now (we'll test the actual run separately).

- [ ] **Step 3: Commit**

```bash
git add scripts/hydra-setup.sh
git commit -m "feat: add MAVLink config and Docker launch to hydra-setup.sh"
```

---

## Chunk 2: Documentation Updates

### Task 5: Tailscale SSH reference doc

**Files:**
- Create: `docs/tailscale-ssh.md`

- [ ] **Step 1: Write the doc**

```markdown
# Tailscale SSH — Remote Access to Jetson

Tailscale creates a private mesh VPN between your devices. With Tailscale SSH
enabled, you can SSH into the Jetson from any machine on your Tailscale
network — no port forwarding, no public IP, no SSH key setup.

## How It Works

1. The Jetson and your laptop both run Tailscale, logged into the same account
2. Tailscale assigns each device a stable IP (100.x.x.x)
3. Tailscale SSH handles authentication — no passwords or keys needed

## Connecting from Windows

Open PowerShell or Windows Terminal:

```
ssh sorcc@<jetson-tailscale-ip>
```

Find the Jetson's Tailscale IP:
- On the Jetson: `tailscale ip -4`
- In the Tailscale admin console: https://login.tailscale.com/admin/machines

You can also use the machine name:
```
ssh sorcc@sorcc-desktop
```

## Connecting from Mac/Linux

Same command:
```
ssh sorcc@<jetson-tailscale-ip>
```

## Setting Up Tailscale (if not done during setup)

If you skipped Tailscale during `hydra-setup.sh`, you can install it manually:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale set --ssh
```

Follow the auth URL printed by `tailscale up` to link the device to your
Tailscale account.

## Troubleshooting

### "Connection refused" or timeout
- Is Tailscale running on both machines? Check: `tailscale status`
- Are both machines on the same Tailscale account?
- Try pinging the Tailscale IP: `ping <jetson-tailscale-ip>`

### "Permission denied"
- Make sure you're using the right username: `ssh sorcc@...` (not your
  Windows username)
- Is Tailscale SSH enabled on the Jetson? Check: `tailscale status`
  Look for "SSH" in the output. If missing: `sudo tailscale set --ssh`

### Tailscale not starting on boot
```bash
sudo systemctl enable tailscaled
sudo systemctl start tailscaled
```

### Need to re-authenticate
```bash
sudo tailscale up --reset
```
Follow the new auth URL.
```

- [ ] **Step 2: Commit**

```bash
git add docs/tailscale-ssh.md
git commit -m "docs: add Tailscale SSH reference guide"
```

---

### Task 6: Update README.md and jetson-setup-guide.md

**Files:**
- Modify: `README.md`
- Modify: `docs/jetson-setup-guide.md`

- [ ] **Step 1: Update README.md Getting Started section**

Add a note about the setup script before the manual steps. After the
`## Getting Started` heading and before the existing code block, add:

```markdown
### Automated Setup (Recommended)

After flashing JetPack and completing the first-boot wizard
([guide](docs/jetson-initial-setup.md)), run the setup script:

```bash
cd ~/Hydra
bash scripts/hydra-setup.sh
```

This walks you through everything: system checks, optional Tailscale remote
access, Docker build, hardware config, and first launch.

For remote access after setup, see the [Tailscale SSH guide](docs/tailscale-ssh.md).

### Manual Setup
```

The existing Getting Started code block becomes the "Manual Setup" path.

- [ ] **Step 2: Update docs/jetson-setup-guide.md**

Add a note at the top, after the "For initial JetPack flashing..." paragraph:

```markdown
> **Prefer the automated path?** Run `bash scripts/hydra-setup.sh` instead —
> it handles everything below in one interactive script. See the
> [Tailscale SSH guide](tailscale-ssh.md) for remote access setup.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/jetson-setup-guide.md
git commit -m "docs: reference hydra-setup.sh from README and setup guide"
```

---

## Chunk 3: End-to-End Verification

### Task 7: Full walkthrough test

- [ ] **Step 1: Run the complete script from scratch**

```bash
bash scripts/hydra-setup.sh
```

Walk through all 7 steps. Verify:
- Preflight: all checks pass
- Tailscale: reports existing connection
- Docker: base image detected or pulled, image built or skip offered
- Directories: created or already exist
- Config: serial device detected, MAVLink enabled/disabled in config.ini
- Launch: docker run command printed, optionally launch

- [ ] **Step 2: Verify config.ini was modified correctly**

```bash
grep -A3 '^\[mavlink\]' config.ini
```

Expected: Only `enabled` and `connection_string` changed. No other sections affected.

- [ ] **Step 3: Verify idempotency — run again**

```bash
bash scripts/hydra-setup.sh
```

Expected: Everything skips or reports already-done. No errors, no re-downloads,
no re-builds (unless user requests rebuild).

- [ ] **Step 4: Test the actual Docker launch**

Say "Y" to the launch prompt in Step 7, or run the printed docker command manually.
Verify:
- Container starts without errors
- Dashboard accessible at `http://localhost:8080`
- If MAVLink enabled: MAVLink connection status shows on dashboard
- `Ctrl+C` stops the container cleanly
