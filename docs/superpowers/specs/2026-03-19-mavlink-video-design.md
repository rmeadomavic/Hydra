# MAVLink Video — Thumbnail Stream Over Telemetry Radio

**Date:** 2026-03-19
**Status:** Approved
**Goal:** Stream low-resolution annotated detection thumbnails over MAVLink
(RFD900x / serial / UDP) so Mission Planner displays live detection imagery
without requiring an IP video link.

## Requirements

- Stream the same annotated frame (bounding boxes, overlays) downscaled to thumbnail
- Use standard MAVLink `DATA_TRANSMISSION_HANDSHAKE` + `ENCAPSULATED_DATA` protocol
- Mission Planner receives and displays frames with no custom plugins
- Adaptive frame rate — back off when link is saturated, speed up when clear
- Configurable resolution, JPEG quality, and rate limits in `config.ini`
- Live tuning of resolution, quality, and rate from web UI
- Config toggle, **on by default**
- Runtime toggle via web UI and REST API (runtime-only, not persisted to config)
- Must not block the detection hot loop
- Works over any MAVLink transport: RFD900 serial, UDP, OpenMANET mesh

## Bandwidth Budget

RFD900x: ~250 kbps theoretical, ~80 kbps usable after MAVLink telemetry.

| Resolution | JPEG Q | Frame size | Max FPS | Bandwidth |
|-----------|--------|-----------|---------|-----------|
| 160x120 | 20 | ~3-5 KB | 1-2 | ~5-10 KB/s |
| 120x90 | 20 | ~2-3 KB | 2-3 | ~4-6 KB/s |
| 80x60 | 25 | ~1-2 KB | 3-4 | ~4-6 KB/s |

## MAVLink Protocol

Each frame transfer:

1. **`DATA_TRANSMISSION_HANDSHAKE`** (msg id 130):
   - `type`: 0 (JPEG)
   - `size`: total JPEG byte count
   - `width`, `height`: thumbnail dimensions
   - `packets`: ceil(size / 253)
   - `payload`: 253 (max bytes per chunk)
   - `jpg_quality`: current quality setting

2. **`ENCAPSULATED_DATA`** (msg id 131) x N packets:
   - `seqnr`: 0..packets-1
   - `data`: list of 253 ints (bytes as ints; last packet zero-padded to 253)

Mission Planner reassembles the JPEG from the sequence and displays it in
the Video pane when receiving these messages.

## Thread Safety: MAVLink Send Lock

pymavlink's `send()` is **NOT thread-safe** — it has no internal lock.
The existing codebase avoids contention because all sends happen from the
pipeline thread or web request threads (which are fast, infrequent calls).

A video sender thread sending 20 packets per frame would interleave with
pipeline sends (alerts, yaw, heartbeats) and corrupt packets.

**Solution:** Add a `threading.Lock` to `MAVLinkIO` as `self._send_lock`.
All existing send methods (`alert_detection`, `adjust_yaw`, `command_long`,
`send_statustext`, etc.) acquire this lock. The `MAVLinkVideoSender`
receives a reference to this lock and holds it for the entire frame
transmission (handshake + all chunks). This serializes all sends.

The sender thread releases the lock between frames (not between packets)
so telemetry sends can interleave between frames, not mid-frame.

## Inter-Packet Pacing

On serial links, sending 20 packets back-to-back (~7 KB) monopolizes the
port. The sender thread inserts a small sleep (2ms) between each
`ENCAPSULATED_DATA` packet to allow the serial TX buffer to drain and
prevent reader-thread message loss.

The send lock is held for the entire frame (handshake + all paced packets)
to prevent other sends from splitting the image sequence. At 2ms x 20
packets = 40ms lock hold per frame, this is acceptable — pipeline sends
(STATUSTEXT, yaw) are infrequent and can tolerate 40ms delay.

## Adaptive Rate

**Limitation:** On serial links, pyserial's `write()` buffers data and
returns immediately — wall-clock send time does not reflect actual link
saturation. True link-level feedback is not available from pymavlink.

**Pragmatic approach:** Use frame-size-based rate adaptation instead of
timing-based:

1. After encoding, measure JPEG size in bytes
2. Estimate transmit time: `jpeg_size / link_budget_bytes_per_sec`
   where `link_budget_bytes_per_sec` is a config value (default 8000,
   representing ~80 kbps usable)
3. Set next interval: `estimated_tx_time * 2` (50% duty cycle — leave
   half the bandwidth for telemetry)
4. Clamp: `1/max_fps <= interval <= 1/min_fps`
5. Expose actual achieved FPS and bytes_per_sec in status

Operator can fine-tune via web UI sliders if the default budget is wrong.

## Architecture

```
Pipeline._run_loop
    │
    ├── rtsp_server.push_frame(annotated)       # existing
    │
    └── mavlink_video.push_frame(annotated)     # new — swaps frame reference
            │
            └── Sender thread (own schedule):
                1. Read latest frame ref (skip if stale/None)
                2. cv2.resize → cv2.imencode (JPEG)
                3. Acquire send_lock
                4. DATA_TRANSMISSION_HANDSHAKE
                5. ENCAPSULATED_DATA x N (2ms pacing)
                6. Release send_lock
                7. Compute next interval from JPEG size
```

