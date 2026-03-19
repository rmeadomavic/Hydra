# MAVLink Video Thumbnail Stream — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream low-resolution annotated detection thumbnails over MAVLink telemetry radio so Mission Planner displays live detection imagery without an IP link.

**Architecture:** A new `mavlink_video.py` module downscales annotated frames, JPEG-encodes them, and sends via MAVLink `DATA_TRANSMISSION_HANDSHAKE` + `ENCAPSULATED_DATA` on its own thread. A send lock in `MAVLinkIO` prevents packet interleaving with other MAVLink sends. Frame rate adapts to JPEG size and configured link budget.

**Tech Stack:** pymavlink (DATA_TRANSMISSION_HANDSHAKE/ENCAPSULATED_DATA), OpenCV (resize/imencode), threading

**Spec:** `docs/superpowers/specs/2026-03-19-mavlink-video-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `hydra_detect/mavlink_io.py` | Add `_send_lock`, wrap existing sends, expose `send_lock` property |
| Create | `hydra_detect/mavlink_video.py` | MAVLink video sender — resize, encode, chunk, send |
| Create | `tests/test_mavlink_video.py` | Unit tests for sender module |
| Modify | `hydra_detect/pipeline.py` | Wire mavlink_video into init, hot loop, shutdown, callbacks |
| Modify | `hydra_detect/web/server.py` | Add 3 API endpoints |
| Modify | `hydra_detect/web/templates/operations.html` | MAVLink Video toggle + stats + sliders |
| Modify | `hydra_detect/web/static/js/operations.js` | Wire UI to API |
| Modify | `tests/test_web_api.py` | Endpoint tests |
| Modify | `tests/test_pipeline_callbacks.py` | Pipeline callback tests |
| Modify | `config.ini` | Add `[mavlink_video]` section |

---

## Task 1: Add Send Lock to MAVLinkIO

**Files:**
- Modify: `hydra_detect/mavlink_io.py`
- Modify: `tests/test_mavlink_safety.py` (or new test)

- [ ] **Step 1: Add `_send_lock` to MAVLinkIO.__init__**

In `mavlink_io.py`, after `self._cmd_callbacks_lock = threading.Lock()` (line 77), add:

```python
        # Serializes all MAVLink sends (prevents interleaving from video thread)
        self._send_lock = threading.Lock()
```

Add public accessor after the `connected` property:

```python
    @property
    def send_lock(self) -> threading.Lock:
        """Lock for serializing MAVLink send operations."""
        return self._send_lock

    @property
    def mav(self):
        """Raw pymavlink connection (for MAVLink video sender)."""
        return self._mav
```

- [ ] **Step 2: Wrap existing send methods with send_lock**

Wrap these methods with `with self._send_lock:` around the `self._mav.mav.*_send()` calls:

- `send_statustext` (line 389): already uses `self._lock` for throttle — add `with self._send_lock:` around the inner `self._mav.mav.send(msg)` call (line 401)
- `command_loiter` (line 439): wrap `self._mav.set_mode_apm()` call
- `set_roi` (line 455): wrap `command_long_send` call
- `clear_roi` (line 474): wrap `command_long_send` call
- `adjust_yaw` (line 494): wrap `command_long_send` call
- `command_guided_to` (line 550): wrap `set_mode_apm` and `set_position_target_global_int_send` calls
- `set_servo` (line 649): wrap `command_long_send` call
- `_send_command_ack` (line 332): wrap `command_ack_send` call
- `set_mode` method if it exists: wrap `set_mode_apm` call

For each, the pattern is:

```python
        with self._send_lock:
            self._mav.mav.command_long_send(...)
```

Do NOT change the `_message_reader` thread — it only reads, never sends.

- [ ] **Step 3: Run existing tests**

Run: `python -m pytest tests/ -v`
Expected: All 374+ tests still PASS (send lock is transparent to existing functionality)

- [ ] **Step 4: Commit**

```bash
git add hydra_detect/mavlink_io.py
git commit -m "feat: add send_lock to MAVLinkIO for thread-safe MAVLink sends"
```

---

## Task 2: MAVLink Video Sender Module

**Files:**
- Create: `hydra_detect/mavlink_video.py`
- Create: `tests/test_mavlink_video.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_mavlink_video.py`:

```python
"""Unit tests for MAVLink video thumbnail sender."""

