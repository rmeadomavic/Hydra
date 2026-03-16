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


# Keywords that identify webcams (preferred) and capture cards (fallback).
_WEBCAM_KEYWORDS = ("webcam", "c270", "c920", "c922", "brio", "lifecam")
_CAPTURE_KEYWORDS = (
    "usb video", "av to usb", "hdmi to usb", "capture", "uvc",
    "easycap", "macrosilicon", "elgato",
)
# Keywords that indicate non-capture V4L2 nodes (metadata, output, codecs).
_REJECT_KEYWORDS = ("metadata", "output", "codec", "decoder", "encoder")


def _classify_device(idx: int) -> str:
    """Classify a V4L2 device by its sysfs name.

    Returns:
        "webcam"  — known camera (preferred for auto-detect)
        "capture" — USB capture card / HDMI dongle (valid source)
        "unknown" — unrecognised device (may still work)
        "reject"  — metadata node, encoder, or output device
    """
    name = _get_device_name(idx).lower()
    if any(kw in name for kw in _REJECT_KEYWORDS):
        return "reject"
    if any(kw in name for kw in _WEBCAM_KEYWORDS):
        return "webcam"
    # "camera" is checked separately — it's common in webcam names but could
    # also appear in other contexts, so we check it after reject keywords.
    if "camera" in name:
        return "webcam"
    if any(kw in name for kw in _CAPTURE_KEYWORDS):
        return "capture"
    return "unknown"


def _is_capture_device(idx: int) -> bool:
    """Check if a /dev/video device is a usable video source.

    Returns True for webcams and USB capture cards (CVBS/HDMI dongles).
    Returns False for metadata nodes, encoders, and output devices.
    """
    return _classify_device(idx) in ("webcam", "capture")


def find_default_camera() -> int:
    """Find the best video device index, falling back to 0.

    Priority order:
    1. Known webcams (Logitech C270/C920, etc.)
    2. USB capture cards (CVBS/HDMI dongles for FPV feeds like HDZero)
    3. Any device that OpenCV can open and read a frame from
    4. Device index 0 as last resort
    """
    devices = sorted(glob.glob("/dev/video*"))
    webcams: list[int] = []
    captures: list[int] = []
    unknowns: list[int] = []

    for dev in devices:
        try:
            idx = int(dev.replace("/dev/video", ""))
        except ValueError:
            continue
        kind = _classify_device(idx)
        if kind == "webcam":
            webcams.append(idx)
        elif kind == "capture":
            captures.append(idx)
        elif kind == "unknown":
            unknowns.append(idx)

    # Prefer webcams, then capture cards
    for idx in webcams:
        logger.info("Auto-detected webcam: /dev/video%d (%s)", idx, _get_device_name(idx))
        return idx
    for idx in captures:
        logger.info("Auto-detected capture card: /dev/video%d (%s)", idx, _get_device_name(idx))
        return idx

    # Last resort: probe unknown devices with OpenCV
    for idx in unknowns:
        try:
            cap = cv2.VideoCapture(idx)
            if cap.isOpened():
                ok, _ = cap.read()
                cap.release()
                if ok:
                    logger.info(
                        "Auto-detected video source: /dev/video%d (%s)",
                        idx, _get_device_name(idx),
                    )
                    return idx
            else:
                cap.release()
        except Exception:
            pass

    logger.warning("No camera auto-detected, falling back to /dev/video0")
    return 0


def list_video_sources(current_source: int | str | None = None) -> list[dict]:
    """Enumerate available /dev/video* devices and identify them.

    Returns a list of dicts with 'index', 'device', 'name', and 'type' keys.
    Type is one of: "webcam", "capture", "unknown".
    Skips metadata/output nodes. Always includes ``current_source`` since
    it's locked by the pipeline and can't be test-opened.
    """
    sources: list[dict] = []
    seen: set[int] = set()
    devices = sorted(glob.glob("/dev/video*"))

    # Always include the current source first (it's busy, can't be test-opened)
    if current_source is not None:
        try:
            cur_idx = int(current_source)
            kind = _classify_device(cur_idx)
            if kind == "reject":
                kind = "unknown"
            sources.append({
                "index": cur_idx,
                "device": f"/dev/video{cur_idx}",
                "name": _get_device_name(cur_idx),
                "type": kind,
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
        kind = _classify_device(idx)
        if kind == "reject":
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
        sources.append({
            "index": idx,
            "device": dev,
            "name": _get_device_name(idx),
            "type": kind,
        })
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
