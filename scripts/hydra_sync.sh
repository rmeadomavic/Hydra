#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: hydra_sync.sh [OPTIONS] <jetson-host>

Sync latest code to a Jetson, rebuild the Docker image, and restart the
Hydra Detect service. Run from your laptop (Linux/Mac/WSL).

Arguments:
  <jetson-host>    Tailscale IP, Tailscale hostname, or mDNS name (e.g. hydra.local)

Options:
  -u, --user USER  SSH user on the Jetson (default: sorcc)
  -d, --dir DIR    Hydra repo path on the Jetson (default: /home/sorcc/Hydra)
  -b, --branch BR  Git branch to pull (default: main)
  --no-rebuild     Skip Docker image rebuild (just pull code and restart)
  --dry-run        Show what would be done without executing
  -h, --help       Show this help message

Examples:
  # Sync to a Jetson over Tailscale (by hostname)
  bash scripts/hydra_sync.sh hydra-jetson

  # Sync to a Jetson over Tailscale (by IP)
  bash scripts/hydra_sync.sh 100.64.1.42

  # Sync to a Jetson on the local network (mDNS)
  bash scripts/hydra_sync.sh hydra.local

  # Sync without rebuilding Docker image
  bash scripts/hydra_sync.sh --no-rebuild hydra-jetson

  # Different user/directory
  bash scripts/hydra_sync.sh -u admin -d /opt/Hydra hydra-jetson
EOF
  exit 0
}

# ── Defaults ──────────────────────────────────────────────────────────
USER="sorcc"
HYDRA_DIR="/home/sorcc/Hydra"
BRANCH="main"
REBUILD=true
DRY_RUN=false
HOST=""

# ── Parse arguments ───────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--user)       USER="$2"; shift 2 ;;
    -d|--dir)        HYDRA_DIR="$2"; shift 2 ;;
    -b|--branch)     BRANCH="$2"; shift 2 ;;
    --no-rebuild)    REBUILD=false; shift ;;
    --dry-run)       DRY_RUN=true; shift ;;
    -h|--help)       usage ;;
    -*)              echo "Unknown option: $1"; usage ;;
    *)               HOST="$1"; shift ;;
  esac
done

if [ -z "$HOST" ]; then
  echo "Error: <jetson-host> is required."
  echo "Run with -h for usage."
  exit 1
fi

SSH_TARGET="$USER@$HOST"

# ── Helpers ───────────────────────────────────────────────────────────
run_remote() {
  if [ "$DRY_RUN" = true ]; then
    echo "[DRY RUN] ssh $SSH_TARGET \"$*\""
  else
    ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$SSH_TARGET" "$@"
  fi
}

step() {
  echo
  echo "── $1 ──"
}

# ── Main ──────────────────────────────────────────────────────────────
echo "Hydra Detect — Remote Sync"
echo "=========================="
echo "  Target:  $SSH_TARGET"
echo "  Repo:    $HYDRA_DIR"
echo "  Branch:  $BRANCH"
echo "  Rebuild: $REBUILD"
echo

# Step 1: Test connectivity
step "Testing SSH connection"
if [ "$DRY_RUN" = false ]; then
  if ! ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$SSH_TARGET" "echo ok" >/dev/null 2>&1; then
    echo "Error: Cannot reach $SSH_TARGET"
    echo
    echo "Troubleshooting:"
    echo "  - Is Tailscale running on both machines? (tailscale status)"
    echo "  - Can you ping the host? (ping $HOST)"
    echo "  - Is SSH enabled on the Jetson? (sudo systemctl status ssh)"
    exit 1
  fi
fi
echo "  Connected to $SSH_TARGET"

# Step 2: Pull latest code
step "Pulling latest code (branch: $BRANCH)"
run_remote "cd $HYDRA_DIR && git fetch origin $BRANCH && git checkout $BRANCH && git pull origin $BRANCH"

# Step 3: Rebuild Docker image (optional)
if [ "$REBUILD" = true ]; then
  step "Rebuilding Docker image"
  run_remote "cd $HYDRA_DIR && docker build --network=host -t hydra-detect:latest ."
  echo "  Image rebuilt."
else
  step "Skipping Docker rebuild (--no-rebuild)"
fi

# Step 4: Restart the service
step "Restarting hydra-detect service"
run_remote "sudo systemctl restart hydra-detect"

# Step 5: Verify
step "Verifying service status"
if [ "$DRY_RUN" = false ]; then
  STATUS="$(run_remote "systemctl is-active hydra-detect 2>/dev/null || echo inactive")"
  if [ "$STATUS" = "active" ]; then
    echo "  hydra-detect is running."
  else
    echo "  Warning: hydra-detect status is '$STATUS'"
    echo "  Check logs: ssh $SSH_TARGET 'sudo journalctl -u hydra-detect -n 30'"
  fi
fi

# Step 6: Print dashboard URL
step "Done"
echo "  Dashboard: http://$HOST:8080"
echo
