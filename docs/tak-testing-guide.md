# TAK/ATAK Testing Guide for Hydra Detect

How to test Hydra's CoT output at every level — from desk verification to
full mesh deployment with student tablets.

---

## Testing Phases

| Phase | What | Client | Network |
|-------|------|--------|---------|
| 1 | Verify CoT XML packets | tcpdump + Python script | Jetson only |
| 2 | Desktop client | WinTAK on laptop | Same WiFi |
| 3 | Personal mobile | ATAK-CIV on Android phone | Same WiFi |
| 4 | iOS alternative | iTAK on iPhone/iPad | Same WiFi |
| 5 | Mesh integration | ATAK tablets on OpenMANET | Mesh WiFi AP |
| 6 | Course deployment | ATAK on student devices | FreeTAKServer relay |

---

## Phase 1: Verify CoT Output (No TAK Client)

### Enable TAK in Hydra

Set in `config.ini`:
```ini
[tak]
enabled = true
callsign = HYDRA-1
```

Or toggle it on at runtime from the web UI: **Operations panel → TAK/ATAK toggle**.

### tcpdump on the Jetson

```bash
# See all CoT multicast traffic (run outside Docker, or inside with host networking)
sudo tcpdump -i any -A udp port 6969

# Filter to just the multicast group
sudo tcpdump -i any -A 'udp and dst 239.2.3.1 and port 6969'

# Capture to file for analysis
sudo tcpdump -i any -w /tmp/cot_capture.pcap udp port 6969
```

You should see XML events every few seconds (self-SA) and on every detection
cycle (detection markers).

### Python multicast listener

Run on any machine on the same network:

```python
import socket, struct

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('', 6969))

mreq = struct.pack('4sL', socket.inet_aton('239.2.3.1'), socket.INADDR_ANY)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

print("Listening for CoT on 239.2.3.1:6969 ...")
while True:
    data, addr = sock.recvfrom(65535)
    print(f"\n--- From {addr} ---")
    print(data.decode('utf-8', errors='replace'))
```

### What to verify

- Self-SA events: `type="a-f-A-M-F-Q"` (friendly air UAV) every 5 seconds
- Detection markers: `type="a-u-G-U-C-I"` (person), `type="a-u-G-E-V-C"` (vehicle), etc.
- Valid XML with `<event>`, `<point lat="..." lon="...">`, `<detail>`
- Stale time is in the future, time/start match
- Callsign appears in `<contact callsign="HYDRA-1"/>`

---

## Phase 2: WinTAK on Windows Laptop (Most Reliable)

WinTAK is the most reliable first TAK client test — no multicast lock issues,
big screen, can run Wireshark alongside.

### Setup

1. Create a free account at **https://tak.gov**
2. Download **WinTAK** from Products → WinTAK
3. Install (standard Windows installer)
4. First-run wizard: set callsign, team color, role

### Configure multicast input

1. **Settings** (gear icon) → **Network Preferences**
2. Under **Multicast / Input**, verify or add:
   - Address: `239.2.3.1`
   - Port: `6969`
   - Protocol: UDP
3. If Windows Firewall prompts, click **Allow**
4. If multicast doesn't work, add an inbound firewall rule for UDP 6969

### Network

- Laptop must be on the **same WiFi / wired network** as the Jetson
- Disable VPN software during testing (VPNs capture multicast)
- Wired Ethernet is more reliable than WiFi for multicast

### What you should see

- A blue drone icon labeled "HYDRA-1" at the vehicle's GPS position
- Yellow/red detection markers appearing when Hydra detects objects
- Markers moving as tracked objects move
- Markers disappearing after the stale timeout (60s default)

---

## Phase 3: ATAK-CIV on Android Phone

> **Verified 2026-03-26:** Pixel 7 Pro on same WiFi as Jetson — multicast
> worked immediately with zero ATAK network configuration. ATAK's default SA
> multicast input on `239.2.3.1:6969` is already enabled out of the box.

### Install ATAK-CIV

