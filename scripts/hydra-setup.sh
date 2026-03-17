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
