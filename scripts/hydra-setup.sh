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

# ── Step 2: Tailscale (Optional) ────────────────────────────
info "Step 2/7: Tailscale remote access"
echo ""

TS_IP=""
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
