# Fleet Coordination via OpenMANET Mesh

**Date:** 2026-03-19
**Status:** Approved
**Scope:** UDP multicast fleet state sharing over OpenMANET mesh, with web UI
panel for shared awareness

## Problem

Hydra Detect currently operates as a single vehicle with no awareness of other
Hydra-equipped vehicles in the area. When 4-10 vehicles are deployed, operators
have no shared picture — each operator only sees their own vehicle's detections
and state. This leads to duplicate engagement and poor coordination.

## Solution

Add a fleet coordination layer that broadcasts each Jetson's state over the
OpenMANET mesh network via UDP multicast. Each Jetson listens for broadcasts
from others and maintains a shared fleet picture. A new Fleet Status panel in
the web UI shows all known fleet members.

This is shared awareness only (no automated deconfliction). The architecture
supports future C2 capabilities and TAK/CoT integration as independent
additions.

## Network Architecture

```
Jetson A ──ethernet──▶ RPi (mesh node) ◀──802.11ah──▶ RPi (mesh node) ◀──ethernet── Jetson B
              │                                              │
              └──── 10.41.0.0/16 flat IP subnet ─────────────┘
                    UDP multicast 239.41.0.1:41000
```

- OpenMANET provides a flat `10.41.0.0/16` IP subnet across all mesh nodes
- BATMAN-V handles Layer 2 mesh routing — transparent to applications
- Jetsons connect as clients via Ethernet (primary) or WiFi AP (fallback)
- Standard UDP multicast works across hops (BATMAN multicast optimizations)
- ~100 kbps throughput per node — fleet telemetry uses ~250 bytes/sec per node
- No broker, no discovery protocol, no single point of failure

## Design

### Broadcast Protocol

Each Jetson broadcasts a JSON status packet every 2 seconds to UDP multicast
group `239.41.0.1:41000`. Packet size is ~500 bytes.

**Packet format:**
```json
{
  "node_id": "hydra-01",
  "timestamp": 1710792000.0,
  "position": {
    "lat": 34.05,
    "lon": -118.25,
    "alt": 15.0,
    "heading": 270,
    "speed": 2.1
  },
  "vehicle": {
    "mode": "GUIDED",
    "armed": true,
    "battery_pct": 78
  },
  "detections": [
    {
      "track_id": 5,
      "label": "person",
      "confidence": 0.92,
      "lat": 34.051,
      "lon": -118.249
    }
  ],
  "target_lock": {
    "locked": true,
    "track_id": 5,
    "mode": "track"
  },
  "rf_hunt": {
    "state": "searching",
    "best_rssi": -72
  },
  "health": {
    "fps": 12.3,
    "gps_fix": 3
  }
}
```

**Node lifecycle:**
- **Active** — packet received within the last 10 seconds
- **Stale** — no packet for 10-30 seconds (network hiccup or node struggling)
- **Offline** — no packet for 30+ seconds (removed from active fleet dict)

### FleetManager (`hydra_detect/fleet/manager.py`)

```python
class FleetManager:
    def __init__(self, *, node_id, multicast_group, multicast_port,
                 broadcast_interval_sec, stale_timeout_sec,
                 offline_timeout_sec):
        ...

    def start(self) -> None:
        """Start broadcaster and listener daemon threads."""

    def stop(self) -> None:
        """Stop both threads."""

    def update_local(self, state: dict) -> None:
        """Update local state for next broadcast (called from pipeline each frame)."""

    def get_fleet(self) -> dict:
        """Return dict of all known fleet nodes (thread-safe)."""

    def get_node(self, node_id: str) -> dict | None:
        """Return a single node's state (thread-safe)."""

    @property
    def node_id(self) -> str: ...
```

**Threading model:**
- Broadcaster thread — daemon, sleeps `broadcast_interval_sec`, builds JSON
  from latest local state, sends to multicast group
- Listener thread — daemon, blocks on `recvfrom()`, parses JSON, updates fleet
  dict with timestamp