1. Create a free account at **https://tak.gov** (any email works for CIV version)
2. Log in, go to **Products → ATAK → ATAK-CIV**, download the APK (~200 MB)
3. Transfer to phone (download directly in Chrome, or USB/Google Drive)
4. On the phone: **Settings → Apps → Chrome → Install Unknown Apps → Allow**
5. Open the APK to install, accept all permissions

### First launch

1. Set callsign (e.g., "TEST-1")
2. Select team color and role (defaults are fine)
3. GPS source: Internal GPS
4. Accept default maps

### Network — just same WiFi

Connect the phone to the **same WiFi network** as the Jetson. That's it.

ATAK's default SA multicast input on `239.2.3.1:6969` is already enabled —
no manual network configuration needed. You should see the drone icon
immediately.

### Android battery settings (recommended)

Android can kill ATAK's multicast listener in the background. To prevent this:

- **Settings → Apps → ATAK → Battery → "Unrestricted"** (prevents Android
  from throttling ATAK's network when screen is off)
- Inside ATAK: **Settings → Display → Keep screen on** during testing

### If markers don't appear

Try these in order:

1. **Verify Hydra is sending:** Run the Python listener (Phase 1) on the Jetson
   to confirm packets are going out
2. **Check WiFi AP isolation:** Some routers block device-to-device traffic
   by default (especially guest networks). Disable "client isolation" or
   "AP isolation" in your router settings.
3. **Use unicast fallback:** If multicast just won't work on your network:
   - In Hydra `config.ini`: `unicast_targets = <phone-ip>:4242`
   - In ATAK: Hamburger menu → Settings → Network Preferences →
     Network Connection Preferences → Add Input → Protocol: UDP,
     Address: `0.0.0.0`, Port: `4242`

| Problem | Fix |
|---------|-----|
| No markers appear | Check `tcpdump` on Jetson — are packets being sent? |
| Packets sent but not received | Disable AP isolation on router |
| Intermittent reception | Disable battery optimization, keep screen on |
| Works on Ethernet, not WiFi | WiFi AP may not bridge multicast — try unicast fallback |

---

## Phase 4: iTAK on iOS (Alternative)

### Setup

1. Install **iTAK** from the Apple App Store (free, by PAR Government Systems)
2. Set callsign and team
3. Settings → Network → Add connection:
   - Multicast: UDP, `239.2.3.1`, port `6969`
   - Or unicast: UDP, `0.0.0.0`, port `4242`

### iOS multicast caveats

- iOS is **more aggressive** than Android about killing multicast on WiFi
- Keep iTAK in the foreground — iOS suspends multicast for backgrounded apps
- Unicast is more reliable on iOS — use `unicast_targets` in Hydra config
- Less feature-rich than ATAK but fine for demos

---

## Phase 5: OpenMANET Mesh Integration

### Architecture

```
Jetson (Hydra) ─── ETH/WiFi ──→ [Mesh Node RPi]
                                      │
                                    bat0 (BATMAN-V mesh)
                                      │
                              ┌───────┼───────┐
                              │       │       │
                         [Node 2] [Node 3] [Node 4]
                              │                │
                           WiFi AP          WiFi AP
                              │                │
                         ATAK Tablet      ATAK Tablet
```

### Connect ATAK tablets to the mesh

ATAK devices can't use WiFi HaLow directly. Options:

**Option A: WiFi AP on a mesh node (recommended)**
1. Configure an RPi mesh node to run a 2.4/5 GHz WiFi AP via `hostapd`
2. Bridge the WiFi AP to the `bat0` BATMAN-V interface
3. ATAK tablets connect to this AP and get IPs in `10.41.0.0/16`
4. Tablets are now logically on the mesh

**Option B: USB Ethernet on tablet**
- Some Android tablets support USB-C Ethernet adapters
- Connect tablet to mesh node via Ethernet
- Avoids WiFi multicast issues entirely

### Multicast on the mesh

BATMAN-V handles unicast well. Multicast requires attention:

```bash
# On each mesh node — force multicast flooding (simplest for small meshes)
echo 1 > /sys/class/net/bat0/mesh/multicast_forceflood

# Verify multicast status
batctl meshif bat0 multicast
```

If `adsbcot` already works on your mesh (ADS-B → CoT to ATAK), then Hydra's
CoT will work the same way — it uses the same multicast group and port.

### Verification

1. Run Python multicast listener on a mesh node to confirm packets traverse hops
2. Run ATAK on a tablet connected to a mesh WiFi AP
3. Verify drone SA and detection markers appear

---

## Phase 6: Course Deployment with FreeTAKServer

For a training course with many students, **FreeTAKServer (FTS) as a TCP
relay** is more reliable than multicast. Students connect ATAK → FTS via TCP,
Hydra sends CoT → FTS, FTS relays to all clients.

### Why FTS for courses

- Eliminates multicast headaches (AP isolation, WiFi sleep, IGMP)
- Students connect via TCP — works across subnets, VPNs, cellular
- FTS provides a web dashboard for the instructor to monitor
- Data persistence — mission playback after the exercise

### Quick FTS setup (on a laptop or RPi — not the Jetson)

```bash
# Option A: Docker (easiest)
docker run -d --name fts \
  -p 8087:8087 -p 8089:8089 -p 8443:8443 \
  freetakteam/freetakserver:2

# Option B: pip install
python3 -m venv /opt/fts-env
source /opt/fts-env/bin/activate
pip install FreeTAKServer FreeTAKServer-UI
python -m FreeTAKServer.controllers.services.FTS
```

FTS listens on:
- Port `8087` — unencrypted TCP (for testing)
- Port `8089` — TLS TCP (for production)
- Port `8443` — web admin UI

### Connect ATAK to FTS

On each student's ATAK:
1. Settings → Network Preferences → TAK Servers → Add
2. Address: `<FTS-IP>`, Port: `8087`, Protocol: TCP
3. No certificates needed for unencrypted mode

### Connect Hydra to FTS

**Current approach (multicast) still works** — FTS clients and multicast
clients coexist. But for FTS-only deployment, add FTS as a unicast target:

```ini
[tak]
enabled = true
unicast_targets = <FTS-IP>:8087
```

> **Note:** FTS expects TCP connections, not raw UDP. For direct FTS integration,
> a future Hydra update could add TCP CoT streaming. For now, run both multicast
> AND have FTS on the same network — FTS clients will still see multicast CoT
> if they're on the same L2 segment.

### Alternative: FTS + multicast bridge

Some FTS deployments run a multicast-to-TCP bridge. The FTS web UI can also
display CoT events received via its API. Check FreeTAKServer docs for the
REST API endpoint to POST CoT events directly.

---

## Testing Checklist

### Desk test (Phase 1)
- [ ] TAK enabled in config or via web UI toggle
- [ ] `tcpdump` shows CoT XML on port 6969
- [ ] Python listener receives and displays valid XML
- [ ] Self-SA events contain correct callsign and GPS
- [ ] Detection events contain correct label, confidence, track ID
- [ ] Events stop when TAK is toggled off

### Desktop client (Phase 2)
- [ ] WinTAK shows blue drone icon at correct GPS position
- [ ] Detection markers appear when objects are in frame
- [ ] Markers show correct type (person = yellow diamond, vehicle = yellow vehicle icon)
- [ ] Tapping a marker shows label, confidence, track ID in remarks
- [ ] Markers disappear after stale timeout
- [ ] RTSP video feed discoverable in WinTAK (if `advertise_host` configured)

### Mobile client (Phase 3-4)
- [ ] ATAK/iTAK receives CoT on same WiFi network
- [ ] Battery optimization disabled, screen kept on
- [ ] If multicast fails, unicast fallback works
- [ ] Multiple simultaneous detections show as separate markers

### Mesh (Phase 5)
- [ ] ATAK tablet connected via mesh WiFi AP gets `10.41.x.x` IP
- [ ] CoT packets traverse at least one mesh hop
- [ ] Detection latency acceptable (< 5 seconds from detection to marker)
- [ ] Mesh multicast flooding enabled if IGMP isn't working

### Course deployment (Phase 6)
- [ ] FreeTAKServer running and accepting connections
- [ ] All student ATAK devices connected to FTS
- [ ] Instructor can see all clients on FTS web dashboard
- [ ] Hydra detections visible on all student devices simultaneously
