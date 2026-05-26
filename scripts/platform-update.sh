#!/usr/bin/env bash
# platform-update.sh — Hydra OTA update entry point (issue #152, PR-B).
#
# Flow (PR-B):
#   1. Read channel from /etc/hydra/channel (allowlist: stable, beta).
#   2. Read GHCR_REPO + GPG_KEY_PATH + MANIFEST_URL_BASE from
#      /etc/hydra/update.env (or env defaults).
#   3. Resolve manifest URL by channel:
#        stable -> ${BASE}/latest/download/manifest.json  (GH 302's to latest)
#        beta   -> ${BASE}/download/beta/manifest.json    (literal "beta" tag,
#                                                          a rolling pre-release)
#   4. curl --fail manifest.json + manifest.json.sig to a tmpdir.
#   5. gpg --verify against the pinned public keyring at GPG_KEY_PATH.
#   6. Validate manifest schema + that manifest channel == box channel.
#   7. If manifest version == $HYDRA_VERSION (running container), exit
#      with status="up_to_date" — no pull.
#   8. docker pull ${GHCR_REPO}@${digest}  (digest from the verified manifest)
#   9. Write /var/lib/hydra/last-update.json atomically (tmp+rename).
#
# PR-B does NOT restart the container — the pulled image sits in the
# local Docker cache until PR-C promotes it via the A/B flip + healthcheck
# gate. PR-D adds the dashboard view.
#
# Wired from:
#   - /etc/systemd/system/hydra-platform-update.service (EnvironmentFile
#     loads /etc/hydra/update.env if present)
#   - /etc/systemd/system/hydra-platform-update.timer   (daily,
#     RandomizedDelaySec=1800)
#
# Inputs (all overridable via env — tests use HYDRA_*_PATH to sandbox):
#   /etc/hydra/channel                   single token: "stable" | "beta"
#   /etc/hydra/update.env                shell-sourceable:
#                                          GHCR_REPO=ghcr.io/rmeadomavic/hydra
#                                          GPG_KEY_PATH=/etc/hydra/ota-signing.pub
#                                          MANIFEST_URL_BASE=https://github.com/rmeadomavic/Hydra/releases
#
# Output:
#   stdout              one ``[platform-update]`` line per phase
#   /var/lib/hydra/last-update.json
#                       {"ts": <unix>, "status": "ok"|"failed"|"up_to_date",
#                        "version": str, "digest": str, "channel": str,
#                        "reason": str?}
#
# Failure-mode contract: any reachable failure path MUST write a
# last-update.json with status="failed" and a short, fixed ``reason``
# token (``manifest_fetch_failed``, ``signature_invalid``, etc.) before
# exiting non-zero. The one exception is missing_signing_key, which
# exits 0 (so the timer stays armed) but still records the failure so
# the operator sees it on the dashboard.

set -euo pipefail

# --- paths + defaults --------------------------------------------------------

readonly CHANNEL_FILE="${HYDRA_CHANNEL_PATH:-/etc/hydra/channel}"
readonly UPDATE_ENV_FILE="${HYDRA_UPDATE_ENV_PATH:-/etc/hydra/update.env}"
readonly LAST_UPDATE_PATH="${HYDRA_LAST_UPDATE_PATH:-/var/lib/hydra/last-update.json}"

# The channels we will accept from /etc/hydra/channel AND from the
# manifest payload. Anything else is rejected before any network or
# shell substitution happens (R3-1 from PR-A's adversarial).
readonly ALLOWED_CHANNELS=("stable" "beta")

TMP_DIR=""
CURRENT_CHANNEL=""
CURRENT_VERSION=""
CURRENT_DIGEST=""

# --- tmp dir + cleanup -------------------------------------------------------

cleanup() {
    if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
        rm -rf -- "${TMP_DIR}"
    fi
}
trap cleanup EXIT

# --- logging -----------------------------------------------------------------

log() {
    printf '[platform-update] %s\n' "$*"
}

# --- last-update.json writer (atomic) ----------------------------------------

