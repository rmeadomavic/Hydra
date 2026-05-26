#!/usr/bin/env bash
# platform-update.sh — Hydra OTA update entry point (issue #152, PR-A skeleton).
#
# PR-A only stubs the timer surface. The actual update path (image-digest
# GPG-verify + docker pull by digest + A/B promotion on healthcheck) lands
# in PR-B/C/D. For now this script just reads the channel and update env,
# logs intent, and exits 0 so the systemd timer's first runs are inert.
#
# Wired from:
#   - /etc/systemd/system/hydra-platform-update.service (EnvironmentFile
#     loads /etc/hydra/update.env if present)
#   - /etc/systemd/system/hydra-platform-update.timer   (daily,
#     RandomizedDelaySec=1800)
#
# Inputs (all optional — defaults are safe on a fresh box):
#   /etc/hydra/channel     -> single token, "stable" or "beta". Default
#                             "stable" if missing or empty.
#   /etc/hydra/update.env  -> shell-sourceable. Expected keys (placeholder,
#                             not used yet):
#                               GHCR_REPO=ghcr.io/rmeadomavic/hydra
#                               GPG_KEY_PATH=/etc/hydra/ota-signing.pub
#
# Output: a single ``[platform-update]`` log line on stdout (journald
# captures it via SyslogIdentifier=hydra-platform-update).

set -euo pipefail

readonly CHANNEL_FILE="${HYDRA_CHANNEL_PATH:-/etc/hydra/channel}"
readonly UPDATE_ENV_FILE="${HYDRA_UPDATE_ENV_PATH:-/etc/hydra/update.env}"

# --- channel -----------------------------------------------------------------
channel="stable"
if [ -r "${CHANNEL_FILE}" ]; then
    # First whitespace token only — keeps trailing comments / newlines
    # from leaking into the log line.
    read -r raw_channel < "${CHANNEL_FILE}" || raw_channel=""
    if [ -n "${raw_channel}" ]; then
        channel="${raw_channel}"
    fi
fi

# --- update env (placeholder, not consumed yet in PR-A) ----------------------
GHCR_REPO="${GHCR_REPO:-ghcr.io/rmeadomavic/hydra}"
GPG_KEY_PATH="${GPG_KEY_PATH:-/etc/hydra/ota-signing.pub}"
if [ -r "${UPDATE_ENV_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${UPDATE_ENV_FILE}"
fi

echo "[platform-update] channel=${channel} ghcr=${GHCR_REPO} gpg_key=${GPG_KEY_PATH} would check for updates"

exit 0
