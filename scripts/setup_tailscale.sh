#!/usr/bin/env bash
set -euo pipefail

PASS=0
WARN=0
FAIL=0

ok()   { echo "[PASS] $1"; PASS=$((PASS+1)); }
warn() { echo "[WARN] $1"; WARN=$((WARN+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

usage() {
  cat <<'EOF'
Usage: setup_tailscale.sh [OPTIONS]

Install and configure Tailscale for SSH remote access on a Jetson.

Options:
  --authkey KEY    Use a Tailscale auth key (skips interactive login)
  --hostname NAME  Set the Tailscale hostname (default: hydra-jetson)
  --ssh            Enable Tailscale SSH (default: enabled)
  --no-ssh         Disable Tailscale SSH
  -h, --help       Show this help message

Examples:
  # Interactive login (opens a URL to authenticate)
  sudo bash scripts/setup_tailscale.sh

  # Auth key (for batch provisioning multiple Jetsons)
  sudo bash scripts/setup_tailscale.sh --authkey tskey-auth-xxxxx

  # Custom hostname
  sudo bash scripts/setup_tailscale.sh --hostname hydra-jetson-03
EOF
  exit 0
}

# ── Defaults ──────────────────────────────────────────────────────────
AUTHKEY=""
HOSTNAME="hydra-jetson"
SSH_ENABLED=true

# ── Parse arguments ───────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --authkey)  AUTHKEY="$2"; shift 2 ;;
    --hostname) HOSTNAME="$2"; shift 2 ;;
    --ssh)      SSH_ENABLED=true; shift ;;
    --no-ssh)   SSH_ENABLED=false; shift ;;
    -h|--help)  usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# ── Root check ────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root (sudo)."
  exit 1
fi

echo "Hydra Detect — Tailscale SSH Setup"
echo "==================================="

# ── Step 1: Install Tailscale ─────────────────────────────────────────
echo
echo "Step 1: Installing Tailscale..."

if command -v tailscale >/dev/null 2>&1; then
  ok "Tailscale is already installed ($(tailscale version | head -1))"
else
  echo "  Downloading Tailscale install script..."
  curl -fsSL https://tailscale.com/install.sh | sh
  if command -v tailscale >/dev/null 2>&1; then
    ok "Tailscale installed ($(tailscale version | head -1))"
  else
    fail "Tailscale installation failed"
    echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
    exit 1
  fi
fi

# ── Step 2: Enable and start tailscaled ───────────────────────────────
echo
echo "Step 2: Enabling tailscaled service..."

systemctl enable --now tailscaled 2>/dev/null || true
if systemctl is-active --quiet tailscaled; then
  ok "tailscaled service is running"
else
  fail "tailscaled service failed to start"
  echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
  exit 1
fi

# ── Step 3: Configure SSH keys ────────────────────────────────────────
echo
echo "Step 3: Configuring SSH..."

# Ensure OpenSSH server is installed and running
if ! command -v sshd >/dev/null 2>&1; then
  echo "  Installing OpenSSH server..."
  apt-get update -qq && apt-get install -y -qq openssh-server
fi

systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true

if systemctl is-active --quiet ssh 2>/dev/null || systemctl is-active --quiet sshd 2>/dev/null; then
  ok "OpenSSH server is running"
else
  warn "OpenSSH server may not be running — Tailscale SSH can still work"
fi

# Ensure the sorcc user has an .ssh directory
SORCC_HOME="/home/sorcc"
if [ -d "$SORCC_HOME" ]; then
  mkdir -p "$SORCC_HOME/.ssh"
  chmod 700 "$SORCC_HOME/.ssh"
  touch "$SORCC_HOME/.ssh/authorized_keys"
  chmod 600 "$SORCC_HOME/.ssh/authorized_keys"
  chown -R sorcc:sorcc "$SORCC_HOME/.ssh"
  ok "SSH directory configured for sorcc"
else
  warn "User home $SORCC_HOME not found — skipping SSH key setup"
fi

# Enable password authentication as a fallback
if [ -f /etc/ssh/sshd_config ]; then
  if ! grep -q "^PasswordAuthentication yes" /etc/ssh/sshd_config; then
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
  fi
  ok "SSH password authentication enabled (fallback)"
fi

# ── Step 4: Bring up Tailscale ────────────────────────────────────────
echo
echo "Step 4: Connecting to Tailscale network..."

UP_ARGS=("--hostname=$HOSTNAME")

if [ "$SSH_ENABLED" = true ]; then
  UP_ARGS+=("--ssh")
fi

if [ -n "$AUTHKEY" ]; then
  UP_ARGS+=("--authkey=$AUTHKEY")
  echo "  Using auth key for non-interactive login..."
else
  echo "  Interactive login — a URL will appear below."
  echo "  Open it in a browser to authenticate this Jetson."
  echo
fi

tailscale up "${UP_ARGS[@]}"

# Give Tailscale a moment to establish the connection
sleep 2

# ── Step 5: Verify and report ─────────────────────────────────────────
echo
echo "Step 5: Verifying Tailscale connection..."

TS_IP="$(tailscale ip -4 2>/dev/null || true)"
TS_STATUS="$(tailscale status --self 2>/dev/null || true)"

if [ -n "$TS_IP" ]; then
  ok "Tailscale is connected"
  echo
  echo "  ┌──────────────────────────────────────────────┐"
  echo "  │  Tailscale IP:  $TS_IP"
  echo "  │  Hostname:      $HOSTNAME"
  echo "  │  SSH command:   ssh sorcc@$TS_IP"
  if [ "$SSH_ENABLED" = true ]; then
  echo "  │  Tailscale SSH: ssh sorcc@$HOSTNAME"
  fi
  echo "  │  Dashboard:     http://$TS_IP:8080"
  echo "  └──────────────────────────────────────────────┘"
else
  fail "Tailscale did not get an IP address"
fi

if [ "$SSH_ENABLED" = true ]; then
  ok "Tailscale SSH enabled (no key management needed)"
else
  warn "Tailscale SSH is disabled — use regular SSH with keys"
fi

# ── Step 6: Persistence check ─────────────────────────────────────────
echo
echo "Step 6: Checking persistence..."

if systemctl is-enabled --quiet tailscaled 2>/dev/null; then
  ok "tailscaled will start on boot"
else
  warn "tailscaled is not enabled on boot — run: sudo systemctl enable tailscaled"
fi

echo
echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
