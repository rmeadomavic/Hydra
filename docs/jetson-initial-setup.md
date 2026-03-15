# Jetson Orin Nano — Initial Setup (JetPack Flash to Terminal)

This guide covers flashing JetPack onto a Jetson Orin Nano and completing the
Ubuntu first-boot wizard. When you're done here, you'll have a fresh desktop
with a terminal ready for the [Hydra software setup](jetson-setup-guide.md).

Written for students reproducing the Hydra Detect build from scratch.

## What You Need

- NVIDIA Jetson Orin Nano (Developer Kit or Super)
- microSD card (64 GB+ recommended, UHS-I or faster)
- Power supply (USB-C, 5V/3A minimum — the official adapter is recommended)
- Monitor, keyboard, mouse (for initial setup only)
- Wi-Fi network (or Ethernet)

## 1. Flash JetPack to the SD Card

Download the JetPack SD card image from NVIDIA's Jetson AI Lab:

> https://www.jetson-ai-lab.com/tutorials/initial-setup-jetson-orin-nano/

As of this writing, we use **JetPack 6.2.1**.

Flash the image to your microSD card using [Balena Etcher](https://etcher.balena.io/)
or the imaging tool of your choice. This takes 10-15 minutes depending on your
card speed.

Insert the flashed SD card into the Jetson's microSD slot (on the underside of
the carrier board) and connect power to boot.

## 2. First Boot — Ubuntu OOBE Wizard

The Jetson boots into Ubuntu's out-of-box setup. Walk through each screen:

### License Agreement
- Click **I Accept** to agree to the NVIDIA software license terms.

### Language
- Select **English** (or your preferred language). Click Continue.

### Keyboard Layout
- Select **English (US)** (or your layout). Click Continue.

### Wi-Fi
- Select your Wi-Fi network and enter the password. The Jetson needs internet
  for system updates and package installs later.

### Time Zone
- Select your time zone. Click Continue.

### User Account
- **Your name:** `sorcc`
- **Username:** `sorcc`
- **Password:** `sorcc`
- Check **Log in automatically**
- Click Continue

> **Note:** For classroom/lab Jetsons, we use `sorcc`/`sorcc` as the standard
> credentials. For field deployments, use a stronger password.

### Partition Size
- Accept the default partition size. Click Continue.

### Chromium Browser
- The system will offer to install Chromium. Click **Install** — you'll need a
  browser to access the Hydra web dashboard later.
- Wait a few minutes for the install to finish, then click **Close**.

The Jetson will apply initial settings and **reboot automatically**.

## 3. Post-Reboot Desktop Setup

After reboot, you land on the Ubuntu desktop.

### Online Accounts
- Click **Skip**.

### Ubuntu Pro
- Click **Skip**.

### System Info to Canonical
- Select **No, don't send system info**. Click Next.

### Location Services
- **Leave this OFF.** Hydra gets GPS from the flight controller over MAVLink,
  not from the Jetson's OS. Ubuntu location services just add unnecessary
  background network activity.

### Done
- Click **Done** to finish the wizard.

## 4. System Updates

A **Software Updater** popup will appear shortly after setup completes.

1. Click **Install Now**.
2. Enter password (`sorcc`) if prompted.
3. **Wait.** This update takes 20-30 minutes on a fresh JetPack install.
4. When prompted, click **Restart Now**.

The Jetson will show the NVIDIA boot screen with an update progress bar during
restart. This is normal — do not power off during this step.

## 5. Install Claude Code

Once the desktop comes back up, open a terminal (`Ctrl+Alt+T` or find Terminal
in the app menu).

Install Claude Code:

```bash
# Install Node.js (Claude Code requires it)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install Claude Code globally
sudo npm install -g @anthropic-ai/claude-code

# Launch it
claude
```

Follow the authentication prompts to connect Claude Code to your Anthropic
account.

## What's Next

Claude Code is now running in the terminal. From here, it can handle the rest
of the Hydra Detect setup — cloning the repo, building Docker images, and
configuring the system. See [jetson-setup-guide.md](jetson-setup-guide.md) for
the full software setup process.

---

*Guide tested on Jetson Orin Nano 8 GB, JetPack 6.2.1, 2026-03-15*
