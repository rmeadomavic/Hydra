---
title: "QSPI Firmware Update"
description: "Update QSPI firmware on new Jetson Orin Nano devices so they can boot JetPack 6.x."
sidebarTitle: "QSPI update"
icon: "bolt"
---

# QSPI Firmware Update — Jetson Orin Nano

New out-of-box Jetson Orin Nanos ship with old QSPI firmware incompatible with JetPack 6.x (L4T R36.x). The QSPI must be updated before the Jetson can boot a JetPack 6 SD card or golden image.

No host PC required. The update runs from a JetPack 5.1.3 SD card booted on the Jetson itself.

## What You Need

- JetPack 5.1.3 SD card image ([download from NVIDIA](https://developer.nvidia.com/embedded/downloads))
- One microSD card (used temporarily for the update, then reused)
- Monitor, keyboard for first device only (subsequent devices boot to desktop automatically)
- Wi-Fi or Ethernet (for the `apt install` on the first device)

## Single Device Procedure

1. Flash JetPack 5.1.3 to a microSD card using Etcher or `dd`.
2. Insert the card into the Jetson. Connect monitor, keyboard, power.
3. Complete the Ubuntu first-boot wizard. Use `sorcc`/`sorcc` for credentials.
4. Open a terminal (`Ctrl+Alt+T`):

```bash
sudo apt-get update
sudo apt-get install nvidia-l4t-jetson-orin-nano-qspi-updater
sudo reboot
```

<Warning>
The package name uses lowercase L: `l4t`, not `14t`. Common typo.
</Warning>

5. The Jetson flashes the QSPI during boot, then **halts** (blank screen or UEFI prompt). This is expected.
6. Disconnect power. Remove the JP 5.1.3 card. Insert the JetPack 6.x or golden image card. Power on.

## Multi-Device Pipeline (Reuse One Card)

One JP 5.1.3 SD card handles all devices. After the first Jetson completes the Ubuntu OOBE, the card boots directly to the desktop on subsequent Jetsons (same user account, no re-setup). The QSPI updater package is cached after the first download.

### Assembly Line

| Device | Steps | Time |
|--------|-------|------|
| Jetson #1 | Boot JP 5.1.3 → OOBE wizard → `apt install` updater → reboot → halt | ~10 min |
| Jetson #2 | Boot JP 5.1.3 (skips OOBE) → `apt install --reinstall` updater → reboot → halt | ~5 min |
| Jetson #3+ | Same as #2 | ~5 min each |

<Warning>
On every device after #1 you must pass `--reinstall`. The package is already
marked installed on the reused SD card, so plain `apt install` is a no-op and
the post-install QSPI flash hook won't re-run — leaving factory QSPI intact.
</Warning>

```bash
# Jetson #2+ — reuse the same JP 5.1.3 SD card
sudo apt-get install --reinstall nvidia-l4t-jetson-orin-nano-qspi-updater
sudo reboot
```

After QSPI update, swap in the golden image card and boot. Per-device customization: set callsign via the setup wizard at `/setup` or edit `config.ini` directly.

## Verification

After booting JetPack 6.x, confirm the update:

```bash
cat /etc/nv_tegra_release
# Expected: R36 (release), REVISION: 4.x
```

If this shows R36.4.x, the QSPI is current and JetPack 6.x is running.

## Troubleshooting

### `Unable to locate package nvidia-l4t-jetson-orin-nano-qspi-updater`

Check the package name for typos (lowercase L, not number 1). Run `sudo apt-get update` first. Ensure the Jetson has internet access.

### Board does not halt after reboot

If the board reboots back to JP 5.1.3 desktop instead of halting, the QSPI update may not have triggered. Re-run:

```bash
sudo apt-get install --reinstall nvidia-l4t-jetson-orin-nano-qspi-updater
sudo reboot
```

### JetPack 6.x still fails to boot after QSPI update

Re-flash the JetPack 6.x SD card. A corrupted flash is more common than a failed QSPI update. Use Etcher with verify enabled.

---

*Procedure verified for Jetson Orin Nano 8 GB, JetPack 5.1.3 → JetPack 6.2.1, April 2026*
