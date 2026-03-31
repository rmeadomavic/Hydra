# SORCC RF Survey Dashboard — Audit & Next Steps

## Architecture Discovery

The **SORCC RF Survey Dashboard** is a separate application from Hydra Detect:

- **Device:** Raspberry Pi at `100.71.115.45` (Tailscale IP)
- **Jetson Hydra Dashboard:** `100.109.160.122:8080` — detection/tracking (this repo)
- **Pi RF Dashboard:** `100.71.115.45:8080` — RF survey/Kismet (separate codebase)
- **Likely stack:** Flask/Python serving static JS/CSS
- **Static files (probable):**
  - `/static/js/app.js`
  - `/static/js/operations.js`
  - `/static/js/map.js`
  - `/static/js/settings.js`
  - `/static/js/preflight.js`
  - `/static/css/` (stylesheets)
- **SSH access:** `pi@100.71.115.45` — password unknown (not `raspberry`, not `sorcc`)
- **Service:** likely `sorcc-dashboard` systemd unit
- **Source location:** likely `/home/pi/` or `/opt/sorcc-dashboard/`

The Pi source code is NOT in the Hydra repo. A future session needs SSH access
to the Pi to locate and modify the RF dashboard source files.

---

## Full 20-Item Audit (Three-Persona Review)

### CRITICAL — Breaks Operational Use

**1. WiFi stat shows "0" while in WiFi Survey mode**

The stat bar shows: Total=490, WiFi=0, Bluetooth=490, Other=0. You're in "WiFi
Survey" mode but the WiFi counter reads 0 and Bluetooth reads 490. This is either
a labeling bug (all BT devices being counted under Bluetooth even though the
active mode is WiFi Survey) or the WiFi radio isn't capturing. A Green Beret
glancing at this thinks WiFi is dead. The mode card says "WiFi Survey" but the
data says otherwise. This needs a clear explanation or a fix — if BT recon is
actually running, the mode badge should say "Bluetooth Recon" not "WiFi Survey",
OR the stat labels need to reflect what's actually being captured.

**2. GPS dot in header is YELLOW (no fix) but there's no prominent alert**

The GPS indicator top-right is yellow/amber while KISMET and LTE are green. GPS
has no fix. The map tab shows an orange text warning "Waiting for GPS fix — map
will center when acquired" but the header GPS pill is tiny. A general looking at
this dashboard has no immediate understanding that GPS is offline. On the
Preflight page it shows WARN for Network because of GPS. This should be more
visible on the main ops screen — a yellow pulsing badge or a persistent banner
when GPS has no fix.

**3. Device list signal bars are not showing signal strength — they're flat gray lines**

In the Detected Devices list, every device has a signal bar graphic that appears
as a flat horizontal gray/dark line, not a real signal strength indicator.
There's no RSSI/dBm data being displayed in the bars. A soldier needs to know if
a target device is 5 feet away or 500 feet away — blank bars are useless. The
signal column shows "pkts" (packet count) not signal strength. If signal data
isn't available for Bluetooth devices, the bar should say "N/A" or show the
signal value if it exists, or be removed entirely.

**4. TOP ACTIVE bar chart bars are all identical length**

In the "TOP ACTIVE" section, devices #1 through #8 all show green bars of nearly
identical length despite having very different packet counts (3993 vs 3301 — a
20% difference). The bar widths should visually scale to reflect the relative
activity difference. Right now they all look the same, which destroys the purpose
of having a bar chart.

### HIGH — Degrades Professional Credibility

**5. Device names truncated with "…" in TOP ACTIVE — no tooltip**

"ACI-UniversalCon…" in TOP ACTIVE is cut off. Hovering over it shows no tooltip.
An instructor demoing this to soldiers can't read the full device name without
clicking into the device. Add a tooltip on hover showing the full name.

**6. WiFi counter always 0 in stat tiles makes WIFI and OTHER tiles look broken**

Even if BT recon is the active mode, showing WiFi=0 and Other=0 looks like a
system failure. Either hide counters for inactive modes, or display them with a
"(inactive)" label so operators understand the 0 is expected.

**7. No device type icons in the device list**

The device list shows MAC addresses and names but no visual indicator of device
type (phone, laptop, IoT, BLE beacon, etc.). Kismet provides device type
classification. Adding icons would make scanning the list much faster.

**8. Map tab empty with no GPS — just a gray rectangle**

When GPS has no fix, the Map tab shows an empty gray area with orange text. This
wastes a full tab. Consider showing a message with instructions ("Connect GPS
antenna" or "Ensure clear sky view") or showing a non-geographic heatmap of
signal strength.

### MEDIUM — Polish for Demo/Funding Audiences

**9. Spectrum tab shows "No spectrum data available"**

The Full Spectrum tab displays nothing. If the SDR isn't connected, show a clear
status message: "RTL-SDR not detected — connect USB dongle to enable spectrum
analysis." If it IS connected but not capturing, show the last known scan or a
configuration prompt.

**10. Channel Activity chart area is blank**

The Channel Activity section under the spectrum area is empty. Same issue — needs
a clear empty state message or placeholder visualization.

**11. Dark theme is too dark — some text is nearly invisible**

Some secondary text (timestamps, MAC addresses) blends into the dark background.
The contrast ratio may not meet WCAG AA standards. Bump the dim text color from
current gray to at least `#999` or `rgba(255,255,255,0.6)`.

**12. No export button for device list**

An instructor after a demo wants to show results. There's no way to export the
detected device list as CSV or JSON from the dashboard. Add an export button.

**13. Settings page layout cramped on laptop screens**

The Settings page has form fields that run edge-to-edge without adequate spacing.
On a 13" laptop, the controls feel cramped. Add more padding and consider a
max-width container.

**14. No session timer showing how long the survey has been running**

There's no indication of survey duration. A general wants to know "how long have
you been scanning?" Add an elapsed time counter to the header or stats area.

### LOW — Nice-to-Have

**15. No dark/light theme toggle**

Military users often switch between indoor (bright) and outdoor (dark) use.
A quick theme toggle would help.

**16. No keyboard shortcuts**

Power users (instructors) would benefit from keyboard shortcuts for common
actions: start/stop survey, switch tabs, export data.

**17. Preflight page "Re-check" button styling inconsistent**

The Re-check button on the preflight page uses different styling than other
buttons in the dashboard.

**18. No sound/vibration alert when new devices are detected**

In a surveillance scenario, a soldier monitoring the dashboard while doing other
tasks needs an audio alert when a new device appears.

**19. Tab switching animation is jarring**

The tabs switch instantly with no transition. A subtle fade or slide would feel
more polished for demos.

**20. No "About" or version information visible**

There's no way to tell what version of the dashboard is running. Add a version
string to the footer or settings page.

---

## Instructions for Next Session

SSH from the Jetson (100.109.160.122) to the Pi (pi@100.71.115.45) to locate
and edit the SORCC dashboard source. Run:

```bash
ssh pi@100.71.115.45 'find /home/pi -name operations.js 2>/dev/null; sudo systemctl status sorcc-dashboard --no-pager | head -20'
```

Then fix all 20 audit items in that codebase. The audit list is in
`~/Hydra/docs/SORCC_DASHBOARD_AUDIT.md`.

### What's Already Done (Hydra Jetson Dashboard)
All 15 Hydra dashboard issues from the overnight session are fixed and deployed
on the Jetson at `100.109.160.122:8080`:
- Video stream (snapshot polling)
- Battery/position/status dots/footer
- Mode highlights, touch targets, text overflow
- XSS fixes, CSP hardening, auth bypass
- 954 tests passing
