"""MAVLink video — sends annotated detection thumbnails over telemetry radio."""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)

CHUNK_SIZE = 253
INTER_PACKET_DELAY = 0.002


class MAVLinkVideoSender:
    """Downscale, JPEG-encode, and send annotated frames via MAVLink."""

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
        self._link_budget = link_budget_bytes_sec

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

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._mavlink_io is None or not self._mavlink_io.connected:
            logger.warning("MAVLink video: no connection — disabled.")
            return False
        if self._running:
            return True
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._sender_loop, daemon=True, name="mav-video",
        )
        self._thread.start()
        self._running = True
        logger.info("MAVLink video started: %dx%d Q%d max %.1f FPS",
                    self._width, self._height, self._jpeg_quality, self._max_fps)
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

    def _sender_loop(self) -> None:
        last_send_time = 0.0
        while not self._stop_evt.is_set():
            self._stop_evt.wait(timeout=self._interval)
            if self._stop_evt.is_set():
                break
            with self._frame_lock:
                frame = self._frame
                gen = self._generation
            if frame is None or gen == self._last_sent_gen:
                continue
            self._last_sent_gen = gen
            try:
                with self._params_lock:
                    w, h, quality = self._width, self._height, self._jpeg_quality
                thumb = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode('.jpg', thumb, [cv2.IMWRITE_JPEG_QUALITY, quality])
                if not ok:
                    continue
                jpeg_bytes = buf.tobytes()
                self._send_frame(jpeg_bytes, w, h)
                now = time.monotonic()
                if last_send_time > 0:
                    dt = now - last_send_time
                    self._current_fps = 1.0 / dt if dt > 0 else 0.0
                    self._bytes_per_sec = len(jpeg_bytes) / dt if dt > 0 else 0.0
                last_send_time = now
                self._interval = self._compute_interval(len(jpeg_bytes))
            except Exception as exc:
                logger.warning("MAVLink video send error: %s", exc)

    def _send_frame(self, jpeg_bytes: bytes, width: int, height: int) -> None:
        mav = self._mavlink_io.mav
        if mav is None:
            return
        chunks = self._chunk_jpeg(jpeg_bytes)
        with self._mavlink_io.send_lock:
            mav.mav.data_transmission_handshake_send(
                0, len(jpeg_bytes), width, height, len(chunks), CHUNK_SIZE, self._jpeg_quality,
            )
            for seq, chunk in enumerate(chunks):
                mav.mav.encapsulated_data_send(seq, chunk)
                if seq < len(chunks) - 1:
                    time.sleep(INTER_PACKET_DELAY)

    @staticmethod
    def _chunk_jpeg(jpeg_bytes: bytes) -> list[list[int]]:
        chunks = []
        for i in range(0, len(jpeg_bytes), CHUNK_SIZE):
            chunk = list(jpeg_bytes[i:i + CHUNK_SIZE])
            if len(chunk) < CHUNK_SIZE:
                chunk.extend([0] * (CHUNK_SIZE - len(chunk)))
            chunks.append(chunk)
        return chunks

    def _compute_interval(self, jpeg_size: int) -> float:
        with self._params_lock:
            max_fps = self._max_fps
            min_fps = self._min_fps
            budget = self._link_budget
        tx_time = jpeg_size / budget if budget > 0 else 1.0
        interval = tx_time * 2.0
        return max(1.0 / max_fps, min(1.0 / min_fps, interval))
