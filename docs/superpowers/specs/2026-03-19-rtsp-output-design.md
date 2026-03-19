# RTSP Annotated Video Output

**Date:** 2026-03-19
**Status:** Approved
**Goal:** Publish annotated detection frames as an RTSP stream so Mission Planner
(or any RTSP client) can consume the Hydra video feed with bounding boxes and overlays.

## Requirements

- Stream the same annotated frame the web UI receives (bounding boxes, FPS, lock indicators)
- Match pipeline output resolution and framerate as-is (640x480, ~5-15 FPS) — no interpolation
- Config toggle in `config.ini`, **on by default**
- Runtime toggle via web UI and REST API (start/stop without restarting Hydra)
- Hardware H.264 encoding via NVENC on Jetson (`nvv4l2h264enc`)
- Software fallback (`x264enc tune=zerolatency`) for dev/testing off-Jetson
- Graceful degradation: if GStreamer/PyGObject not installed, log warning and disable (same pattern as MAVLink)
- Must not block the detection hot loop

## Dependencies

**Apt packages** (add to Dockerfile):
```
gstreamer1.0-rtsp gir1.2-gst-rtsp-server-1.0
gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
python3-gi gir1.2-gst-plugins-base-1.0 gir1.2-gstreamer-1.0
```

These are already available in the Jetson L4T base image but must be explicitly
installed in the Docker layer. No pip packages needed — PyGObject uses system `gi`.

**Dockerfile changes:**
- Add `RUN apt-get install -y` for the packages above
- Add `EXPOSE 8554`

**Systemd service (`scripts/hydra-detect.service`):**
- Add `-p 8554:8554` to the `docker run` command

## Architecture

```
Pipeline._run_loop
    │
    ├── stream_state.update_frame(annotated)   # existing — web MJPEG
    │
    └── rtsp_server.push_frame(annotated)      # new — copies frame into appsrc
            │
            └── GStreamer thread (async):
                appsrc → videoconvert → nvv4l2h264enc → rtph264pay → RTSP sink
```

Stream URL: `rtsp://<jetson-ip>:8554/hydra`

## New Module: `hydra_detect/rtsp_server.py`

Self-contained RTSP server module:

- `RTSPServer` class with `start()`, `stop()`, `push_frame(frame)` interface
- Creates `GstRtspServer.RTSPServer` on configurable port (default 8554)
- Uses a **shared media factory** (`factory.set_shared(True)`) so a single
  persistent GStreamer pipeline serves all connected clients. This means
  `push_frame()` always has an `appsrc` to write to, regardless of client count.
- `push_frame()` copies the BGR numpy frame into a GStreamer buffer and pushes
  to `appsrc`. Called from the pipeline thread — `appsrc.push-buffer` is
  thread-safe when called from outside the GLib main loop context (which it is).
  When RTSP is toggled off or stopped, `push_frame()` early-returns.
- GLib main loop runs in a daemon thread (same pattern as the web server)
- Client tracking via `client-connected` / `client-disconnected` signals on
  the server object, incrementing/decrementing an atomic counter for the
  `/api/rtsp/status` endpoint.
- `stop()` tears down the server, quits the GLib main loop, and joins the thread

**Appsrc caps:**
```
video/x-raw,format=BGR,width=640,height=480,framerate=0/1
```
`framerate=0/1` because frames arrive at variable rate from the detection loop.
`videoconvert` handles BGR→I420 conversion for the encoder.

**Encoder selection:**
1. Build pipeline string with `nvv4l2h264enc`
2. Attempt `Gst.parse_launch()` and transition to `PLAYING`
3. If state change returns `GST_STATE_CHANGE_FAILURE`, rebuild with
   `x264enc tune=zerolatency speed-preset=ultrafast`
4. Log which encoder was selected

**Graceful import failure:**
```python
try:
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstRtspServer', '1.0')
    from gi.repository import Gst, GstRtspServer, GLib
    _GST_AVAILABLE = True
except (ImportError, ValueError):
    _GST_AVAILABLE = False
```
If `_GST_AVAILABLE` is False, `start()` logs a warning and returns False.
Pipeline continues without RTSP (same pattern as MAVLink at pipeline.py:339-343).

## Pipeline Integration (`pipeline.py`)

1. **Init:** Read `[rtsp]` section — `enabled` (default true), `port` (default 8554),
   `mount` (default `/hydra`), `bitrate` (default 2000000)
2. **Start:** After web server launch, if RTSP enabled, instantiate and call
   `rtsp_server.start()`. If start returns False (GStreamer unavailable),
   log warning and set `self._rtsp = None`. Log the RTSP URL on success.
3. **Hot loop:** After `stream_state.update_frame(annotated)`, call
   `self._rtsp.push_frame(annotated)` if `self._rtsp is not None`.
   No extra copy beyond what GStreamer needs.
4. **Shutdown:** Call `rtsp_server.stop()` in `_shutdown()`
5. **Runtime toggle:** Register `on_rtsp_toggle` callback in `stream_state.set_callbacks()`
   alongside existing callbacks (lines 381-406). When toggled off: call `stop()`,
   set `self._rtsp = None`, `push_frame` calls become no-ops. When toggled on:
   re-instantiate and `start()`. Config change is in-memory only (not persisted
   to disk), consistent with other runtime toggles.

## Config (`config.ini`)

```ini
[rtsp]
enabled = true
port = 8554
mount = /hydra
bitrate = 2000000
```

## Web API

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/rtsp/status` | GET | No | Returns `{enabled, running, url, clients}` |
| `/api/rtsp/toggle` | POST | Yes | Body: `{enabled: bool}` — start/stop at runtime |

## Web UI

- Toggle switch in Operations panel system/settings area
- Show RTSP URL as copyable text when enabled
- Show connected client count

## Tests

- **`tests/test_rtsp_server.py`:**
  - Mock `gi` and `gi.repository` at module level (GStreamer not available in CI)
  - Verify `start()` returns False when `_GST_AVAILABLE` is False
  - Verify `push_frame()` with dummy numpy array when server is running
  - Verify `push_frame()` is a no-op after `stop()` (no crash)
  - Verify client counter increments/decrements on connect/disconnect signals
- **`tests/test_web_server.py`:**
  - `GET /api/rtsp/status` — response shape validation
  - `POST /api/rtsp/toggle` — auth required, toggle on/off

## Constraints

- `push_frame()` must not block — it writes to appsrc and returns
- H.264 encoding runs on GStreamer's thread via NVENC, not on the detection thread
- Bounded: shared factory with single appsrc, no per-client pipeline duplication
- Memory: single frame buffer in appsrc, no queue buildup
- If no clients connected, frames still push to appsrc but encoder output is
  discarded by the RTSP server (no network I/O)
