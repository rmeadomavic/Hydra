---
title: "Flash JetPack to Jetson"
description: "Flash the JetPack SD card image onto a Jetson Orin Nano and complete the Ubuntu first-boot wizard."
sidebarTitle: "Jetson flash"
icon: "microchip"
---

This guide covers flashing JetPack onto a Jetson Orin Nano and completing the Ubuntu first-boot wizard. When done, you'll have a fresh desktop with a terminal ready for the [Hydra software setup](/setup/jetson-docker).

Written for operators reproducing the Hydra Detect build from scratch.

## What you need

- NVIDIA Jetson Orin Nano (Developer Kit or Super)
- microSD card (64 GB+ recommended, UHS-I or faster)
- Power supply (USB-C, 5V/3A minimum. The official adapter is recommended)
- Monitor, keyboard, mouse (for initial setup only)
- Wi-Fi network (or Ethernet)

## Step 0: QSPI Firmware Update (New Jetsons Only)

New out-of-box Jetson Orin Nanos ship with old QSPI firmware that cannot boot JetPack 6.x. If your Jetson has never been updated, you must update the QSPI first. Jetsons that have already run JetPack 6.x can skip this step.

**How to tell if you need this:** Insert a JetPack 6.x SD card and power on. If the Jetson fails to boot (no NVIDIA logo, no Ubuntu setup), the QSPI needs updating.

### QSPI Update Procedure

1. Download the **JetPack 5.1.3** SD card image from [NVIDIA Jetson Downloads](https://developer.nvidia.com/embedded/downloads). This is the last JetPack version compatible with old QSPI firmware.

2. Flash JetPack 5.1.3 to a microSD card using [Balena Etcher](https://etcher.balena.io/).

3. Insert the JP 5.1.3 card into the Jetson and power on. Walk through the Ubuntu first-boot wizard (language, keyboard, Wi-Fi, user account). Use `sorcc`/`sorcc` for credentials.

4. Open a terminal and install the QSPI updater:

```bash
sudo apt-get update
sudo apt-get install nvidia-l4t-jetson-orin-nano-qspi-updater
```

<Warning>
The package name uses a lowercase L (`l4t`), not the number one (`14t`). This is a common typo that causes `Unable to locate package` errors.
</Warning>

5. Reboot:

```bash
sudo reboot
```

6. The Jetson flashes the QSPI firmware during boot. When complete, the board **halts** (screen goes blank or shows UEFI). This is expected. Disconnect power.

7. Remove the JP 5.1.3 card. Insert your JetPack 6.x card (or the golden image card). Power on. JetPack 6.x boots normally.

### Reusing One JP 5.1.3 Card for Multiple Jetsons

You can reuse the same JP 5.1.3 SD card across multiple Jetsons. After the first Jetson completes the Ubuntu OOBE wizard, subsequent Jetsons boot directly to the desktop with the same user account. The QSPI updater package is cached in `/var/cache/apt/archives/`, so the `apt install` step is near-instant on devices 2+.

Workflow for multiple devices:

1. Flash one JP 5.1.3 SD card.
2. Boot Jetson #1, complete OOBE, install QSPI updater, reboot, wait for halt.
3. Power off. Move the same card to Jetson #2. Boot, open terminal, `apt install` the updater, reboot, halt.
4. Repeat for remaining Jetsons.

The QSPI update does not modify the SD card. Each new Jetson still has old factory QSPI, so JP 5.1.3 boots on all of them.

<Steps>

<Step title="Flash JetPack to the SD card">

Download the JetPack SD card image from NVIDIA's Jetson AI Lab:

> https://www.jetson-ai-lab.com/tutorials/initial-setup-jetson-orin-nano/

As of this writing, we use **JetPack 6.2.1**.

Flash the image to your microSD card using [Balena Etcher](https://etcher.balena.io/) or the imaging tool of your choice. This takes 10-15 minutes depending on your card speed.

Insert the flashed SD card into the Jetson's microSD slot (on the underside of the carrier board) and connect power to boot.

</Step>

<Step title="First boot: Ubuntu OOBE wizard">

The Jetson boots into Ubuntu's out-of-box setup. Walk through each screen:

**License Agreement** — Click **I Accept**.

**Language** — Select **English** (or your preferred language). Click Continue.

**Keyboard Layout** — Select **English (US)** (or your layout). Click Continue.

**Wi-Fi** — Select your Wi-Fi network and enter the password. The Jetson needs internet for system updates and package installs later.

**Time Zone** — Select your time zone. Click Continue.

**User Account:**
- **Your name:** `sorcc`
- **Username:** `sorcc`
- **Password:** `sorcc`
- Check **Log in automatically**
- Click Continue

<Warning>
For bench/lab Jetsons, we use `sorcc`/`sorcc` as the standard credentials. For field deployments, use a stronger password.
</Warning>

**Partition Size** — Accept the default. Click Continue.

**Chromium Browser** — Click **Install**. You'll need a browser to access the Hydra web dashboard later. Wait for the install to finish, then click **Close**.

The Jetson will apply initial settings and reboot automatically.

</Step>

<Step title="Post-reboot desktop setup">

After reboot, you land on the Ubuntu desktop.

**Online Accounts** — Click **Skip**.

**Ubuntu Pro** — Click **Skip**.

**System Info to Canonical** — Select **No, don't send system info**. Click Next.

**Location Services**

<Note>
**Leave this OFF.** Hydra gets GPS from the flight controller over MAVLink, not from the Jetson's OS. Ubuntu location services just add unnecessary background network activity.
</Note>

**Done** — Click **Done** to finish the wizard.

</Step>

<Step title="System updates">

A **Software Updater** popup will appear shortly after setup completes.

1. Click **Install Now**.
2. Enter password (`sorcc`) if prompted.
3. Wait. This update takes 20-30 minutes on a fresh JetPack install.
4. When prompted, click **Restart Now**.

<Warning>
The Jetson will show the NVIDIA boot screen with an update progress bar during restart. This is normal. Do not power off during this step.
</Warning>

</Step>

</Steps>

## What's next

The Jetson is ready. Open a terminal (`Ctrl+Alt+T`) and proceed to the [Hydra software setup guide](/setup/jetson-docker) to clone the repo, build the Docker image, and get Hydra Detect running.

---

*Guide tested on Jetson Orin Nano 8 GB, JetPack 6.2.1, 2026-03-15*
