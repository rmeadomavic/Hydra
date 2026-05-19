#!/usr/bin/env bash
# platform_clone_wipe.sh — Pre-clone wipe for Hydra golden image
#
# Run this BEFORE imaging the SSD for distribution to take-home units. Each
# cloned unit must boot with a clean per-unit identity rather than inherit
# the master unit's callsign, API token, or dashboard password.
#
# What this script clears (issue #149):
#   - /etc/machine-id (regenerated on first boot by systemd-machine-id-setup)
#   - SSH host keys in /etc/ssh/ssh_host_*
#   - Tailscale persistent state in /var/lib/tailscale/
#   - bash / zsh history for the imaging user
#   - [identity] section of config.ini (so identity_boot.py demands setup)
#   - [web].api_token and [web].web_password in config.ini (legacy fallback path)
#
# What this script does NOT clear:
#   - The Hydra repo itself
#   - Trained model weights or output_data/
#   - Apt packages or systemd unit files
#   - Network configuration in NetworkManager (wifi credentials)
#     -> Wipe those separately with `nmcli connection delete <ssid>` if needed
#
# After running this script, immediately power off and image the SSD.
# On first boot of each cloned unit, the operator MUST run:
#     python scripts/platform_setup.py
# which regenerates the identity surface (callsign, token, password).

set -euo pipefail

CONFIG_PATH="${HYDRA_CONFIG:-./config.ini}"
DRY_RUN=0
FORCE=0

usage() {
  cat <<'EOF'
Usage: platform_clone_wipe.sh [OPTIONS]

Wipe per-unit identity before cloning the Hydra master SSD.

Options:
  --config PATH    Path to config.ini (default: ./config.ini or $HYDRA_CONFIG)
  --dry-run        Show what would be cleared without changing anything
  --force          Don't prompt for confirmation (for scripted imaging pipelines)
  -h, --help       Show this help

This is a destructive operation. Run it ONCE just before imaging.
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)  CONFIG_PATH="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force)   FORCE=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

if [[ $DRY_RUN -eq 1 ]]; then
  PREFIX="[DRY-RUN] would run:"
else
  PREFIX=""
fi

if [[ $FORCE -eq 0 && $DRY_RUN -eq 0 ]]; then
  echo "This will wipe machine-id, SSH host keys, Tailscale state, shell history,"
  echo "and per-unit identity fields in $CONFIG_PATH."
  echo ""
  echo "Run this ONLY immediately before imaging the SSD. The current unit"
  echo "will require Platform Setup to come back online after this wipe."
  echo ""
  read -r -p "Type WIPE to continue: " confirm
  if [[ "$confirm" != "WIPE" ]]; then
    echo "Cancelled."
    exit 1
  fi
fi

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "$PREFIX $*"
  else
    echo "+ $*"
    "$@"
  fi
}

run_shell() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "$PREFIX $*"
  else
    echo "+ $*"
    bash -c "$*"
  fi
}

echo "==> Wiping machine-id"
if [[ -f /etc/machine-id ]]; then
  run_shell "sudo truncate -s 0 /etc/machine-id"
  if [[ -f /var/lib/dbus/machine-id ]]; then
    run_shell "sudo rm -f /var/lib/dbus/machine-id"
  fi
fi

echo "==> Wiping SSH host keys"
run_shell "sudo rm -f /etc/ssh/ssh_host_*"

echo "==> Wiping Tailscale persistent state"
if [[ -d /var/lib/tailscale ]]; then
  run_shell "sudo systemctl stop tailscaled 2>/dev/null || true"
  run_shell "sudo rm -rf /var/lib/tailscale/tailscaled.state /var/lib/tailscale/tailscaled.log* 2>/dev/null || true"
fi

echo "==> Wiping shell history"
for histfile in ~/.bash_history ~/.zsh_history ~/.python_history; do
  if [[ -f $histfile ]]; then
    run_shell "truncate -s 0 $histfile"
  fi
done

echo "==> Wiping per-unit identity from $CONFIG_PATH"
if [[ ! -f $CONFIG_PATH ]]; then
  echo "Config file $CONFIG_PATH not found — skipping config wipe."
else
  # Use Python to do this surgically; sed on .ini is brittle.
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "$PREFIX clear [identity] section and [web].api_token / [web].web_password in $CONFIG_PATH"
  else
    python3 - <<PYEOF
import configparser
from pathlib import Path

path = Path("$CONFIG_PATH")
cfg = configparser.ConfigParser(inline_comment_prefixes=(';', '#'))
cfg.read(path)

# Clear [identity] entirely — Platform Setup regenerates it on first boot.
if cfg.has_section("identity"):
    cfg.remove_section("identity")
    print("  cleared [identity]")

# Clear [web].api_token and [web].web_password (legacy plaintext path).
# Leave [web].require_auth_for_control alone — that's the policy, not the secret.
if cfg.has_section("web"):
    for key in ("api_token", "web_password"):
        if cfg.has_option("web", key):
            cfg.set("web", key, "")
            print(f"  cleared [web].{key}")

with open(path, "w") as f:
    cfg.write(f)
PYEOF
  fi
fi

echo ""
if [[ $DRY_RUN -eq 1 ]]; then
  echo "Dry run complete. Re-run without --dry-run to actually wipe."
else
  echo "Wipe complete. Power off NOW and image the SSD."
  echo "Each cloned unit must run scripts/platform_setup.py on first boot."
fi