- Both protected by a single `threading.Lock` (consistent with Hydra's pattern)
- Ignores packets from own `node_id`
- A periodic cleanup pass (piggybacks on broadcast cycle) removes nodes that
  exceed `offline_timeout_sec`

**Multicast socket setup:**
- Broadcaster: `socket.socket(AF_INET, SOCK_DGRAM)`, `setsockopt(IPPROTO_IP,
  IP_MULTICAST_TTL, 4)` — TTL of 4 covers 3-4 mesh hops
- Listener: bind to `('', port)`, join multicast group via
  `setsockopt(IPPROTO_IP, IP_ADD_MEMBERSHIP, ...)`
- Both use `SO_REUSEADDR` for multiple instances on same host during testing

### Broadcast Module (`hydra_detect/fleet/broadcast.py`)

Low-level UDP multicast send/receive functions used by FleetManager:

- `create_sender(group, port, ttl)` → configured send socket
- `create_listener(group, port)` → configured receive socket with membership
- `send_packet(sock, group, port, data: dict)` → JSON encode + sendto
- `recv_packet(sock, timeout)` → recvfrom + JSON decode, returns (data, addr)

Separated from FleetManager so the socket setup can be tested and reused
(e.g., by a future TAK/CoT broadcaster).

### Pipeline Integration

In `Pipeline.__init__`:
```python
self._fleet_manager: FleetManager | None = None
if self._cfg.getboolean("fleet", "enabled", fallback=False):
    self._fleet_manager = FleetManager(
        node_id=self._cfg.get("fleet", "node_id", fallback="hydra-01"),
        multicast_group=self._cfg.get("fleet", "multicast_group", fallback="239.41.0.1"),
        multicast_port=self._cfg.getint("fleet", "multicast_port", fallback=41000),
        broadcast_interval_sec=self._cfg.getfloat("fleet", "broadcast_interval_sec", fallback=2.0),
        stale_timeout_sec=self._cfg.getfloat("fleet", "stale_timeout_sec", fallback=10.0),
        offline_timeout_sec=self._cfg.getfloat("fleet", "offline_timeout_sec", fallback=30.0),
    )
    self._fleet_manager.start()
```

In `_run_loop()` — every frame, call `fleet_manager.update_local(state)` with
current vehicle/detection/lock state. This is non-blocking (just updates a
dict).

In `_shutdown()` — call `fleet_manager.stop()`.

Web callbacks: register `get_fleet_status=self._fleet_manager.get_fleet`.

### Config (`config.ini`)

New section:
```ini
[fleet]
enabled = false
node_id = hydra-01
multicast_group = 239.41.0.1
multicast_port = 41000
broadcast_interval_sec = 2.0
stale_timeout_sec = 10.0
offline_timeout_sec = 30.0
```

### Web API

New endpoint:
- `GET /api/fleet/status` — returns the fleet dict. No auth required (read-only).

Response format:
```json
{
  "self": "hydra-01",
  "nodes": {
    "hydra-02": {
      "status": "active",
      "last_seen": 1710792000.0,
      "position": {...},
      "vehicle": {...},
      "detections": [...],
      "target_lock": {...},
      "rf_hunt": {...},
      "health": {...}
    }
  }
}
```

### Fleet Status Panel (Operations View)

New panel added to `operations.html`. Positioned as Tier 2 (expanded by
default), after RF Hunt and before Detection Config.

**Panel content:**
- One card per fleet node showing:
  - Node ID as header (e.g., "HYDRA-02")
  - Connection indicator (green dot = active, yellow = stale)
  - Distance from this vehicle (in meters, computed from GPS positions)
  - Vehicle state: mode badge, armed badge, battery %
  - Detection summary: count + top label (e.g., "3 tracks — person x2")
  - Target lock state: "TRACKING #5 person" or "No lock"
- Empty state: "No fleet members detected"
- Panel visibility toggle entry in the dropdown menu

**Polling:** New `fleet` poller at 2000ms, active in Operations view.

## Files to Create

| File | Purpose |
|------|---------|
| `hydra_detect/fleet/__init__.py` | Package init |
| `hydra_detect/fleet/broadcast.py` | UDP multicast send/receive functions |
| `hydra_detect/fleet/manager.py` | FleetManager class |
| `tests/test_fleet_broadcast.py` | Broadcast unit tests |
| `tests/test_fleet_manager.py` | FleetManager unit tests |

## Files to Modify

| File | Change |
|------|--------|
| `hydra_detect/pipeline.py` | Create FleetManager, call update_local each frame, stop on shutdown, register web callback |
| `hydra_detect/web/server.py` | Add GET /api/fleet/status endpoint |
| `hydra_detect/web/templates/operations.html` | Add Fleet Status panel |
| `hydra_detect/web/static/js/operations.js` | Add fleet panel update logic |
| `hydra_detect/web/static/js/app.js` | Add fleet poller |
| `hydra_detect/web/static/css/operations.css` | Fleet panel styles |
| `config.ini` | Add [fleet] section |

## Testing

### Unit Tests

**`tests/test_fleet_broadcast.py`:**
- Send and receive a packet on localhost multicast
- Handle malformed JSON gracefully
- Handle socket timeout
- Sender TTL is configured correctly

**`tests/test_fleet_manager.py`:**
- Fleet dict starts empty
- update_local stores state for broadcast
- Received packet adds node to fleet
- Own node_id is ignored
- Stale timeout marks node as stale
- Offline timeout removes node
- get_fleet returns thread-safe snapshot
- get_node returns None for unknown node

### Integration Testing

- Start two FleetManagers on localhost with different node_ids
- Verify each sees the other within 3 seconds
- Stop one, verify the other marks it stale then offline

### Manual Testing

- [ ] Enable fleet on two Jetsons connected to OpenMANET mesh
- [ ] Both appear in each other's Fleet Status panel within 5 seconds
- [ ] Detections on one vehicle appear in the other's fleet panel
- [ ] Disconnect one from mesh — other shows stale then removes it
- [ ] Fleet panel shows distance between vehicles

## Future Extensions (out of scope)

- TAK/CoT integration (separate sub-project, uses broadcast.py)
- Automated target deconfliction
- Commander node / C2 commands
- Fleet map overlay on video feed
- Shared geofence enforcement
