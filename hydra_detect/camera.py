"""Camera abstraction — unified interface for USB, RTSP, and file sources."""

from __future__ import annotations

import glob
import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _get_device_name(idx: int) -> str:
    """Read the V4L2 device name from sysfs."""
    try:
        with open(f"/sys/class/video4linux/video{idx}/name", "r") as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError):
        return f"Video {idx}"


def _is_capture_device(idx: int) -> bool:
    """Check if a /dev/video device supports video capture (not metadata/output).

    Reads the V4L2 device_caps from sysfs. Bit 0 of device_caps indicates
    VIDEO_CAPTURE capability. On Jetson, /dev/video0 and /dev/video1 are
    often HDMI output or metadata nodes, not real cameras.
    """
    try:
        caps_path = f"/sys/class/video4linux/video{idx}/device/video4linux/video{idx}/dev"
        # More reliable: check if the device name looks like a real camera
        name = _get_device_name(idx).lower()
        # Filter out metadata companion devices (odd-numbered V4L2 nodes)
        # and devices with generic "USB Video" names that are often HDMI capture
        if any(kw in name for kw in ("webcam", "camera", "cam", "c270", "c920", "c922", "brio")):
            return True
        return False
    except Exception:
        return False


def find_default_camera() -> int:
    """Find the first real webcam device index, falling back to 0.

    On Jetson boards, /dev/video0 is often an HDMI capture/output device.
    This function prefers devices whose V4L2 name contains common camera
    keywords (webcam, camera, c270, etc.) over generic "USB Video" devices.
    """
    devices = sorted(glob.glob("/dev/video*"))
    # First pass: look for known webcam names
    for dev in devices:
        try:
            idx = int(dev.replace("/dev/video", ""))
        except ValueError:
            continue
        if _is_capture_device(idx):
            logger.info("Auto-detected webcam: /dev/video%d (%s)", idx, _get_device_name(idx))
            return idx
    # Fallback to device 0
    return 0


def list_video_sources(current_source: int | str | None = None) -> list[dict]:
    """Enumerate available /dev/video* devices and identify them.

    Returns a list of dicts with 'index', 'device', and 'name' keys.
    Skips devices that can't be opened or read (metadata nodes, etc.),
    but always includes ``current_source`` since it's locked by the pipeline.
    """
    sources: list[dict] = []
    seen: set[int] = set()
    devices = sorted(glob.glob("/dev/video*"))

    # Always include the current source first (it's busy, can't be test-opened)
    if current_source is not None:
        try:
            cur_idx = int(current_source)
            sources.append({
                "index": cur_idx,
                "device": f"/dev/video{cur_idx}",
                "name": _get_device_name(cur_idx),
            })
            seen.add(cur_idx)
        except (TypeError, ValueError):
            pass  # RTSP/GStreamer source — not a /dev/video device

    for dev in devices:
        try:
            idx = int(dev.replace("/dev/video", ""))
        except ValueError:
            continue
        if idx in seen:
            continue
        # Try opening briefly to check if it's a real capture device
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        # Read one frame to verify it actually produces output
        ok, _ = cap.read()
        cap.release()
        if not ok:
            continue
        sources.append({"index": idx, "device": dev, "name": _get_device_name(idx)})
        seen.add(idx)
    return sources


class Camera:
    """Thread-safe camera capture with automatic reconnection."""

    def __init__(
        self,
        source: str | int = "auto",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ):
        if str(source).lower() == "auto":
            self._source = find_default_camera()
        else:
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

        self._configure_and_start()
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
            # Timeout must exceed max backoff (30s) to avoid racing with the grab thread
            self._thread.join(timeout=35.0)
        if self._cap is not None:
            self._cap.release()
        logger.info("Camera closed.")

    def _configure_and_start(self) -> None:
        """Apply capture properties and start the grab thread."""
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)
        self._running = True
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

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

    def switch_source(self, new_source: str | int) -> bool:
        """Switch to a different camera source at runtime.

        Stops the current capture, opens the new source, and restarts.
        Returns True on success, False if the new source can't be opened
        (in which case the old source is restored).
        """
        new_source = int(new_source) if str(new_source).isdigit() else new_source
        old_source = self._source
        logger.info("Switching camera: %s -> %s", old_source, new_source)

        # Stop current capture
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._cap is not None:
            self._cap.release()

        # Try new source
        self._source = new_source
        self._cap = cv2.VideoCapture(self._source)
        if not self._cap.isOpened():
            logger.error("Cannot open new camera source: %s — reverting to %s",
                         new_source, old_source)
            self._source = old_source
            self._cap = cv2.VideoCapture(self._source)
            if not self._cap.isOpened():
                logger.error("Cannot reopen original source either!")
                return False
            self._configure_and_start()
            return False

        with self._lock:
            self._frame = None  # Clear stale frame from old source
        self._configure_and_start()
        logger.info("Camera switched to: %s", new_source)
        return True

    # -- Public accessors -------------------------------------------------
    @property
    def source(self) -> str | int:
        return self._source

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