# write_last_update STATUS [REASON]
#
# Composes a JSON record using the current channel/version/digest globals
# and writes it atomically (tmp file in same dir + mv) so the read side
# (version_surface._read_last_update) never sees a half-written file.
# Missing fields are emitted as empty strings — version_surface accepts
# any JSON object.
write_last_update() {
    local status="$1"
    local reason="${2:-}"
    local ts
    ts="$(date +%s)"

    local parent_dir
    parent_dir="$(dirname -- "${LAST_UPDATE_PATH}")"
    mkdir -p -- "${parent_dir}"

    local tmp_file
    tmp_file="$(mktemp -- "${parent_dir}/.last-update.json.XXXXXX")"

    # Compose JSON via python3 — robust against quoting / unicode quirks
    # that a printf '{"key":"%s"}' approach would mangle.
    STATUS_ENV="${status}" \
    REASON_ENV="${reason}" \
    TS_ENV="${ts}" \
    VERSION_ENV="${CURRENT_VERSION}" \
    DIGEST_ENV="${CURRENT_DIGEST}" \
    CHANNEL_ENV="${CURRENT_CHANNEL}" \
    python3 -c '
import json, os, sys
rec = {
    "ts": int(os.environ["TS_ENV"]),
    "status": os.environ["STATUS_ENV"],
    "version": os.environ["VERSION_ENV"],
    "digest": os.environ["DIGEST_ENV"],
    "channel": os.environ["CHANNEL_ENV"],
}
reason = os.environ.get("REASON_ENV", "")
if reason:
    rec["reason"] = reason
sys.stdout.write(json.dumps(rec))
' > "${tmp_file}"

    mv -f -- "${tmp_file}" "${LAST_UPDATE_PATH}"
}

# fail REASON [EXIT_CODE]
#
# Records a failed last-update entry and exits non-zero (default 1).
# Use for any post-pre-flight failure where we want the timer to keep
# firing tomorrow.
fail() {
    local reason="$1"
    local code="${2:-1}"
    log "FAIL: ${reason}"
    write_last_update "failed" "${reason}"
    exit "${code}"
}

# --- channel allowlist -------------------------------------------------------

is_allowed_channel() {
    local candidate="$1"
    local allowed
    for allowed in "${ALLOWED_CHANNELS[@]}"; do
        [[ "${candidate}" == "${allowed}" ]] && return 0
    done
    return 1
}

# --- read channel ------------------------------------------------------------

channel="stable"
if [[ -r "${CHANNEL_FILE}" ]]; then
    read -r raw_channel < "${CHANNEL_FILE}" || raw_channel=""
    # Strip a trailing carriage return so a CRLF channel file written
    # from Windows still matches the allowlist. Defends against the
    # "beta\r" mis-match observed in tests run under Git Bash.
    raw_channel="${raw_channel%$'\r'}"
    if [[ -n "${raw_channel}" ]]; then
        channel="${raw_channel}"
    fi
fi
CURRENT_CHANNEL="${channel}"

if ! is_allowed_channel "${channel}"; then
    # Bail before any network call. Don't trust the operator's typo.
    fail "invalid_channel"
fi

# --- read update.env ---------------------------------------------------------

GHCR_REPO="${GHCR_REPO:-ghcr.io/rmeadomavic/hydra}"
GPG_KEY_PATH="${GPG_KEY_PATH:-/etc/hydra/ota-signing.pub}"
MANIFEST_URL_BASE="${MANIFEST_URL_BASE:-https://github.com/rmeadomavic/Hydra/releases}"

if [[ -r "${UPDATE_ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    . "${UPDATE_ENV_FILE}"
fi

readonly GHCR_REPO GPG_KEY_PATH MANIFEST_URL_BASE

log "channel=${channel} ghcr=${GHCR_REPO} gpg_key=${GPG_KEY_PATH} starting update check"

# --- signing key sanity ------------------------------------------------------

# Missing key is a special case: we don't want the timer to fail (which
# would mark the unit failed and stop further checks until an operator
# clears it). Record the failure visibly and exit 0 so tomorrow's run
# tries again.
if [[ ! -r "${GPG_KEY_PATH}" ]]; then
    log "FAIL: missing_signing_key (no readable key at ${GPG_KEY_PATH})"
    write_last_update "failed" "missing_signing_key"
    exit 0
fi

# --- tmpdir for downloads ----------------------------------------------------

TMP_DIR="$(mktemp -d)"
MANIFEST_PATH="${TMP_DIR}/manifest.json"
SIG_PATH="${TMP_DIR}/manifest.json.sig"

# --- resolve manifest URLs ---------------------------------------------------

case "${channel}" in
    stable)
        # GitHub redirects /releases/latest/download/<asset> to the
        # newest non-prerelease asset on each request — no need to
        # parse the API for the latest tag.
        MANIFEST_URL="${MANIFEST_URL_BASE}/latest/download/manifest.json"
        SIG_URL="${MANIFEST_URL_BASE}/latest/download/manifest.json.sig"
        ;;
    beta)
        # Beta is a single rolling pre-release tag literally named
        # "beta" that gets republished each cycle. release-manifest.yml
        # picks this up via release.prerelease == true.
        MANIFEST_URL="${MANIFEST_URL_BASE}/download/beta/manifest.json"
        SIG_URL="${MANIFEST_URL_BASE}/download/beta/manifest.json.sig"
        ;;
    *)
        # Unreachable due to allowlist above, but keeps shellcheck happy.
        fail "invalid_channel"
        ;;
esac

# --- fetch manifest + sig ----------------------------------------------------

