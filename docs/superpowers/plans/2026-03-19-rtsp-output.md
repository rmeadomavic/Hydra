# RTSP Annotated Video Output — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish annotated detection frames as an RTSP H.264 stream consumable by Mission Planner or any RTSP client.

**Architecture:** A new `rtsp_server.py` module wraps GStreamer's RTSP server library. The pipeline pushes annotated frames into an `appsrc`; GStreamer encodes via NVENC (hardware) or x264 (fallback) on its own thread. Config toggle on by default, runtime toggle via web UI.

**Tech Stack:** GStreamer (GstRtspServer, PyGObject/gi), nvv4l2h264enc (Jetson HW encoder), x264enc (software fallback)

**Spec:** `docs/superpowers/specs/2026-03-19-rtsp-output-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `hydra_detect/rtsp_server.py` | RTSP server module — start/stop/push_frame |
| Create | `tests/test_rtsp_server.py` | Unit tests for RTSP server (mocked GStreamer) |
| Modify | `hydra_detect/pipeline.py` | Wire RTSP into init, hot loop, shutdown, callbacks |
| Modify | `hydra_detect/web/server.py` | Add `/api/rtsp/status` and `/api/rtsp/toggle` endpoints |
| Modify | `hydra_detect/web/templates/operations.html` | RTSP toggle + URL display in System panel |
| Modify | `hydra_detect/web/static/js/operations.js` | Wire RTSP toggle to API |
| Modify | `tests/test_web_api.py` | RTSP endpoint tests |
| Modify | `config.ini` | Add `[rtsp]` section |
| Modify | `Dockerfile` | Add GStreamer apt packages, EXPOSE 8554 |
| Modify | `scripts/hydra-detect.service` | Add `-p 8554:8554` port mapping |

---

## Task 1: RTSP Server Module — Core Class

**Files:**
- Create: `hydra_detect/rtsp_server.py`
- Create: `tests/test_rtsp_server.py`

- [ ] **Step 1: Write failing tests for RTSPServer lifecycle**

Create `tests/test_rtsp_server.py`:

```python
"""Unit tests for RTSP server — GStreamer is mocked for CI."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Mock gi before importing rtsp_server (GStreamer not available in CI)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_gi(monkeypatch):
    """Provide a fake gi module so rtsp_server can be imported."""
    mock_gi = MagicMock()
    mock_gi.require_version = MagicMock()

    mock_gst = MagicMock()
    mock_gst.init.return_value = None
    mock_gst.Buffer.new_wrapped.return_value = MagicMock()

    mock_rtsp = MagicMock()
    mock_server = MagicMock()
    mock_factory = MagicMock()
    mock_rtsp.RTSPServer.return_value = mock_server
    mock_rtsp.RTSPMediaFactory.return_value = mock_factory

    mock_glib = MagicMock()
    mock_loop = MagicMock()
    mock_glib.MainLoop.return_value = mock_loop

    mock_gi.repository.Gst = mock_gst
    mock_gi.repository.GstRtspServer = mock_rtsp
    mock_gi.repository.GLib = mock_glib

    monkeypatch.setitem(sys.modules, 'gi', mock_gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', mock_gi.repository)

    # Force reimport with mocks in place
    mod_name = 'hydra_detect.rtsp_server'
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    yield {
        'gi': mock_gi,
        'Gst': mock_gst,
        'GstRtspServer': mock_rtsp,
        'GLib': mock_glib,
        'server': mock_server,
        'factory': mock_factory,
        'loop': mock_loop,
    }

    if mod_name in sys.modules:
        del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRTSPServerLifecycle:
    def test_start_creates_server_on_port(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        result = srv.start()
        assert result is True
        assert srv.running is True
        _mock_gi['server'].set_service.assert_called_once_with("8554")

    def test_stop_quits_mainloop(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        srv.stop()
        assert srv.running is False
        _mock_gi['loop'].quit.assert_called_once()

    def test_push_frame_when_running(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Should not raise
        srv.push_frame(frame)

    def test_push_frame_noop_when_stopped(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Should not raise even when not started
        srv.push_frame(frame)

    def test_client_count_starts_at_zero(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        assert srv.client_count == 0

    def test_url_property(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        assert srv.url == "rtsp://0.0.0.0:8554/hydra"


class TestRTSPGracefulDegradation:
    def test_start_returns_false_when_gst_unavailable(self, _mock_gi):
        """If _GST_AVAILABLE is False, start() should return False."""
        from hydra_detect.rtsp_server import RTSPServer
        import hydra_detect.rtsp_server as mod
        original = mod._GST_AVAILABLE
        mod._GST_AVAILABLE = False
        try:
            srv = RTSPServer(port=8554, mount="/hydra")
            assert srv.start() is False
            assert srv.running is False
        finally:
            mod._GST_AVAILABLE = original


class TestRTSPClientTracking:
    def test_client_connected_increments_count(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        mock_client = MagicMock()
        srv._on_client_connected(None, mock_client)
        assert srv.client_count == 1
        mock_client.connect.assert_called_once_with("closed", srv._on_client_closed)

    def test_client_closed_decrements_count(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        mock_client = MagicMock()
        srv._on_client_connected(None, mock_client)
        srv._on_client_closed(mock_client)
        assert srv.client_count == 0

    def test_client_count_never_negative(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv._on_client_closed(MagicMock())
        assert srv.client_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rtsp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hydra_detect.rtsp_server'`

- [ ] **Step 3: Implement RTSPServer class**

Create `hydra_detect/rtsp_server.py`:

```python
"""GStreamer RTSP server — publishes annotated detection frames as H.264 stream."""

from __future__ import annotations

import logging
import threading

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Graceful degradation: GStreamer may not be installed (dev machines, CI).
_GST_AVAILABLE = False
try:
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstRtspServer', '1.0')
    from gi.repository import GLib, Gst, GstRtspServer
    Gst.init(None)
    _GST_AVAILABLE = True
except (ImportError, ValueError):
    logger.info("GStreamer not available — RTSP output disabled.")


class RTSPServer:
    """Publish annotated frames as an RTSP H.264 stream.

    Usage:
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()          # launches GLib main loop in daemon thread
        srv.push_frame(bgr)  # call from detection loop (non-blocking)
        srv.stop()           # tears down server
    """

    def __init__(
        self,
        port: int = 8554,
        mount: str = "/hydra",
        bitrate: int = 2_000_000,
        width: int = 640,
        height: int = 480,
    ):
        self._port = port
        self._mount = mount if mount.startswith("/") else f"/{mount}"
        self._bitrate = bitrate
        self._width = width
        self._height = height

        self._server = None
        self._loop = None
        self._thread: threading.Thread | None = None
        self._appsrc = None
        self._running = False
        self._client_count = 0
        self._client_lock = threading.Lock()

    # -- Public interface ---------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def client_count(self) -> int:
        with self._client_lock:
            return self._client_count

    @property
    def url(self) -> str:
        return f"rtsp://0.0.0.0:{self._port}{self._mount}"

    def start(self) -> bool:
        """Start the RTSP server. Returns False if GStreamer is unavailable."""
        if not _GST_AVAILABLE:
            logger.warning("GStreamer not available — cannot start RTSP server.")
            return False

        if self._running:
            return True

        try:
            self._server = GstRtspServer.RTSPServer()
            self._server.set_service(str(self._port))

            factory = GstRtspServer.RTSPMediaFactory()
            factory.set_launch(self._build_pipeline_string())
            factory.set_shared(True)
            factory.connect("media-configure", self._on_media_configure)

            mounts = self._server.get_mount_points()
            mounts.add_factory(self._mount, factory)

            self._server.connect("client-connected", self._on_client_connected)

            self._server.attach(None)

            self._loop = GLib.MainLoop()
            self._thread = threading.Thread(
                target=self._loop.run, daemon=True, name="hydra-rtsp",
            )
            self._thread.start()
            self._running = True
            logger.info("RTSP server started: %s", self.url)
            return True

        except Exception as exc:
            logger.error("Failed to start RTSP server: %s", exc)
            self._running = False
            return False

    def stop(self) -> None:
        """Shut down the RTSP server."""
        if not self._running:
            return
        self._running = False
        self._appsrc = None
        if self._loop is not None:
            self._loop.quit()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._server = None
        logger.info("RTSP server stopped.")

    def push_frame(self, frame: np.ndarray) -> None:
        """Push a BGR frame to connected RTSP clients. Non-blocking."""
        if not self._running or self._appsrc is None:
            return

        try:
            data = frame.tobytes()
            buf = Gst.Buffer.new_wrapped(data)
            self._appsrc.emit("push-buffer", buf)
        except Exception as exc:
            # Rate-limit: only log once per 100 failures to avoid log spam
            if not hasattr(self, '_push_err_count'):
                self._push_err_count = 0
            self._push_err_count += 1
            if self._push_err_count == 1 or self._push_err_count % 100 == 0:
                logger.warning("RTSP push_frame error (#%d): %s", self._push_err_count, exc)

    # -- Internal -----------------------------------------------------------

    def _build_pipeline_string(self) -> str:
        """Build GStreamer launch string, trying HW encoder first."""
        caps = (
            f"video/x-raw,format=BGR,width={self._width},"
            f"height={self._height},framerate=0/1"
        )
        # Try Jetson HW encoder first
        hw_enc = (
            f"( appsrc name=source is-live=true format=time caps={caps} ! "
            f"videoconvert ! video/x-raw,format=I420 ! "
            f"nvv4l2h264enc bitrate={self._bitrate} ! "
            f"h264parse ! rtph264pay name=pay0 pt=96 )"
        )
        if self._check_encoder("nvv4l2h264enc"):
            logger.info("RTSP using hardware encoder: nvv4l2h264enc")
            return hw_enc

        # Software fallback
        logger.info("RTSP using software encoder: x264enc")
        sw_enc = (
            f"( appsrc name=source is-live=true format=time caps={caps} ! "
            f"videoconvert ! video/x-raw,format=I420 ! "
            f"x264enc tune=zerolatency speed-preset=ultrafast "
            f"bitrate={self._bitrate // 1000} ! "
            f"h264parse ! rtph264pay name=pay0 pt=96 )"
        )
        return sw_enc

    @staticmethod
    def _check_encoder(name: str) -> bool:
        """Check if a GStreamer encoder element is available."""
        if not _GST_AVAILABLE:
            return False
        try:
            factory = Gst.ElementFactory.find(name)
            return factory is not None
        except Exception:
            return False

    def _on_media_configure(self, factory, media) -> None:
        """Called when a client connects and media pipeline is created."""
        element = media.get_element()
        self._appsrc = element.get_child_by_name("source")

    def _on_client_connected(self, server, client) -> None:
        """Track connected RTSP clients."""
        with self._client_lock:
            self._client_count += 1
        logger.info("RTSP client connected (total: %d)", self.client_count)
        client.connect("closed", self._on_client_closed)

    def _on_client_closed(self, client) -> None:
        with self._client_lock:
            self._client_count = max(0, self._client_count - 1)
        logger.info("RTSP client disconnected (total: %d)", self.client_count)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rtsp_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/rtsp_server.py tests/test_rtsp_server.py
git commit -m "feat: add RTSP server module with GStreamer backend"
```

---

## Task 2: Pipeline Integration

**Files:**
- Modify: `hydra_detect/pipeline.py` (import, init, start, hot loop, shutdown, toggle callback)
- Modify: `config.ini` (add `[rtsp]` section)

- [ ] **Step 1: Add `[rtsp]` section to config.ini**

Append to `config.ini` after the `[logging]` section:

```ini
[rtsp]
enabled = true
port = 8554
mount = /hydra
bitrate = 2000000
```

- [ ] **Step 2: Add RTSP import and init to Pipeline.__init__**

In `pipeline.py`, add import at top (after existing imports around line 23):

```python
from .rtsp_server import RTSPServer
```

In `Pipeline.__init__`, after the web UI config block (after line 296 `self._web_port`), add:

```python
        # RTSP output
        self._rtsp: RTSPServer | None = None
        self._rtsp_enabled = self._cfg.getboolean("rtsp", "enabled", fallback=True)
        self._rtsp_port = self._cfg.getint("rtsp", "port", fallback=8554)
        self._rtsp_mount = self._cfg.get("rtsp", "mount", fallback="/hydra")
        self._rtsp_bitrate = self._cfg.getint("rtsp", "bitrate", fallback=2_000_000)
```

- [ ] **Step 3: Add RTSP start to Pipeline.start()**

In `Pipeline.start()`, after `run_server()` call (after line 412), add:

```python
        # Start RTSP output
        if self._rtsp_enabled:
            self._rtsp = RTSPServer(
                port=self._rtsp_port,
                mount=self._rtsp_mount,
                bitrate=self._rtsp_bitrate,
                width=self._cfg.getint("camera", "width", fallback=640),
                height=self._cfg.getint("camera", "height", fallback=480),
            )
            if not self._rtsp.start():
                logger.warning("RTSP server failed to start — continuing without.")
                self._rtsp = None
```

- [ ] **Step 4: Add push_frame to hot loop**

In `_run_loop()`, BEFORE the `if self._web_enabled:` block (before line 558), at the
same indentation as the OSD update (line 550). This ensures RTSP works independently
of the web UI being enabled:

```python
            # Push to RTSP stream (independent of web UI)
            if self._rtsp is not None:
                self._rtsp.push_frame(annotated)
```

- [ ] **Step 5: Add RTSP shutdown**

In `_shutdown()`, before `self._camera.close()` (line 893), add:

```python
        if self._rtsp is not None:
            self._rtsp.stop()
```

- [ ] **Step 6: Add RTSP toggle callback and wire into stream_state.set_callbacks**

Add a handler method to the Pipeline class (after `_handle_rf_stop`, around line 870):

```python
    def _handle_rtsp_toggle(self, enabled: bool) -> dict:
        """Start or stop the RTSP server at runtime."""
        if enabled and self._rtsp is None:
            self._rtsp = RTSPServer(
                port=self._rtsp_port,
                mount=self._rtsp_mount,
                bitrate=self._rtsp_bitrate,
                width=self._cfg.getint("camera", "width", fallback=640),
                height=self._cfg.getint("camera", "height", fallback=480),
            )
            if self._rtsp.start():
                return {"status": "ok", "running": True, "url": self._rtsp.url}
            self._rtsp = None
            return {"status": "error", "message": "RTSP server failed to start"}
        elif not enabled and self._rtsp is not None:
            self._rtsp.stop()
            self._rtsp = None
            return {"status": "ok", "running": False}
        return {
            "status": "ok",
            "running": self._rtsp is not None and self._rtsp.running,
        }

    def _get_rtsp_status(self) -> dict:
        """Return RTSP server status for the web API."""
        if self._rtsp is not None and self._rtsp.running:
            return {
                "enabled": True,
                "running": True,
                "url": self._rtsp.url,
                "clients": self._rtsp.client_count,
            }
        return {
            "enabled": self._rtsp_enabled,
            "running": False,
            "url": f"rtsp://0.0.0.0:{self._rtsp_port}{self._rtsp_mount}",
            "clients": 0,
        }
```

In `stream_state.set_callbacks()` block (around line 381-406), add:

```python
                on_rtsp_toggle=self._handle_rtsp_toggle,
                get_rtsp_status=self._get_rtsp_status,
```

- [ ] **Step 7: Add RTSP stats to web update block**

In the hot loop stats update block (around line 588-591), add before `stats_update.update(self._jetson_stats)`:

```python
                if self._rtsp is not None:
                    stats_update["rtsp_clients"] = self._rtsp.client_count
```

- [ ] **Step 8: Add pipeline callback tests**

Add to `tests/test_pipeline_callbacks.py` (follow existing `_make_pipeline` pattern):

```python
# ---------------------------------------------------------------------------
# RTSP toggle / status
# ---------------------------------------------------------------------------

class TestRTSPCallbacks:
    def test_rtsp_status_when_disabled(self):
        p = _make_pipeline()
        p._rtsp = None
        p._rtsp_enabled = False
        p._rtsp_port = 8554
        p._rtsp_mount = "/hydra"
        status = p._get_rtsp_status()
        assert status["enabled"] is False
        assert status["running"] is False

    def test_rtsp_status_when_running(self):
        p = _make_pipeline()
        p._rtsp = MagicMock()
        p._rtsp.running = True
        p._rtsp.url = "rtsp://0.0.0.0:8554/hydra"
        p._rtsp.client_count = 2
        p._rtsp_enabled = True
        p._rtsp_port = 8554
        p._rtsp_mount = "/hydra"
        status = p._get_rtsp_status()
        assert status["running"] is True
        assert status["clients"] == 2

    def test_rtsp_toggle_off(self):
        p = _make_pipeline()
        p._rtsp = MagicMock()
        p._rtsp.running = True
        p._rtsp_enabled = True
        p._rtsp_port = 8554
        p._rtsp_mount = "/hydra"
        p._rtsp_bitrate = 2_000_000
        result = p._handle_rtsp_toggle(False)
        assert result["status"] == "ok"
        assert result["running"] is False
        assert p._rtsp is None
```

- [ ] **Step 9: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS (existing + new pipeline callback tests)

- [ ] **Step 10: Commit**

```bash
git add hydra_detect/pipeline.py config.ini tests/test_pipeline_callbacks.py
git commit -m "feat: wire RTSP server into pipeline — init, hot loop, shutdown, toggle"
```

---

## Task 3: Web API Endpoints

**Files:**
- Modify: `hydra_detect/web/server.py` (add 2 endpoints)
- Modify: `tests/test_web_api.py` (add RTSP tests)

- [ ] **Step 1: Write failing tests for RTSP endpoints**

Add to `tests/test_web_api.py`:

```python
# ---------------------------------------------------------------------------
# RTSP endpoints
# ---------------------------------------------------------------------------

class TestRTSPEndpoints:
    def test_rtsp_status_default(self, client):
        """Status endpoint returns shape even without callback."""
        resp = client.get("/api/rtsp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "running" in data

    def test_rtsp_toggle_requires_auth(self, client):
        configure_auth("secret-token-123")
        resp = client.post("/api/rtsp/toggle", json={"enabled": True})
        assert resp.status_code == 401

    def test_rtsp_toggle_with_auth(self, client):
        configure_auth("secret-token-123")
        called = {}
        def on_toggle(enabled):
            called["enabled"] = enabled
            return {"status": "ok", "running": enabled}
        stream_state.set_callbacks(on_rtsp_toggle=on_toggle)
        headers = {"Authorization": "Bearer secret-token-123"}
        resp = client.post("/api/rtsp/toggle", json={"enabled": True}, headers=headers)
        assert resp.status_code == 200
        assert called["enabled"] is True

    def test_rtsp_toggle_no_auth_when_disabled(self, client):
        configure_auth(None)
        called = {}
        def on_toggle(enabled):
            called["enabled"] = enabled
            return {"status": "ok", "running": enabled}
        stream_state.set_callbacks(on_rtsp_toggle=on_toggle)
        resp = client.post("/api/rtsp/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert called["enabled"] is False

    def test_rtsp_toggle_missing_body(self, client):
        resp = client.post("/api/rtsp/toggle", json={})
        assert resp.status_code == 400
```

Also add RTSP toggle endpoints to the `TestAuthEnforcement.CONTROL_ENDPOINTS` list:

```python
        ("POST", "/api/rtsp/toggle", {"enabled": True}),
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_api.py::TestRTSPEndpoints -v`
Expected: FAIL — 404 (endpoints don't exist yet)

- [ ] **Step 3: Implement endpoints in server.py**

Add after the RF Hunt endpoints section (after the `api_rf_stop` function, around line 674):

```python
# ── RTSP ─────────────────────────────────────────────────────

@app.get("/api/rtsp/status")
async def api_rtsp_status():
    """Return RTSP server status."""
    cb = stream_state.get_callback("get_rtsp_status")
    if cb:
        return cb()
    return {"enabled": False, "running": False, "url": "", "clients": 0}


@app.post("/api/rtsp/toggle")
async def api_rtsp_toggle(request: Request, authorization: Optional[str] = Header(None)):
    """Start or stop the RTSP server at runtime. Body: {"enabled": true/false}"""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "enabled field required (true/false)"}, status_code=400)
    cb = stream_state.get_callback("on_rtsp_toggle")
    if cb:
        result = cb(bool(enabled))
        _audit(request, "rtsp_toggle", target=str(enabled))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    _audit(request, "rtsp_toggle", outcome="unavailable")
    return JSONResponse({"error": "RTSP toggle not available"}, status_code=503)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_api.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/server.py tests/test_web_api.py
git commit -m "feat(api): add RTSP status and toggle endpoints"
```

---

## Task 4: Web UI — RTSP Toggle in Operations Panel

**Files:**
- Modify: `hydra_detect/web/templates/operations.html` (add toggle + URL display)
- Modify: `hydra_detect/web/static/js/operations.js` (wire toggle to API)

- [ ] **Step 1: Add RTSP toggle HTML to System panel**

In `operations.html`, inside the System panel body (after the power mode `<div class="panel-field">` block at line 148 and before `<div class="panel-pipeline-btns">` at line 149), add:

```html
            <div class="panel-field">
                <label class="panel-field-label">RTSP Stream</label>
                <div style="display:flex;align-items:center;gap:8px;">
                    <div class="toggle-switch" id="ctrl-rtsp-toggle" title="Toggle RTSP stream"></div>
                    <span class="panel-sys-val mono" id="ctrl-rtsp-status">OFF</span>
                </div>
                <div id="ctrl-rtsp-url" class="mono" style="font-size:var(--font-xs);color:var(--text-secondary);margin-top:4px;cursor:pointer;display:none;" title="Click to copy"></div>
            </div>
```

- [ ] **Step 2: Add RTSP functions to operations.js**

Add these functions to `operations.js` (inside the IIFE, after the RF Hunt functions):

```javascript
    // -- RTSP ----------------------------------------------------------

    async function loadRTSPStatus() {
        const data = await HydraApp.apiGet('/api/rtsp/status');
        if (!data) return;
        const toggle = document.getElementById('ctrl-rtsp-toggle');
        const status = document.getElementById('ctrl-rtsp-status');
        const urlEl = document.getElementById('ctrl-rtsp-url');
        if (!toggle || !status) return;

        if (data.running) {
            toggle.classList.add('active');
            status.textContent = data.clients > 0
                ? data.clients + ' client' + (data.clients !== 1 ? 's' : '')
                : 'ON';
            if (urlEl) {
                urlEl.textContent = data.url;
                urlEl.style.display = 'block';
            }
        } else {
            toggle.classList.remove('active');
            status.textContent = 'OFF';
            if (urlEl) urlEl.style.display = 'none';
        }
    }

    async function toggleRTSP() {
        const toggle = document.getElementById('ctrl-rtsp-toggle');
        if (!toggle) return;
        const nowActive = toggle.classList.contains('active');
        const resp = await HydraApp.apiPost('/api/rtsp/toggle', { enabled: !nowActive });
        if (resp) loadRTSPStatus();
    }
```

Wire the event handlers in the `wireEventHandlers` function (after the RF Hunt wiring, around line 203):

```javascript
        // RTSP toggle
        addClick('ctrl-rtsp-toggle', () => toggleRTSP());
        const rtspUrl = document.getElementById('ctrl-rtsp-url');
        if (rtspUrl) {
            rtspUrl.addEventListener('click', () => {
                navigator.clipboard.writeText(rtspUrl.textContent);
                rtspUrl.title = 'Copied!';
                setTimeout(() => { rtspUrl.title = 'Click to copy'; }, 1500);
            });
        }
```

Add `loadRTSPStatus()` inside the `loadDropdowns()` function (after the `loadPowerModes()` call, around line 46 of operations.js):

```javascript
    loadRTSPStatus();
```

Update the periodic stats refresh (wherever the stats poller runs) to also refresh RTSP status every ~5 seconds by adding inside the stats update handler:

```javascript
        // Refresh RTSP client count from stats
        if (data.rtsp_clients !== undefined) {
            const status = document.getElementById('ctrl-rtsp-status');
            if (status && document.getElementById('ctrl-rtsp-toggle')?.classList.contains('active')) {
                status.textContent = data.rtsp_clients > 0
                    ? data.rtsp_clients + ' client' + (data.rtsp_clients !== 1 ? 's' : '')
                    : 'ON';
            }
        }
```

- [ ] **Step 3: Verify in browser**

Open http://localhost:8080, check that:
- System panel shows RTSP toggle switch
- Toggle shows ON/OFF state
- URL appears when enabled and is copyable

- [ ] **Step 4: Commit**

```bash
git add hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js
git commit -m "feat(ui): add RTSP stream toggle and URL display in operations panel"
```

---

## Task 5: Docker & Service File Updates

**Files:**
- Modify: `Dockerfile` (add GStreamer packages, EXPOSE 8554)
- Modify: `scripts/hydra-detect.service` (add port mapping)

- [ ] **Step 1: Update Dockerfile**

After the existing `RUN pip3 install` block (line 27) and before `COPY hydra_detect/` (line 30), add:

```dockerfile
# GStreamer RTSP server for annotated video output
RUN apt-get update && apt-get install -y --no-install-recommends \
    gstreamer1.0-rtsp \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gir1.2-gst-rtsp-server-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gir1.2-gstreamer-1.0 \
    python3-gi \
    && rm -rf /var/lib/apt/lists/*
```

After the existing `EXPOSE 8080` (line 33), add:

```dockerfile
EXPOSE 8554
```

- [ ] **Step 2: Update systemd service**

In `scripts/hydra-detect.service`, add `-p 8554:8554` to the `docker run` command.
Change line 22 from:

```
  -p 8080:8080 \
```

to:

```
  -p 8080:8080 \
  -p 8554:8554 \
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile scripts/hydra-detect.service
git commit -m "build: add GStreamer RTSP deps to Docker, expose port 8554"
```

---

## Task 6: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run lint and type check**

Run: `flake8 hydra_detect/ tests/`
Run: `mypy hydra_detect/`
Fix any issues.

- [ ] **Step 3: Test RTSP stream manually (on Jetson)**

1. Start Hydra: `sudo python3 -m hydra_detect --config config.ini`
2. Open VLC or ffplay: `ffplay rtsp://<jetson-ip>:8554/hydra`
3. Verify annotated frames with bounding boxes appear
4. Check web UI shows RTSP toggle ON and client count = 1
5. Toggle off via web UI, verify stream stops
6. Toggle back on, verify stream resumes

- [ ] **Step 4: Test in Mission Planner**

1. In Mission Planner, go to HUD → right-click → Video → Set GStreamer Source
2. Enter: `rtspsrc location=rtsp://<jetson-ip>:8554/hydra latency=0 ! decodebin ! videoconvert ! autovideosink`
3. Verify live annotated feed appears in the HUD

- [ ] **Step 5: Commit any final fixes**

```bash
git add hydra_detect/rtsp_server.py hydra_detect/pipeline.py hydra_detect/web/server.py tests/
git commit -m "fix: address lint/type issues from RTSP integration"
```