from __future__ import annotations

import math
import threading
import time
from unittest.mock import MagicMock, call, patch

import cv2
import numpy as np
import pytest

from hydra_detect.mavlink_video import MAVLinkVideoSender


@pytest.fixture
def mock_mavlink():
    """Mock MAVLinkIO with a mav object and send_lock."""
    mav_io = MagicMock()
    mav_io.connected = True
    mav_io.send_lock = threading.Lock()
    mav_io.mav = MagicMock()
    mav_io.mav.mav = MagicMock()
    return mav_io


class TestChunking:
    def test_chunk_count_for_5000_bytes(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        chunks = sender._chunk_jpeg(bytes(5000))
        assert len(chunks) == math.ceil(5000 / 253)  # 20 packets

    def test_last_chunk_zero_padded(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        # Simulate a 500-byte JPEG (500 % 253 = 247, needs padding to 253)
        chunks = sender._chunk_jpeg(bytes(range(244)) * 2 + bytes(12))
        last = chunks[-1]
        assert len(last) == 253

    def test_handshake_fields(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120, jpeg_quality=20)
        jpeg_data = bytes(500)
        sender._send_frame(jpeg_data, 160, 120)
        # Verify handshake was called with correct fields
        mock_mavlink._mav.mav.data_transmission_handshake_send.assert_called_once_with(
            0,           # type: JPEG
            500,         # size
            160,         # width
            120,         # height
            math.ceil(500 / 253),  # packets
            253,         # payload size
            20,          # jpg_quality
        )


class TestPushFrame:
    def test_push_frame_is_nonblocking(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        start = time.monotonic()
        sender.push_frame(frame)
        elapsed = time.monotonic() - start
        assert elapsed < 0.01  # Should be sub-millisecond

    def test_push_frame_noop_when_stopped(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sender.push_frame(frame)  # Should not raise


class TestLifecycle:
    def test_start_returns_false_without_mavlink(self):
        sender = MAVLinkVideoSender(None, width=160, height=120)
        assert sender.start() is False

    def test_start_returns_false_when_disconnected(self):
        mav_io = MagicMock()
        mav_io.connected = False
        sender = MAVLinkVideoSender(mav_io, width=160, height=120)
        assert sender.start() is False

    def test_start_stop_lifecycle(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        assert sender.start() is True
        assert sender.running is True
        sender.stop()
        assert sender.running is False

    def test_status_dict(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120, jpeg_quality=20)
        status = sender.get_status()
        assert status["width"] == 160
        assert status["height"] == 120
        assert status["quality"] == 20
        assert status["running"] is False


class TestStaleFrameDetection:
    def test_skips_stale_frame(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        # Push one frame, send it, then don't push again
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sender.push_frame(frame)
        # After one send cycle, generation should match — next cycle skips
        assert sender._generation == 1


class TestAdaptiveRate:
    def test_large_jpeg_increases_interval(self, mock_mavlink):
        sender = MAVLinkVideoSender(
            mock_mavlink, width=160, height=120,
            max_fps=2.0, min_fps=0.2, link_budget_bytes_sec=8000,
        )
        # 8000 byte JPEG at 8000 bps budget → 1s transmit → 2s interval (50% duty)
        interval = sender._compute_interval(8000)
        assert interval == 2.0

    def test_small_jpeg_decreases_interval(self, mock_mavlink):
        sender = MAVLinkVideoSender(
            mock_mavlink, width=160, height=120,
            max_fps=2.0, min_fps=0.2, link_budget_bytes_sec=8000,
        )
        # 1000 byte JPEG → 0.125s transmit → 0.25s interval, clamped to 1/max_fps=0.5
        interval = sender._compute_interval(1000)
        assert interval == 0.5  # clamped to 1/max_fps

    def test_interval_clamped_to_min_fps(self, mock_mavlink):
        sender = MAVLinkVideoSender(
            mock_mavlink, width=160, height=120,
            max_fps=2.0, min_fps=0.2, link_budget_bytes_sec=8000,
        )
        # 50000 byte JPEG → 6.25s transmit → 12.5s, clamped to 1/min_fps=5.0
        interval = sender._compute_interval(50000)
        assert interval == 5.0  # clamped to 1/min_fps


class TestSetParams:
    def test_rejects_out_of_range(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        assert sender.set_params(width=5000) is False
        assert sender.set_params(quality=100) is False
        assert sender.set_params(max_fps=10.0) is False

    def test_accepts_valid_params(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        assert sender.set_params(width=80, height=60, quality=30) is True
        status = sender.get_status()
        assert status["width"] == 80
        assert status["height"] == 60
        assert status["quality"] == 30


class TestSendLock:
    def test_send_acquires_lock(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        jpeg_data = bytes(500)
        # Replace send_lock with a tracking mock
        lock = threading.Lock()
        acquired = []
        original_acquire = lock.acquire
        def track_acquire(*a, **kw):
            acquired.append(True)
            return original_acquire(*a, **kw)
        lock.acquire = track_acquire
        mock_mavlink.send_lock = lock
        sender._send_frame(jpeg_data, 160, 120)
        assert len(acquired) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mavlink_video.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement MAVLinkVideoSender**

Create `hydra_detect/mavlink_video.py`:

```python
"""MAVLink video — sends annotated detection thumbnails over telemetry radio."""

from __future__ import annotations

import logging
import math
import threading
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)

CHUNK_SIZE = 253  # Max bytes per ENCAPSULATED_DATA packet
INTER_PACKET_DELAY = 0.002  # 2ms pacing between chunks


class MAVLinkVideoSender:
    """Downscale, JPEG-encode, and send annotated frames via MAVLink.

    Uses DATA_TRANSMISSION_HANDSHAKE + ENCAPSULATED_DATA protocol.
    Mission Planner reassembles and displays the thumbnails.
    """

    def __init__(
        self,
        mavlink_io,
        width: int = 160,
        height: int = 120,
        jpeg_quality: int = 20,
        max_fps: float = 2.0,
        min_fps: float = 0.2,
        link_budget_bytes_sec: int = 8000,
    ):
        self._mavlink_io = mavlink_io
        self._width = width
        self._height = height
        self._jpeg_quality = jpeg_quality
        self._max_fps = max_fps
        self._min_fps = min_fps
        self._link_budget_bytes_sec = link_budget_bytes_sec

        self._frame = None
        self._generation = 0
        self._last_sent_gen = -1
        self._frame_lock = threading.Lock()
        self._params_lock = threading.Lock()

        self._running = False
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._interval = 1.0 / max_fps
        self._current_fps = 0.0
        self._bytes_per_sec = 0.0

    # -- Public interface ---------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        """Start the sender thread. Returns False if MAVLink not available."""
        if self._mavlink_io is None or not self._mavlink_io.connected:
            logger.warning("MAVLink video: no MAVLink connection — disabled.")
            return False
        if self._running:
            return True

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._sender_loop, daemon=True, name="mav-video",
        )
        self._thread.start()
        self._running = True
        logger.info(
            "MAVLink video started: %dx%d Q%d max %.1f FPS",
            self._width, self._height, self._jpeg_quality, self._max_fps,
        )
        return True

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("MAVLink video stopped.")

    def push_frame(self, frame: np.ndarray) -> None:
        """Swap in the latest frame. Zero-cost — no copy or encode."""
        with self._frame_lock:
            self._frame = frame
            self._generation += 1

    def get_status(self) -> dict:
        with self._params_lock:
            return {
                "enabled": True,
                "running": self._running,
                "width": self._width,
                "height": self._height,
                "quality": self._jpeg_quality,
                "current_fps": round(self._current_fps, 2),
                "bytes_per_sec": round(self._bytes_per_sec, 0),
            }

    def set_params(
        self,
        width: int | None = None,
        height: int | None = None,
        quality: int | None = None,
        max_fps: float | None = None,
    ) -> bool:
        """Live tuning. Returns False if any value out of range."""
        if width is not None and not (40 <= width <= 320):
            return False
        if height is not None and not (30 <= height <= 240):
            return False
        if quality is not None and not (5 <= quality <= 50):
            return False
        if max_fps is not None and not (0.1 <= max_fps <= 5.0):
            return False

        with self._params_lock:
            if width is not None:
                self._width = width
            if height is not None:
                self._height = height
            if quality is not None:
                self._jpeg_quality = quality
            if max_fps is not None:
                self._max_fps = max_fps
                self._interval = min(self._interval, 1.0 / max_fps)
        return True

    # -- Internal -----------------------------------------------------------

    def _sender_loop(self) -> None:
        """Background thread: grab frame, encode, send, adapt rate."""
        last_send_time = 0.0

        while not self._stop_evt.is_set():
            self._stop_evt.wait(timeout=self._interval)
            if self._stop_evt.is_set():
                break

            # Grab latest frame
            with self._frame_lock:
                frame = self._frame
                gen = self._generation

            if frame is None or gen == self._last_sent_gen:
                continue  # No frame or stale

            self._last_sent_gen = gen

            try:
                # Read current params under lock
                with self._params_lock:
                    w, h = self._width, self._height
                    quality = self._jpeg_quality

                # Downscale
                thumb = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)

                # JPEG encode
                ok, buf = cv2.imencode(
                    '.jpg', thumb,
                    [cv2.IMWRITE_JPEG_QUALITY, quality],
                )
                if not ok:
                    continue

                jpeg_bytes = buf.tobytes()
                jpeg_size = len(jpeg_bytes)

                # Send via MAVLink
                self._send_frame(jpeg_bytes, w, h)

                # Update stats
                now = time.monotonic()
                if last_send_time > 0:
                    dt = now - last_send_time
                    self._current_fps = 1.0 / dt if dt > 0 else 0.0
                    self._bytes_per_sec = jpeg_size / dt if dt > 0 else 0.0
                last_send_time = now

                # Adapt interval
                self._interval = self._compute_interval(jpeg_size)

            except Exception as exc:
                logger.warning("MAVLink video send error: %s", exc)

    def _send_frame(self, jpeg_bytes: bytes, width: int, height: int) -> None:
        """Send one JPEG frame as HANDSHAKE + ENCAPSULATED_DATA packets."""
        mav = self._mavlink_io.mav
        if mav is None:
            return

        chunks = self._chunk_jpeg(jpeg_bytes)
        num_packets = len(chunks)

        with self._mavlink_io.send_lock:
            # Handshake
            mav.mav.data_transmission_handshake_send(
                0,                    # type: JPEG
                len(jpeg_bytes),      # size
                width,                # width
                height,               # height
                num_packets,          # packets
                CHUNK_SIZE,           # payload
                self._jpeg_quality,   # jpg_quality
            )

            # Chunks with pacing
            for seq, chunk in enumerate(chunks):
                mav.mav.encapsulated_data_send(seq, chunk)
                if seq < num_packets - 1:
                    time.sleep(INTER_PACKET_DELAY)

    @staticmethod
    def _chunk_jpeg(jpeg_bytes: bytes) -> list[list[int]]:
        """Split JPEG bytes into 253-int lists for ENCAPSULATED_DATA."""
        chunks = []
        for i in range(0, len(jpeg_bytes), CHUNK_SIZE):
            chunk = list(jpeg_bytes[i:i + CHUNK_SIZE])
            # Zero-pad last chunk to exactly 253
            if len(chunk) < CHUNK_SIZE:
                chunk.extend([0] * (CHUNK_SIZE - len(chunk)))
            chunks.append(chunk)
        return chunks

    def _compute_interval(self, jpeg_size: int) -> float:
        """Compute next send interval from JPEG size and link budget."""
        with self._params_lock:
            max_fps = self._max_fps
            min_fps = self._min_fps
            budget = self._link_budget_bytes_sec

        # Estimate transmit time, 50% duty cycle
        tx_time = jpeg_size / budget if budget > 0 else 1.0
        interval = tx_time * 2.0

        # Clamp
        min_interval = 1.0 / max_fps
        max_interval = 1.0 / min_fps
        return max(min_interval, min(max_interval, interval))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mavlink_video.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/mavlink_video.py tests/test_mavlink_video.py
git commit -m "feat: add MAVLink video thumbnail sender module"
```

---

## Task 3: Pipeline Integration

**Files:**
- Modify: `hydra_detect/pipeline.py`
- Modify: `config.ini`
- Modify: `tests/test_pipeline_callbacks.py`

- [ ] **Step 1: Add `[mavlink_video]` section to config.ini**

Append after the `[rtsp]` section:

```ini

[mavlink_video]
enabled = true
width = 160
height = 120
jpeg_quality = 20
max_fps = 2.0
min_fps = 0.2
link_budget_bytes_sec = 8000
```

- [ ] **Step 2: Add import and init to Pipeline**

In `pipeline.py`, add import after the RTSPServer import (line 33):

```python
from .mavlink_video import MAVLinkVideoSender
```

In `Pipeline.__init__`, after the RTSP config block (after line 304), add:

```python
        # MAVLink video thumbnails
        self._mavlink_video: MAVLinkVideoSender | None = None
        self._mavlink_video_enabled = self._cfg.getboolean(
            "mavlink_video", "enabled", fallback=True
        )
```

- [ ] **Step 3: Add start in Pipeline.start()**

After the RTSP start block (after line 435), add:

```python
        # Start MAVLink video thumbnails
        if self._mavlink_video_enabled and self._mavlink is not None:
            self._mavlink_video = MAVLinkVideoSender(
                self._mavlink,
                width=self._cfg.getint("mavlink_video", "width", fallback=160),
                height=self._cfg.getint("mavlink_video", "height", fallback=120),
                jpeg_quality=self._cfg.getint("mavlink_video", "jpeg_quality", fallback=20),
                max_fps=self._cfg.getfloat("mavlink_video", "max_fps", fallback=2.0),
                min_fps=self._cfg.getfloat("mavlink_video", "min_fps", fallback=0.2),
                link_budget_bytes_sec=self._cfg.getint("mavlink_video", "link_budget_bytes_sec", fallback=8000),
            )
            if not self._mavlink_video.start():
                logger.warning("MAVLink video failed to start — continuing without.")
                self._mavlink_video = None
```

- [ ] **Step 4: Add push_frame to hot loop**

After the RTSP push_frame (after line 582), add:

```python
            # Push to MAVLink video (thumbnail over telemetry radio)
            if self._mavlink_video is not None:
                self._mavlink_video.push_frame(annotated)
```

- [ ] **Step 5: Add shutdown**

In `_shutdown()`, after the RTSP stop (after line 894), add:

```python
        if self._mavlink_video is not None:
            self._mavlink_video.stop()
```

- [ ] **Step 6: Add toggle/tune/status handlers and wire callbacks**

Add after `_get_rtsp_status` method (around line 908):

```python
    def _handle_mavlink_video_toggle(self, enabled: bool) -> dict:
        """Start or stop MAVLink video at runtime."""
        if enabled and self._mavlink_video is None:
            if self._mavlink is None:
                return {"status": "error", "message": "MAVLink not connected"}
            self._mavlink_video = MAVLinkVideoSender(
                self._mavlink,
                width=self._cfg.getint("mavlink_video", "width", fallback=160),
                height=self._cfg.getint("mavlink_video", "height", fallback=120),
                jpeg_quality=self._cfg.getint("mavlink_video", "jpeg_quality", fallback=20),
                max_fps=self._cfg.getfloat("mavlink_video", "max_fps", fallback=2.0),
                min_fps=self._cfg.getfloat("mavlink_video", "min_fps", fallback=0.2),
                link_budget_bytes_sec=self._cfg.getint("mavlink_video", "link_budget_bytes_sec", fallback=8000),
            )
            if self._mavlink_video.start():
                return {"status": "ok", "running": True}
            self._mavlink_video = None
            return {"status": "error", "message": "Failed to start"}
        elif not enabled and self._mavlink_video is not None:
            self._mavlink_video.stop()
            self._mavlink_video = None
            return {"status": "ok", "running": False}
        return {"status": "ok", "running": self._mavlink_video is not None}

    def _handle_mavlink_video_tune(self, params: dict) -> dict:
        """Live-tune MAVLink video parameters."""
        if self._mavlink_video is None:
            return {"status": "error", "message": "Not running"}
        if self._mavlink_video.set_params(**params):
            return {"status": "ok", **self._mavlink_video.get_status()}
        return {"status": "error", "message": "Invalid parameter value"}

    def _get_mavlink_video_status(self) -> dict:
        """Return MAVLink video status for web API."""
        if self._mavlink_video is not None:
            return self._mavlink_video.get_status()
        return {
            "enabled": self._mavlink_video_enabled,
            "running": False,
            "width": 0, "height": 0, "quality": 0,
            "current_fps": 0, "bytes_per_sec": 0,
        }
```

Wire into `stream_state.set_callbacks()` (around line 415), add:

```python
                on_mavlink_video_toggle=self._handle_mavlink_video_toggle,
                on_mavlink_video_tune=self._handle_mavlink_video_tune,
                get_mavlink_video_status=self._get_mavlink_video_status,
```

- [ ] **Step 7: Add mavlink_video stats to web update block**

In the hot loop stats block (around line 600), after the RTSP clients line, add:

```python
                if self._mavlink_video is not None:
                    mv_status = self._mavlink_video.get_status()
                    stats_update["mavlink_video_fps"] = mv_status["current_fps"]
                    stats_update["mavlink_video_kbps"] = round(mv_status["bytes_per_sec"] / 1024, 1)
```

- [ ] **Step 8: Add pipeline callback tests**

Add to `tests/test_pipeline_callbacks.py`:

```python
# ---------------------------------------------------------------------------
# MAVLink Video toggle / status
# ---------------------------------------------------------------------------

class TestMAVLinkVideoCallbacks:
    def test_mavlink_video_status_when_disabled(self):
        p = _make_pipeline()
        p._mavlink_video = None
        p._mavlink_video_enabled = False
        status = p._get_mavlink_video_status()
        assert status["enabled"] is False
        assert status["running"] is False

    def test_mavlink_video_status_when_running(self):
        p = _make_pipeline()
        p._mavlink_video = MagicMock()
        p._mavlink_video.get_status.return_value = {
            "enabled": True, "running": True, "width": 160, "height": 120,
            "quality": 20, "current_fps": 1.5, "bytes_per_sec": 5000,
        }
        status = p._get_mavlink_video_status()
        assert status["running"] is True
        assert status["current_fps"] == 1.5

    def test_mavlink_video_toggle_off(self):
        p = _make_pipeline()
        p._mavlink_video = MagicMock()
        p._mavlink_video_enabled = True
        result = p._handle_mavlink_video_toggle(False)
        assert result["status"] == "ok"
        assert p._mavlink_video is None
```

- [ ] **Step 9: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add hydra_detect/pipeline.py config.ini tests/test_pipeline_callbacks.py
git commit -m "feat: wire MAVLink video into pipeline — init, loop, shutdown, callbacks"
```

---

## Task 4: Web API Endpoints

**Files:**
- Modify: `hydra_detect/web/server.py`
- Modify: `tests/test_web_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_web_api.py`:

```python
# ---------------------------------------------------------------------------
# MAVLink Video endpoints
# ---------------------------------------------------------------------------

class TestMAVLinkVideoEndpoints:
    def test_status_default(self, client):
        resp = client.get("/api/mavlink-video/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data

    def test_toggle_requires_auth(self, client):
        configure_auth("secret-token-123")
        resp = client.post("/api/mavlink-video/toggle", json={"enabled": True})
        assert resp.status_code == 401

    def test_toggle_works(self, client):
        called = {}
        def on_toggle(enabled):
            called["enabled"] = enabled
            return {"status": "ok", "running": enabled}
        stream_state.set_callbacks(on_mavlink_video_toggle=on_toggle)
        resp = client.post("/api/mavlink-video/toggle", json={"enabled": True})
        assert resp.status_code == 200

    def test_toggle_missing_field(self, client):
        resp = client.post("/api/mavlink-video/toggle", json={})
        assert resp.status_code == 400

    def test_tune_validates_range(self, client):
        called = {}
        def on_tune(params):
            called.update(params)
            return {"status": "error", "message": "Invalid parameter value"}
        stream_state.set_callbacks(on_mavlink_video_tune=on_tune)
        resp = client.post("/api/mavlink-video/tune", json={"width": 5000})
        assert resp.status_code == 200  # Endpoint passes through, module rejects

    def test_tune_success(self, client):
        def on_tune(params):
            return {"status": "ok", "width": 80, "height": 60}
        stream_state.set_callbacks(on_mavlink_video_tune=on_tune)
        resp = client.post("/api/mavlink-video/tune", json={"width": 80, "height": 60})
        assert resp.status_code == 200
```

Add to `TestAuthEnforcement.CONTROL_ENDPOINTS`:

```python
        ("POST", "/api/mavlink-video/toggle", {"enabled": True}),
        ("POST", "/api/mavlink-video/tune", {"width": 80}),
```

- [ ] **Step 2: Implement endpoints in server.py**

After the RTSP endpoints section (after line 706), add:

```python
# ── MAVLink Video ────────────────────────────────────────────

@app.get("/api/mavlink-video/status")
async def api_mavlink_video_status():
    """Return MAVLink video thumbnail stream status."""
    cb = stream_state.get_callback("get_mavlink_video_status")
    if cb:
        return cb()
    return {"enabled": False, "running": False, "width": 0, "height": 0,
            "quality": 0, "current_fps": 0, "bytes_per_sec": 0}


@app.post("/api/mavlink-video/toggle")
async def api_mavlink_video_toggle(request: Request, authorization: Optional[str] = Header(None)):
    """Start or stop MAVLink video. Body: {"enabled": true/false}"""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "enabled field required"}, status_code=400)
    cb = stream_state.get_callback("on_mavlink_video_toggle")
    if cb:
        result = cb(bool(enabled))
        _audit(request, "mavlink_video_toggle", target=str(enabled))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    return JSONResponse({"error": "MAVLink video not available"}, status_code=503)


@app.post("/api/mavlink-video/tune")
async def api_mavlink_video_tune(request: Request, authorization: Optional[str] = Header(None)):
    """Live-tune MAVLink video params. Body: {width, height, quality, max_fps} (all optional)"""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    # Validate ranges server-side
    for field, lo, hi in [("width", 40, 320), ("height", 30, 240),
                          ("quality", 5, 50), ("max_fps", 0.1, 5.0)]:
        val = body.get(field)
        if val is not None:
            try:
                val = float(val) if field == "max_fps" else int(val)
                if not (lo <= val <= hi):
                    return JSONResponse({"error": f"{field} must be {lo}-{hi}"}, status_code=400)
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{field} must be a number"}, status_code=400)
    cb = stream_state.get_callback("on_mavlink_video_tune")
    if cb:
        result = cb(body)
        _audit(request, "mavlink_video_tune", target=str(body))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    return JSONResponse({"error": "MAVLink video not available"}, status_code=503)
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_web_api.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add hydra_detect/web/server.py tests/test_web_api.py
git commit -m "feat(api): add MAVLink video status, toggle, and tune endpoints"
```

---

## Task 5: Web UI — MAVLink Video Controls

**Files:**
- Modify: `hydra_detect/web/templates/operations.html`
- Modify: `hydra_detect/web/static/js/operations.js`

- [ ] **Step 1: Add HTML to operations.html**

After the RTSP toggle `<div class="panel-field">` block (after line 156, before `<div class="panel-pipeline-btns">`), add:

```html
            <div class="panel-field">
                <label class="panel-field-label">MAVLink Video</label>
                <div style="display:flex;align-items:center;gap:8px;">
                    <div class="toggle-switch" id="ctrl-mvid-toggle" title="Toggle MAVLink video thumbnails"></div>
                    <span class="panel-sys-val mono" id="ctrl-mvid-status">OFF</span>
                </div>
                <div id="ctrl-mvid-details" style="display:none;margin-top:6px;">
                    <div class="panel-range-row" style="margin-bottom:4px;">
                        <span class="panel-field-label" style="min-width:50px;margin:0;">Res</span>
                        <input type="range" id="ctrl-mvid-res" min="60" max="320" step="20" value="160" style="flex:1;">
                        <span class="panel-range-val mono" id="ctrl-mvid-res-val">160</span>
                    </div>
                    <div class="panel-range-row">
                        <span class="panel-field-label" style="min-width:50px;margin:0;">Quality</span>
                        <input type="range" id="ctrl-mvid-quality" min="5" max="50" step="5" value="20" style="flex:1;">
                        <span class="panel-range-val mono" id="ctrl-mvid-quality-val">20</span>
                    </div>
                </div>
            </div>
```

- [ ] **Step 2: Add JS functions to operations.js**

After the RTSP functions, add:

```javascript
    // -- MAVLink Video --------------------------------------------------

    async function loadMAVLinkVideoStatus() {
        const data = await HydraApp.apiGet('/api/mavlink-video/status');
        if (!data) return;
        const toggle = document.getElementById('ctrl-mvid-toggle');
        const status = document.getElementById('ctrl-mvid-status');
        const details = document.getElementById('ctrl-mvid-details');
        if (!toggle || !status) return;

        if (data.running) {
            toggle.classList.add('active');
            const kbps = (data.bytes_per_sec / 1024).toFixed(1);
            status.textContent = data.current_fps.toFixed(1) + ' FPS / ' + kbps + ' KB/s';
            if (details) details.style.display = 'block';
        } else {
            toggle.classList.remove('active');
            status.textContent = 'OFF';
            if (details) details.style.display = 'none';
        }
    }

    async function toggleMAVLinkVideo() {
        const toggle = document.getElementById('ctrl-mvid-toggle');
        if (!toggle) return;
        const nowActive = toggle.classList.contains('active');
        await HydraApp.apiPost('/api/mavlink-video/toggle', { enabled: !nowActive });
        loadMAVLinkVideoStatus();
    }

    async function tuneMAVLinkVideo(params) {
        await HydraApp.apiPost('/api/mavlink-video/tune', params);
    }
```

Wire handlers in `wireEventHandlers` (after RTSP wiring):

```javascript
        // MAVLink Video
        addClick('ctrl-mvid-toggle', () => toggleMAVLinkVideo());
        const mvidRes = document.getElementById('ctrl-mvid-res');
        const mvidResVal = document.getElementById('ctrl-mvid-res-val');
        if (mvidRes) {
            mvidRes.addEventListener('input', function() {
                if (mvidResVal) mvidResVal.textContent = this.value;
            });
            mvidRes.addEventListener('change', function() {
                const v = parseInt(this.value);
                tuneMAVLinkVideo({ width: v, height: Math.round(v * 0.75) });
            });
        }
        const mvidQ = document.getElementById('ctrl-mvid-quality');
        const mvidQVal = document.getElementById('ctrl-mvid-quality-val');
        if (mvidQ) {
            mvidQ.addEventListener('input', function() {
                if (mvidQVal) mvidQVal.textContent = this.value;
            });
            mvidQ.addEventListener('change', function() {
                tuneMAVLinkVideo({ quality: parseInt(this.value) });
            });
        }
```

Add `loadMAVLinkVideoStatus();` in `loadDropdowns()` after `loadRTSPStatus()`.

Add stats refresh in the stats update handler:

```javascript
        if (data.mavlink_video_fps !== undefined) {
            const status = document.getElementById('ctrl-mvid-status');
            if (status && document.getElementById('ctrl-mvid-toggle')?.classList.contains('active')) {
                const kbps = (data.mavlink_video_kbps || 0).toFixed(1);
                status.textContent = data.mavlink_video_fps.toFixed(1) + ' FPS / ' + kbps + ' KB/s';
            }
        }
```

- [ ] **Step 3: Commit**

```bash
git add hydra_detect/web/templates/operations.html hydra_detect/web/static/js/operations.js
git commit -m "feat(ui): add MAVLink video toggle, stats, and tuning sliders"
```

---

## Task 6: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Lint and type check**

Run: `flake8 hydra_detect/ tests/`
Run: `mypy hydra_detect/`
Fix any issues.

- [ ] **Step 3: Restart Hydra and verify**

Kill and restart:
```bash
sudo kill $(sudo lsof -ti :8080) 2>/dev/null
sleep 2
sudo nohup python3 -m hydra_detect --config config.ini > /tmp/hydra.log 2>&1 &
sleep 10
grep -i "mavlink video" /tmp/hydra.log
```

Expected: `MAVLink video started: 160x120 Q20 max 2.0 FPS`

Check API: `curl -s http://localhost:8080/api/mavlink-video/status`

- [ ] **Step 4: Test with Mission Planner**

1. Connect Mission Planner to the Pixhawk over RFD900
2. MAVLink video should appear in MP's Video pane automatically
3. Check web UI shows MAVLink Video toggle ON with FPS/KB stats
4. Adjust resolution slider — verify stats change

- [ ] **Step 5: Push**

```bash
git push origin main
```
