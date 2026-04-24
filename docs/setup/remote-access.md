---
title: "Remote Access (Tailscale)"
description: "Set up Tailscale SSH so operators and maintainers can manage Jetsons remotely without a monitor."
sidebarTitle: "Remote access"
icon: "globe"
keywords:
  - tailscale
  - ssh
  - remote
  - headless
  - sync
---

This guide sets up Tailscale-based SSH remote access so you can manage a Jetson from any laptop. No monitor, no keyboard, no local network required. Tailscale creates a secure peer-to-peer VPN between your devices.

For initial Jetson setup, see [Docker install](/setup/jetson-docker) first.

## Prerequisites

- Jetson with Hydra Detect installed and running
- A free [Tailscale account](https://tailscale.com) (one account covers the whole team)
- Internet access on both the Jetson and your laptop
- `sudo` access on the Jetson

## Part 1: Jetson Setup

<Steps>

<Step title="Run the Tailscale setup script">

SSH into your Jetson (or use a monitor) and run:

```bash
cd ~/Hydra
sudo bash scripts/setup_tailscale.sh
```

The script will install Tailscale, enable the SSH server, start Tailscale, and print a login URL.

Open the URL in a browser on any device to authenticate the Jetson to your Tailscale network.

<Tip>
**Provisioning multiple Jetsons?** Generate an auth key in the [Tailscale admin console](https://login.tailscale.com/admin/settings/keys) and pass it to the script:

```bash
sudo bash scripts/setup_tailscale.sh --authkey tskey-auth-xxxxx --hostname hydra-jetson-03
```

This skips the interactive login step. Useful for setting up a fleet of Jetsons in one session.
</Tip>

</Step>

<Step title="Note the Tailscale IP">

After setup completes, the script prints a summary:

```text
  ┌──────────────────────────────────────────────┐
  │  Tailscale IP:  <JETSON_IP>
  │  Hostname:      hydra-jetson
  │  SSH command:   ssh sorcc@<JETSON_IP>
  │  Tailscale SSH: ssh sorcc@hydra-jetson
  │  Dashboard:     http://<JETSON_IP>:8080
  └──────────────────────────────────────────────┘
```

Save the Tailscale IP or hostname.

</Step>

<Step title="Verify Tailscale persists across reboots">

Tailscale is configured to start on boot automatically. Verify:

```bash
sudo systemctl is-enabled tailscaled
# Expected: enabled
```

</Step>

</Steps>

## Part 2: Laptop Setup

<Steps>

<Step title="Install Tailscale on your laptop">

<Tabs>

<Tab title="Windows / WSL">
Download and install [Tailscale for Windows](https://tailscale.com/download/windows). Sign in with the same account you used for the Jetson.

If using WSL, Tailscale on the Windows host is sufficient. WSL shares the host's network.
</Tab>

<Tab title="macOS">
```bash
brew install --cask tailscale
```

Or download from [tailscale.com/download/mac](https://tailscale.com/download/mac). Sign in with the same Tailscale account.
</Tab>

<Tab title="Linux">
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
</Tab>

</Tabs>

</Step>

<Step title="Test the connection">

Once both devices are on the same Tailscale network:

```bash
# By Tailscale IP
ssh sorcc@<JETSON_IP>

# By Tailscale hostname (if using Tailscale SSH)
ssh sorcc@hydra-jetson

# Access the dashboard from your browser
# http://<JETSON_IP>:8080
```

<Note>
The default password for the `sorcc` user is `sorcc`. Change it after first login with `passwd`.
</Note>

</Step>

</Steps>

## Part 3: One-Command Sync

The `hydra_sync.sh` script pushes code updates from your laptop to a Jetson over Tailscale (or any SSH connection), rebuilds the Docker image, and restarts the service.

```bash
# From your laptop, in the Hydra repo
bash scripts/hydra_sync.sh hydra-jetson
```

This runs:
1. `git pull` on the Jetson
2. `docker build` to rebuild the image
3. `systemctl restart hydra-detect` to apply changes

**Common options:**

| Flag | Description |
|------|-------------|
| `--no-rebuild` | Skip Docker rebuild (just pull code and restart) |
| `-b develop` | Pull a different branch |
| `--dry-run` | Show commands without executing |
| `-u admin` | Use a different SSH user |

```bash
# Quick code-only update (skip Docker rebuild)
bash scripts/hydra_sync.sh --no-rebuild hydra-jetson

# Sync a feature branch
bash scripts/hydra_sync.sh -b develop hydra-jetson

# Preview what will happen
bash scripts/hydra_sync.sh --dry-run hydra-jetson
```

## SSH Key Setup (optional)

For password-less SSH, copy your public key to the Jetson:

```bash
ssh-copy-id sorcc@hydra-jetson
```

After this, `ssh` and `hydra_sync.sh` will connect without a password prompt.

<Tip>
If you enabled Tailscale SSH (`--ssh`, the default), you can skip this step. Tailscale handles authentication through your Tailscale account instead of SSH keys.
</Tip>

## Troubleshooting

<AccordionGroup>

<Accordion title="Cannot reach Jetson over Tailscale">
```
ssh: connect to host <JETSON_IP> port 22: Connection timed out
```
**Fix:** Check that Tailscale is running on both devices:
```bash
# On your laptop
tailscale status

# On the Jetson (if you have local access)
sudo tailscale status
```
Both devices must be logged into the same Tailscale account.
</Accordion>

<Accordion title="Tailscale login URL not appearing">
If `tailscale up` hangs without printing a URL, check that the Jetson has internet access:
```bash
ping -c 3 tailscale.com
```
If DNS is failing, try:
```bash
sudo tailscale up --login-server=https://controlplane.tailscale.com
```
</Accordion>

<Accordion title="Permission denied (publickey)">
```
sorcc@<JETSON_IP>: Permission denied (publickey).
```
**Fix:** Password authentication may be disabled. Re-run the setup script to enable it, or use Tailscale SSH:
```bash
ssh sorcc@hydra-jetson
```
</Accordion>

<Accordion title="hydra_sync.sh fails during Docker build">
```
Error: Cannot reach sorcc@hydra-jetson
```
**Fix:** Verify SSH access works manually first:
```bash
ssh sorcc@hydra-jetson "echo ok"
```
If the build itself fails, SSH in and check Docker logs:
```bash
ssh sorcc@hydra-jetson
cd ~/Hydra && docker build --network=host -t hydra-detect:latest .
```
</Accordion>

<Accordion title="Tailscale IP changed after reboot">
Tailscale IPs are stable and do not change between reboots. If you see a different IP, the device may have been re-authenticated as a new node. Check the [Tailscale admin console](https://login.tailscale.com/admin/machines) and remove duplicate entries.
</Accordion>

</AccordionGroup>

---

*Guide tested on Jetson Orin Nano Super 8GB, JetPack 6.2.1, L4T R36.4.7, Tailscale 1.78, 2026-03-16*