log "fetching ${MANIFEST_URL}"
if ! curl --fail --location --silent --show-error \
        --max-time 60 \
        --output "${MANIFEST_PATH}" "${MANIFEST_URL}"; then
    fail "manifest_fetch_failed"
fi

log "fetching ${SIG_URL}"
if ! curl --fail --location --silent --show-error \
        --max-time 60 \
        --output "${SIG_PATH}" "${SIG_URL}"; then
    fail "manifest_fetch_failed"
fi

# --- gpg verify --------------------------------------------------------------

# --no-default-keyring + --keyring pins trust to the operator-installed
# public key. We must NOT fall through to ~/.gnupg or the system keyring.
log "verifying signature against ${GPG_KEY_PATH}"
if ! gpg --batch \
        --no-default-keyring \
        --keyring "${GPG_KEY_PATH}" \
        --verify "${SIG_PATH}" "${MANIFEST_PATH}"; then
    fail "signature_invalid"
fi

# --- parse + validate manifest ----------------------------------------------

# Use python3 (CI runners + Jetson both ship it; jq may or may not be
# present). Emit channel/version/digest on three TSV lines so the bash
# parse is trivial.
PARSE_OUTPUT="$(MANIFEST_PATH="${MANIFEST_PATH}" python3 -c '
import json, os, re, sys
try:
    with open(os.environ["MANIFEST_PATH"], "r", encoding="utf-8") as f:
        m = json.load(f)
except (OSError, json.JSONDecodeError) as exc:
    print("PARSE_ERROR", str(exc), file=sys.stderr)
    sys.exit(1)
if not isinstance(m, dict):
    print("PARSE_ERROR not a JSON object", file=sys.stderr)
    sys.exit(1)
required = ("channel", "version", "digest")
missing = [k for k in required if k not in m or not isinstance(m[k], str) or not m[k]]
if missing:
    print(f"PARSE_ERROR missing fields: {missing}", file=sys.stderr)
    sys.exit(1)
digest = m["digest"]
# Hard-constrain digest to sha256:<64 hex>. Defends against a manifest
# that smuggles a shell metacharacter into the docker pull tag.
if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    print(f"PARSE_ERROR digest shape: {digest!r}", file=sys.stderr)
    sys.exit(1)
# Version: short SHA or semver-ish. Reject anything that could be
# interpreted by the shell.
if not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", m["version"]):
    print(f"PARSE_ERROR version shape: {m['version']!r}", file=sys.stderr)
    sys.exit(1)
# Channel: same allowlist as the bash side.
if m["channel"] not in ("stable", "beta"):
    print(f"PARSE_ERROR channel: {m['channel']!r}", file=sys.stderr)
    sys.exit(1)
print(m["channel"])
print(m["version"])
print(m["digest"])
' 2>&1)" || {
    log "manifest parse failure: ${PARSE_OUTPUT}"
    fail "manifest_invalid"
}

manifest_channel="$(printf '%s\n' "${PARSE_OUTPUT}" | sed -n '1p')"
manifest_version="$(printf '%s\n' "${PARSE_OUTPUT}" | sed -n '2p')"
manifest_digest="$(printf '%s\n' "${PARSE_OUTPUT}" | sed -n '3p')"

if [[ -z "${manifest_channel}" || -z "${manifest_version}" || -z "${manifest_digest}" ]]; then
    log "manifest parse produced empty fields: ${PARSE_OUTPUT}"
    fail "manifest_invalid"
fi

CURRENT_VERSION="${manifest_version}"
CURRENT_DIGEST="${manifest_digest}"

# --- channel match -----------------------------------------------------------

if [[ "${manifest_channel}" != "${channel}" ]]; then
    log "channel mismatch: manifest=${manifest_channel} box=${channel}"
    fail "channel_mismatch"
fi

# --- already current? --------------------------------------------------------

# The running container's version is exported into the systemd unit env
# as $HYDRA_VERSION (Dockerfile bakes the git SHA at build time). If
# that matches the manifest, we can skip the pull entirely.
running_version="${HYDRA_VERSION:-}"
if [[ -n "${running_version}" && "${running_version}" == "${manifest_version}" ]]; then
    log "already up to date (running=${running_version}, manifest=${manifest_version}); skipping pull"
    write_last_update "up_to_date"
    exit 0
fi

# --- docker pull -------------------------------------------------------------

PULL_REF="${GHCR_REPO}@${manifest_digest}"
log "docker pull ${PULL_REF}"
pull_start="$(date +%s)"
if ! docker pull "${PULL_REF}"; then
    pull_end="$(date +%s)"
    log "docker pull failed after $((pull_end - pull_start))s"
    fail "docker_pull_failed"
fi
pull_end="$(date +%s)"
log "docker pull ok in $((pull_end - pull_start))s"

# --- record success ----------------------------------------------------------

write_last_update "ok"
log "ok version=${manifest_version} digest=${manifest_digest} channel=${channel}"

exit 0
