# Hydra TAK 101

Get Hydra detections showing up on ATAK tablets. This guide covers the
basics — what TAK is, how to set it up, and what you'll see.

---

## What is TAK?

**TAK** (Team Awareness Kit) is a shared map application used by the military
and first responders. **ATAK** is the Android version. When Hydra's TAK output
is enabled, every detection (person, vehicle, boat, etc.) appears as a marker
on every connected ATAK tablet in real time — no voice radio needed.

Hydra sends **Cursor on Target (CoT)** messages — an XML protocol that all TAK
clients understand. These messages carry:

- **Drone position** — a blue friendly-air icon showing where the vehicle is
- **Detection markers** — yellow icons at the estimated GPS position of each
  detected object, labeled with class name and confidence
- **Video feed** — your RTSP stream auto-discoverable in ATAK's video player

---

## Quick Start (Same WiFi)

This is the simplest setup — Jetson and phone on the same WiFi network.
Tested and verified on Pixel 7 Pro with ATAK-CIV.

### 1. Enable TAK in Hydra

**Option A — config.ini:**
```ini
[tak]
enabled = true
callsign = HYDRA-1
```

**Option B — Web UI:** Open the Hydra dashboard → Operations panel →
flip the **TAK/ATAK** toggle to ON.

### 2. Install ATAK on your phone

1. Go to **https://tak.gov** and create a free account (any email works)
2. Log in → **Products → ATAK → ATAK-CIV** → download the APK
3. On your phone: **Settings → Apps → Chrome → Install Unknown Apps → Allow**
4. Open the downloaded APK to install
5. Accept all permissions (location, storage, etc.)

### 3. Launch ATAK

1. Set your callsign (e.g., "ALPHA-1")
2. Pick a team color and role (defaults are fine)
3. GPS source: Internal GPS
4. Accept default maps

### 4. Connect to the same WiFi

Make sure your phone is on the **same WiFi network** as the Jetson.
That's it — no network configuration needed in ATAK.

ATAK listens on the standard SA multicast group (`239.2.3.1:6969`) by
default. Hydra sends to that same group. They find each other automatically.

### 5. What you'll see

- A **blue aircraft icon** labeled with your Hydra callsign (e.g., "HYDRA-1")
  at the vehicle's GPS position
- **Yellow markers** appearing when Hydra detects objects — tap one to see
  the label, confidence score, and track ID
- Markers **move** as tracked objects move and **disappear** after the stale
  timeout (60 seconds by default)

### Android battery tip

Android may throttle ATAK's network access in the background. To prevent
dropped markers:

**Settings → Apps → ATAK → Battery → Unrestricted**

---

## Different Network Setup (VPN / Cellular / Tailscale)

When the ATAK device and Jetson are **not on the same WiFi** — for example,
the drone is in the field on cellular and the operator is at a command post —
multicast won't work. Use **unicast** instead.

### On the Hydra side

Add the ATAK device's IP to `config.ini`:

```ini
[tak]
enabled = true
callsign = HYDRA-1
unicast_targets = <JETSON_IP>:4242
```

Multiple targets are comma-separated:
```ini
unicast_targets = <JETSON_IP>:4242, <JETSON_IP>:4242
```

Use the Tailscale IP, VPN IP, or any routable IP for the ATAK device.

### On the ATAK side

You need to tell ATAK to listen on the unicast port:

1. Hamburger menu (top-left) → **Settings**
2. **Network Preferences → Network Connection Preferences**
3. Tap **Add** under inputs
4. Protocol: **UDP**, Address: **0.0.0.0**, Port: **4242**
5. Enable it

That's it. Hydra sends CoT directly to the device's IP — no multicast needed.

---

## Web UI Controls

### Settings view

Click **TAK/ATAK** in the settings sidebar to configure:

| Setting | What it does |
|---------|-------------|
| `enabled` | Master on/off switch |
| `callsign` | Name shown on ATAK map (e.g., "HYDRA-1", "BLUE-UAS-3") |
| `multicast_group` | UDP multicast address (default `239.2.3.1`) |
| `multicast_port` | UDP multicast port (default `6969`) |
| `unicast_targets` | Comma-separated `host:port` for direct UDP delivery |
| `emit_interval` | Seconds between CoT updates per track (default 2.0) |
| `sa_interval` | Seconds between drone position beacons (default 5.0) |
| `stale_detection` | Seconds before a detection marker expires (default 60) |
| `stale_sa` | Seconds before the drone icon expires (default 30) |
| `advertise_host` | IP to put in RTSP video feed announcement (leave blank to skip) |

