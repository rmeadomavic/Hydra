# Over-the-air updates

Operators take Hydra units home after class. Without an update path, the
version they leave with is the last version they ever see. Issue
[#152](https://github.com/rmeadomavic/Hydra/issues/152) lands an OTA
pipeline in four staged PRs:

| PR  | Scope                                                                     |
| --- | ------------------------------------------------------------------------- |
| A   | Skeleton — systemd timer, channel file, `version`/`channel` on `/api/health` |
| B   | GPG-signed manifest verify + `docker pull` by image digest                |
| C   | A/B image-tag flip + `/api/health` consecutive-OK gate for promotion      |
| D   | Operator dashboard view (current version, channel switcher, history)      |

**This doc covers what PR-A actually ships.** Everything that performs
an update — signature verify, pull, restart, rollback — lands in PR-B
or later. PR-A's job is to make sure the timer fires, the channel is
readable, and operators can see the running version on the dashboard.

## What landed in PR-A

### Channel file — `/etc/hydra/channel`

Single-line plain text. One of:

- `stable` — default. Production release stream.
- `beta` — pre-release builds for instructors who want to dogfood.

If the file is missing, malformed, or empty, Hydra falls back to
`stable`. The health endpoint and the update script both read the file
defensively — neither will crash because `/etc/hydra/channel` is absent
on a fresh image.

To switch channels:

```bash
echo beta | sudo tee /etc/hydra/channel
# verify
curl -s http://localhost:8080/api/health | python3 -c \
  "import sys, json; print(json.load(sys.stdin)['channel'])"
```

You do **not** need to restart `hydra-detect` — every `/api/health`
request re-reads the file. PR-B's `platform-update.sh` reads it once per
timer run.

### Update env — `/etc/hydra/update.env`

Optional shell-sourceable file. PR-A only reads + logs these values;
PR-B will actually use them:

```bash
# /etc/hydra/update.env
GHCR_REPO=ghcr.io/rmeadomavic/hydra
GPG_KEY_PATH=/etc/hydra/ota-signing.pub
```

The systemd unit uses `EnvironmentFile=-/etc/hydra/update.env` (the
leading dash makes it optional), so a missing file is not a failure.

### Timer

```bash
sudo systemctl enable --now hydra-platform-update.timer
sudo systemctl status hydra-platform-update.timer
journalctl -u hydra-platform-update.service --since "1 hour ago"
```

- `OnCalendar=daily` — first run is shortly after `local-fs.target`,
  thereafter every 24 h.
- `RandomizedDelaySec=1800` — up to 30 min of jitter so a fleet doesn't
  hammer GHCR at exactly midnight UTC.
- `Persistent=true` — if the box was off when the timer should have
  fired, it fires once at next boot instead of silently skipping.

In PR-A the script just logs intent and exits 0. You'll see:

```
[platform-update] channel=stable ghcr=ghcr.io/rmeadomavic/hydra gpg_key=/etc/hydra/ota-signing.pub would check for updates
```

### Version on `/api/health`

Three new fields:

```json
{
  "version": "abc1234",
  "channel": "stable",
  "last_update": null
}
```

- `version` — the value of `$HYDRA_VERSION` in the running container.
  CI sets this to the git SHA at build time via Docker's `--build-arg
  HYDRA_VERSION=$GITHUB_SHA`. Local `docker build` without `--build-arg`
  falls through to `"dev"`.
- `channel` — current contents of `/etc/hydra/channel`, defaulting to
  `stable`.
- `last_update` — populated by PR-B's `platform-update.sh` after every
  successful (or failed) update attempt. Shape:
  `{"ts": <unix>, "status": "ok"|"failed", "version": "<sha>"}`. Stays
  `null` until the first update runs.

A malformed `last-update.json` returns `null` rather than 5xx-ing
`/api/health` — the OTA surface must never take a Jetson out of
rotation.

## What's deferred

- **GPG manifest verification + image-digest pin** — PR-B. Verifies a
  signed `manifest.json` from GHCR, extracts the digest, and runs
  `docker pull ghcr.io/rmeadomavic/hydra@sha256:<digest>`.
- **Healthcheck-gated A/B promotion** — PR-C. After pull, the new image
  is started under a new container name, must answer `body.status ==
  "ok"` on `/api/health` for N consecutive minutes, and only then does
  traffic flip to the new tag.
- **Dashboard view** — PR-D. Current/last-attempted version, channel
  switcher with audit log, manual "check now" button.
- **Public-internet relaxation** — issue #152 originally specified "no
  public internet required." That's relaxed by the GHCR choice in PR-A.
  Sites that need air-gap can put a Tailscale-net mirror in front of
  GHCR; the script only needs `$GHCR_REPO` to point at a reachable
  registry that serves the right OCI manifest.

## Files

```
scripts/platform-update.sh                  # the script (PR-A stub)
scripts/hydra-platform-update.service       # systemd oneshot
scripts/hydra-platform-update.timer         # daily timer
hydra_detect/observability/version_surface.py  # channel/last_update readers
```
