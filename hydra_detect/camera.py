"""Camera abstraction — unified interface for USB, RTSP, and file sources."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class Camera:
    """Thread-safe camera capture with automatic reconnection."""

    def __init__(
        self,
        source: str | int = 0,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ):
        self._source = int(source) if str(source).isdigit() else source
        self._width = width
        self._height = height
        self._fps = fps

        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def open(self) -> bool:
        """Open the capture device and start the grab thread."""
        self._cap = cv2.VideoCapture(self._source)
        if not self._cap.isOpened():
            logger.error("Cannot open camera source: %s", self._source)
            return False

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        self._running = True
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()
        logger.info(
            "Camera opened: %s (%dx%d @ %d fps)",
            self._source,
            self._width,
            self._height,
            self._fps,
        )
        return True

    def close(self) -> None:
        """Stop capture and release resources."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._cap is not None:
            self._cap.release()
        logger.info("Camera closed.")

    # ------------------------------------------------------------------
    def read(self) -> Optional[np.ndarray]:
        """Return the latest frame (thread-safe copy)."""
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    # ------------------------------------------------------------------
    def _grab_loop(self) -> None:
        """Continuously grab frames in background thread."""
        backoff = 1.0
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                logger.warning("Reconnecting camera in %.1fs ...", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                self._cap = cv2.VideoCapture(self._source)
                continue

            ok, frame = self._cap.read()
            if not ok:
                logger.warning("Frame grab failed, will reconnect.")
                self._cap.release()
                continue

            backoff = 1.0
            with self._lock:
                self._frame = frame

    # -- Public accessors -------------------------------------------------
    @property
    def width(self) -> int:
        return self._width

    @property
    def has_frame(self) -> bool:
        with self._lock:
            return self._frame is not None

    # ------------------------------------------------------------------
    def __enter__(self) -> "Camera":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
