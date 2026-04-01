# Field Connectivity Guide

Hydra needs an IP link between the operator's device and the Jetson for the
web dashboard, MJPEG stream, and REST API. In the field (no internet), three
connectivity tiers are available.

## Tier 1: WiFi AP (Close Range, ~50m)

The Jetson runs a WiFi hotspot. Operator connects phone or laptop directly.

**When to use:** Boat on shore, benchtop testing, vehicle in parking lot.

**Setup options:**
- USB WiFi adapter in AP mode via `hostapd`
- Jetson's built-in WiFi (if available) in AP mode

**Pros:** Simple, no extra hardware, zero config on client side.
**Cons:** Limited range (~50m), single point of failure.

**Network:** Jetson at 192.168.4.1, DHCP for clients. Dashboard at
`http://192.168.4.1:8080`.

## Tier 2: OpenMANET Mesh (Medium Range, ~1km+)

RPi + WiFi HaLow mesh radios provide a flat IP network that carries both
MAVLink-over-IP and the web dashboard.

**When to use:** Field exercises, extended range ops, multi-vehicle scenarios.

**Hardware:** OpenMANET nodes (RPi + WiFi HaLow), one per vehicle + one at GCS.

**Pros:** Longer range, self-healing mesh, supports multiple vehicles.
**Cons:** Extra hardware, needs power at each node.

**Network:** Flat IP mesh. Jetson gets a mesh IP (e.g., 10.0.0.x). Dashboard
accessible from any node on the mesh.

## Tier 3: LTE Modem (Anywhere with Cell Coverage)

4G USB modem + SIM card on the Jetson, with Tailscale overlay for secure
remote access.

**When to use:** Remote ops, multi-site exercises, instructor monitoring from
a different location, demo from HQ.

**Hardware:** SUNGOOYUE 4G LTE USB dongle (Qualcomm chipset, USB ID
`05c6:9024`, UFI-JZ firmware). Mint Mobile 5 GB prepaid SIM.

**Pros:** Works anywhere with cell coverage, enables remote monitoring,
cheap hardware (~$15 dongle + $15/mo SIM).
**Cons:** Latency (50-200ms), data costs, coverage dependent. Cheap dongles
may lack US rural bands (see [Band Compatibility](#band-compatibility) below).

**Network:** Jetson gets a cellular IP on 192.168.42.0/24 (modem gateway
at 192.168.42.129). Tailscale provides a stable address (e.g., 100.x.x.x).
Dashboard at `http://<tailscale-ip>:8080`.

### LTE Setup

#### 1. Activate SIM

Go to [mintmobile.com/activate](https://mintmobile.com/activate) and follow
the steps. You need the SIM card number and IMEI from the modem. The modem
IMEI is printed on the label or available via the admin UI.

#### 2. Insert SIM and Plug In

Insert the Mint Mobile SIM into the modem. Plug the modem into any Jetson
USB port. The modem initially appears as USB ID `05c6:9091` (storage mode),
then mode-switches to `05c6:9024` (modem mode) within a few seconds.

Verify with:
```bash
lsusb | grep 05c6
# Expected: 05c6:9024 Qualcomm, Inc. ...
```

#### 3. Verify Network Interface

The kernel loads the `rndis_host` driver and creates a `usb2` network
interface. DHCP assigns an IP on the 192.168.42.0/24 subnet.

```bash
ip addr show usb2
# Look for: inet 192.168.42.xxx/24
```

If the interface does not appear:
```bash
sudo modprobe option
echo "05c6 9024" | sudo tee /sys/bus/usb-serial/drivers/option1/new_id
```

Then unplug and replug the modem.

#### 4. Configure APN

Open the modem admin panel at `http://192.168.42.129`. Set:
- **APN:** `wholesale`
- **MCC:** 310
- **MNC:** 240

Save and reboot the modem. It should connect to T-Mobile within 30 seconds.

**Note:** The admin UI ships in Indonesian/Chinese. Look for a "bahasa"
or language toggle near the top to switch to English.

#### 5. Accessing the Admin UI from a Laptop

The modem admin panel is only reachable from the Jetson (it's on the USB
network). To access it from your laptop, run a port forward on the Jetson:

```bash
socat TCP-LISTEN:8081,fork,reuseaddr,bind=0.0.0.0 TCP:192.168.42.129:80
```

Then open `http://<jetson-ip>:8081` from your laptop browser.

#### 6. Verify Connectivity

```bash
# Check that the modem has a connection
ping -c 3 8.8.8.8

# Confirm Tailscale can reach the network
tailscale status
```

Once LTE is up and Tailscale is connected, the dashboard is available at
`http://<tailscale-ip>:8080` from anywhere.

### Band Compatibility

The SUNGOOYUE dongle supports LTE bands B1/B3/B5/B7/B8/B20 — primarily
Asian and European frequencies. T-Mobile US coverage with these bands is
limited, especially in rural areas. Signal must be above -110 dBm to
register on the network.

**For reliable US coverage,** you need B12 (700 MHz) and B71 (600 MHz).
Recommended alternatives:

| Modem | Bands | Notes |
|-------|-------|-------|
| Quectel RM520N-GL (M.2) | All US LTE + 5G | Best coverage, needs carrier board |
| GL.iNet Mudi V2 (GL-E750V2) | All US LTE | Portable router, battery-powered, USB-C |

The Mint Mobile SIM works in any unlocked modem — just set APN to `wholesale`.

## Bandwidth Requirements

| Feature | Bandwidth | Notes |
|---------|-----------|-------|
| Web dashboard (no video) | ~10 KB/s | Stats polling, controls |
| MJPEG stream (640x480) | ~500 KB/s - 2 MB/s | Quality dependent |
| RTSP stream (H.264) | ~200-500 KB/s | More efficient than MJPEG |
| MAVLink telemetry | ~5 KB/s | Heartbeat + GPS + alerts |
| TAK CoT | ~1 KB/s | Detection markers |

**Tier 1 (WiFi):** All features work at full quality.
**Tier 2 (Mesh):** All features work; may need to reduce MJPEG quality.
**Tier 3 (LTE):** Prefer RTSP over MJPEG. Dashboard controls work fine.
Video may need lower resolution depending on signal quality.

## Pre-Mission Checklist

- [ ] Verify connectivity tier before launch
- [ ] Confirm dashboard is accessible from operator device
- [ ] Test MJPEG/RTSP stream at expected quality
- [ ] Verify MAVLink telemetry is flowing (check `/api/stats`)
- [ ] If using LTE: confirm Tailscale is connected (`tailscale status`)
- [ ] If using mesh: confirm all nodes are visible
