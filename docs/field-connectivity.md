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

**Hardware:** 4G USB modem (e.g., Huawei E3372), data SIM card.

**Pros:** Works anywhere with cell coverage, enables remote monitoring.
**Cons:** Latency (50-200ms), data costs, coverage dependent.

**Network:** Jetson gets a cellular IP. Tailscale provides a stable address
(e.g., 100.x.x.x). Dashboard at `http://<tailscale-ip>:8080`.

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
