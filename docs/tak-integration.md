# TAK Integration

Hydra sends and receives Cursor-on-Target (CoT) events over UDP multicast and unicast. ATAK devices see detection markers, vehicle position, and video feed links on the map. Operators can send lock/strike/unlock commands from ATAK GeoChat.

## TAK Output

Three types of CoT events are emitted:

### Detection Markers

Each detection with a GPS position emits a CoT marker on the TAK map. Markers include:

- Detection label and confidence
- Estimated GPS position (computed from vehicle heading and camera offset)
- Track ID for reference
- CoT type mapped from YOLO class to MIL-STD-2525 symbology
- Stale time configured by `stale_detection` (default 60 seconds)

Emission is throttled per-track to `emit_interval` seconds (default 2.0).

### Self-SA (Situational Awareness)

The vehicle's own position is broadcast at `sa_interval` (default 5 seconds). ATAK shows this as a friendly unit marker with the configured callsign.

### Video Feed Announcements

When RTSP is enabled and `advertise_host` is set, Hydra includes a video feed link in its SA events. ATAK devices can tap the marker to open the RTSP stream directly.

The URL format is `rtsp://<advertise_host>:<rtsp_port><rtsp_mount>`.

## TAK Input (Command Listener)

When `listen_commands = true`, Hydra listens for incoming CoT events and dispatches commands.

### GeoChat Commands

Send text messages via ATAK GeoChat in this format:

```
HYDRA LOCK 5
HYDRA STRIKE 5
HYDRA UNLOCK
```

The first word is the callsign prefix. The second is the command. The third (optional) is the track ID.

### Custom CoT Types

For programmatic integration, send CoT events with these types:

| CoT Type | Command | Track ID Location |
|----------|---------|-------------------|
| `a-x-hydra-l` | Lock | `detail/hydra/@trackId` or `detail/remarks` |
| `a-x-hydra-s` | Strike | `detail/hydra/@trackId` or `detail/remarks` |
| `a-x-hydra-u` | Unlock | N/A |

## Callsign Routing

Commands are routed to specific vehicles based on the callsign prefix in the command text.

### Routing Rules

| Command Prefix | Matches |
|---------------|---------|
| `HYDRA-2-USV` | Exact match only |
| `HYDRA` (bare) | Any callsign starting with HYDRA (backwards compatible) |
| `HYDRA-ALL` | All vehicles |
| `HYDRA-ALL-USV` | All vehicles with USV in their callsign |
| `HYDRA-2-ALL` | All vehicles with -2- in their callsign |

The routing uses segment matching. Non-ALL segments in the command prefix must appear as segments in the vehicle's callsign (split on `-`).

### Multi-Instance Identity

The callsign configured in `[tak] callsign` is used throughout the system:

- TAK CoT events (detection markers and self-SA)
- STATUSTEXT alerts to the GCS
- Application log file paths (multi-instance separation)
- Dashboard title bar
- Event timeline filenames

When running multiple Hydra instances on different Jetsons, use distinct callsigns: `HYDRA-1-USV`, `HYDRA-2-DRONE`, etc.

### Duplicate Callsign Detection

The TAK input listener monitors incoming SA events. If it detects another vehicle broadcasting the same callsign, it logs a warning. This catches configuration errors in multi-vehicle deployments.

## Security

### Callsign Allowlist

`allowed_callsigns` is a comma-separated list of callsigns permitted to send commands. This is fail-closed: if the list is empty, all TAK commands are disabled.

```ini
[tak]
allowed_callsigns = INSTRUCTOR-1, INSTRUCTOR-2
```

Only GeoChat messages or CoT events from senders whose callsign matches the allowlist are processed.

### HMAC-SHA256 Verification

For deployments where callsign spoofing is a concern, configure a shared secret:

```ini
[tak]
command_hmac_secret = my-shared-secret-key
```

Commands include an HMAC digest in the CoT event. The listener verifies the digest before processing. Commands without a valid HMAC are rejected.

### Unauthenticated Abort

The web API abort endpoint (`POST /api/abort`) is intentionally unauthenticated for instructor safety override. This is separate from the TAK command path, which respects the allowlist.

## ATAK Plugin

The `atak-plugin/` directory contains an ATAK plugin source for a radial menu target control interface. See the plugin directory for build instructions.

## Configuration

```ini
[tak]
enabled = true
callsign = HYDRA-1-USV
multicast_group = 239.2.3.1
multicast_port = 6969
unicast_targets = <TAK_TARGET_IP>:4242, <TAK_TARGET_IP>:4242
emit_interval = 2.0
sa_interval = 5.0
stale_detection = 60.0
stale_sa = 30.0
advertise_host = <JETSON_IP>
listen_commands = true
listen_port = 6969
allowed_callsigns = INSTRUCTOR-1
command_hmac_secret =
```

### Unicast Targets

By default, CoT events go to the multicast group. Add unicast targets for devices not on the multicast network (e.g., Tailscale peers):

```ini
unicast_targets = <TAK_TARGET_IP>:4242, <TAK_TARGET_IP>:4242
```

Manage targets at runtime via `GET /api/tak/targets`, `POST /api/tak/targets`, `DELETE /api/tak/targets`.

> [!TIP]
> ATAK-CIV on Android receives multicast CoT with zero configuration. Just connect to the same network and ATAK will show Hydra markers automatically.