### Operations view

The **TAK/ATAK toggle** in the operations panel starts and stops CoT output
at runtime — no restart required. When active, it shows the callsign and
the number of CoT events sent.

---

## What Appears on ATAK

### Drone icon (Self-SA)

| Field | Value |
|-------|-------|
| Icon | Blue friendly aircraft |
| Type code | `a-f-A-M-F-Q` (friendly air military UAV) |
| Updates | Every `sa_interval` seconds |
| Shows | Callsign, heading, speed |

### Detection markers

| Field | Value |
|-------|-------|
| Icon | Yellow diamond (unknown affiliation) |
| Type codes | Person: `a-u-G-U-C-I`, Vehicle: `a-u-G-E-V-C`, Boat: `a-u-S-X` |
| Updates | Every `emit_interval` seconds per tracked object |
| Shows | Label, confidence, track ID (tap marker for details) |
| Position | GPS-projected from drone position + camera geometry |
| Accuracy | ~50 m circular error (honest about camera projection uncertainty) |
| Expires | After `stale_detection` seconds if not re-detected |

### Video feed

If `advertise_host` is set and RTSP is running, Hydra announces the video
stream. In ATAK: **Tools → Video** — the feed should appear automatically.

---

## ATAK Versions

| Client | Platform | Where to get it | Notes |
|--------|----------|----------------|-------|
| **ATAK-CIV** | Android | tak.gov (free account) | Recommended for testing |
| **ATAK** (gov) | Android | tak.gov (.mil/.gov email) | Same CoT protocol, extra plugins |
| **iTAK** | iOS | Apple App Store | Works but multicast less reliable |
| **WinTAK** | Windows | tak.gov | Great for instructor station, big screen |

All clients receive the same CoT messages. No special configuration needed
per client — if it can receive UDP on `239.2.3.1:6969`, it sees Hydra's data.

---

## Troubleshooting

### No markers appear

1. **Is TAK enabled?** Check config.ini or the web UI toggle
2. **Is Hydra sending?** Run on the Jetson:
   ```bash
   sudo tcpdump -i any -c 3 'udp and port 6969'
   ```
   You should see packets every few seconds.
3. **Same network?** Phone and Jetson must be on the same WiFi for multicast.
   Run `ping <phone-ip>` from the Jetson to verify connectivity.
4. **AP isolation?** Some routers block device-to-device traffic on WiFi.
   Check your router settings for "client isolation" or "AP isolation" and
   disable it.

### Markers appear then disappear

- **Battery optimization:** Android is killing ATAK's multicast listener.
  Settings → Apps → ATAK → Battery → Unrestricted.
- **Screen off:** Keep ATAK in the foreground during testing.

### Detection markers are in the wrong place

Detection positions are estimated from the drone's GPS + camera geometry.
Accuracy depends on:
- GPS quality (need 3D fix, HDOP < 2.0)
- Altitude accuracy (used to estimate ground distance)
- Camera FOV setting in config.ini (`hfov_deg`, default 60)

### Unicast not working

- Verify the target IP is reachable: `ping <target-ip>`
- Verify the port isn't firewalled on the receiving device
- In ATAK, confirm the UDP input is added and enabled
- Check Hydra logs: `curl http://localhost:8080/api/logs?lines=20&level=WARNING`

---

## Glossary

| Term | Meaning |
|------|---------|
| **TAK** | Team Awareness Kit — shared map platform |
| **ATAK** | Android TAK — the phone/tablet client |
| **CoT** | Cursor on Target — XML protocol for position/event data |
| **SA** | Situational Awareness — knowing where everyone/everything is |
| **Self-SA** | Broadcasting your own position to the team |
| **Multicast** | One sender, many receivers on the same network (UDP 239.2.3.1:6969) |
| **Unicast** | One sender, one specific receiver by IP address |
| **Stale** | When a CoT event expires and the marker disappears from the map |
| **MIL-STD-2525** | Military standard for map symbology — determines icon shapes and colors |
| **Callsign** | Human-readable identifier shown on the map (e.g., "HYDRA-1") |