## New Module: `hydra_detect/mavlink_video.py`

`MAVLinkVideoSender` class:

- **Constructor:** Takes `mavlink_io: MAVLinkIO` instance (not raw `_mav`),
  resolution, quality, rate limits, link_budget_bps
- **`start()`:** Launches sender daemon thread. Returns False if MAVLink
  not connected.
- **`stop()`:** Signals thread to stop, joins.
- **`push_frame(frame)`:** Swaps a `threading.Lock`-protected frame
  reference and increments a generation counter. Zero-cost in hot loop.
- **Sender thread loop:**
  1. Sleep for current interval
  2. Grab latest frame + generation under lock. If generation unchanged
     since last send (stale / pipeline paused), skip.
  3. `cv2.resize` to target resolution
  4. `cv2.imencode('.jpg', [cv2.IMWRITE_JPEG_QUALITY, quality])` → bytes
  5. Convert to `list[int]` for pymavlink
  6. Acquire `send_lock` from MAVLinkIO
  7. Send `DATA_TRANSMISSION_HANDSHAKE`
  8. Chunk into 253-int lists, send `ENCAPSULATED_DATA` with 2ms pacing
  9. Release `send_lock`
  10. Compute next interval from JPEG size and link budget
- **`get_status()`:** Returns dict with enabled, running, width, height,
  quality, current_fps, bytes_per_sec
- **`set_params(width, height, quality, max_fps)`:** Live tuning from web
  UI. Thread-safe via lock. Validated: width 40-320, height 30-240,
  quality 5-50, max_fps 0.1-5.0.

## Pipeline Integration (`pipeline.py`)

1. **Init:** Read `[mavlink_video]` config section
2. **Start:** After RTSP start, if enabled AND MAVLink connected,
   instantiate `MAVLinkVideoSender(self._mavlink, ...)` and start.
   Pass the MAVLinkIO instance — it exposes the send lock.
3. **Hot loop:** After RTSP push_frame, call
   `self._mavlink_video.push_frame(annotated)` if not None
4. **Shutdown:** Call `stop()` in `_shutdown()`
5. **Callbacks:** Wire `on_mavlink_video_toggle`, `on_mavlink_video_tune`,
   `get_mavlink_video_status` through `stream_state`

## MAVLinkIO Changes

Add to `MAVLinkIO.__init__`:
```python
self._send_lock = threading.Lock()
```

Add public accessor:
```python
@property
def send_lock(self) -> threading.Lock:
    return self._send_lock
```

Wrap existing send calls (`send_statustext`, `alert_detection`,
`adjust_yaw`, `command_long_send`, `flash_servo`, etc.) with
`with self._send_lock:` to prevent interleaving with video packets.

## Config (`config.ini`)

```ini
[mavlink_video]
enabled = true
width = 160
height = 120
jpeg_quality = 20
max_fps = 2.0
min_fps = 0.2
link_budget_bps = 8000
```

## Web API

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/mavlink-video/status` | GET | No | `{enabled, running, width, height, quality, current_fps, bytes_per_sec}` |
| `/api/mavlink-video/toggle` | POST | Yes | `{enabled: bool}` — start/stop (runtime-only) |
| `/api/mavlink-video/tune` | POST | Yes | `{width, height, quality, max_fps}` — all optional |

**Tune validation:** width 40-320, height 30-240, quality 5-50,
max_fps 0.1-5.0. Reject out-of-range values with 400.

## Web UI

- Toggle switch in Operations panel (next to RTSP toggle)
- Show stats: current FPS, resolution, KB/s
- Sliders for resolution (80-320) and quality (5-50) for live tuning

## Tests

- **`tests/test_mavlink_video.py`:**
  - Mock pymavlink mav object and send_lock
  - Verify chunking: 5000 byte JPEG → ceil(5000/253) = 20 packets
  - Verify last chunk is zero-padded to 253 ints
  - Verify DATA_TRANSMISSION_HANDSHAKE fields match frame
  - Verify push_frame is non-blocking (just swaps reference)
  - Verify stale frame detection (same generation → skip)
  - Verify adaptive rate: large JPEG → longer interval
  - Verify adaptive rate: small JPEG → shorter interval
  - Verify stop() joins thread cleanly
  - Verify start() returns False when MAVLink not connected
  - Verify send_lock is acquired during frame send
  - Verify set_params rejects out-of-range values
- **`tests/test_web_api.py`:** toggle, tune (with validation), status
- **`tests/test_pipeline_callbacks.py`:** status/toggle callback tests

## Constraints

- `push_frame()` must not block — reference swap + generation increment only
- All encoding and sending happens on the sender thread
- Send lock serializes all MAVLink sends (video + telemetry)
- Lock hold time per frame: ~40ms at 20 packets x 2ms pacing
- Inter-packet pacing: 2ms sleep between ENCAPSULATED_DATA sends
- Stale frame detection: sender skips if pipeline is paused
- If MAVLink disconnects, sender catches exceptions and retries next interval
- Memory: one frame reference + one JPEG buffer on sender thread
