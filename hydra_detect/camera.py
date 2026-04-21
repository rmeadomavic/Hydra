"""Camera abstraction — unified interface for USB, RTSP, file, and analog sources."""

from __future__ import annotations

import glob
import logging
import shutil
import subprocess
import threading
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
        except Exception as exc:
            logger.debug("Failed to probe /dev/video%d: %s", idx, exc)

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


# V4L2 video standard constants (from linux/videodev2.h)
_V4L2_STD_NTSC: int = 0x0000B000
_V4L2_STD_PAL: int = 0x000000FF


def _have_v4l2ctl() -> bool:
    """Return True if v4l2-ctl is available on PATH."""
    return shutil.which("v4l2-ctl") is not None


def _run_v4l2ctl(device: str, *args: str) -> str | None:
    """Run a v4l2-ctl command, returning stdout or None on failure."""
    cmd = ["v4l2-ctl", "-d", device, *args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        logger.warning("v4l2-ctl %s failed (rc=%d): %s",
                       " ".join(args), result.returncode, result.stderr.strip())
    except FileNotFoundError:
        logger.warning("v4l2-ctl not found — install v4l-utils for full analog support")
    except subprocess.TimeoutExpired:
        logger.warning("v4l2-ctl %s timed out", " ".join(args))
    except OSError as exc:
        logger.warning("v4l2-ctl error: %s", exc)
    return None


def _configure_analog_input(device: str, video_standard: str) -> None:
    """Configure a V4L2 capture device for composite (CVBS) input.

    Attempts to set the composite input and video standard via v4l2-ctl.
    Logs warnings and continues gracefully if commands fail.

    ``video_standard`` accepts: ``"ntsc"``, ``"pal"``, or ``"auto"`` (relies on
    the dongle's own auto-detection — skips the ``--set-standard`` call).
    """
    if not _have_v4l2ctl():
        logger.warning(
            "v4l2-ctl not installed — skipping analog input configuration. "
            "Install with: sudo apt install v4l-utils"
        )
        return

    # List available inputs for diagnostics
    inputs_output = _run_v4l2ctl(device, "--list-inputs")
    if inputs_output:
        logger.info("V4L2 inputs for %s:\n%s", device, inputs_output.rstrip())

    # Set composite input (typically input 0)
    result = _run_v4l2ctl(device, "--set-input=0")
    if result is not None:
        logger.info("Set %s to composite input 0", device)

    # Set video standard
    std = video_standard.lower()
    if std == "ntsc":
        _run_v4l2ctl(device, f"--set-standard={_V4L2_STD_NTSC}")
        logger.info("Set %s video standard to NTSC", device)
    elif std == "pal":
        _run_v4l2ctl(device, f"--set-standard={_V4L2_STD_PAL}")
        logger.info("Set %s video standard to PAL", device)
    elif std == "auto":
        logger.info("Video standard set to auto — relying on dongle auto-detection")
    else:
        logger.warning("Unknown video_standard '%s', skipping", video_standard)


class Camera:
    """Thread-safe camera capture with automatic reconnection."""

    def __init__(
        self,
        source: str | int = "auto",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        source_type: str = "auto",
        video_standard: str = "ntsc",
    ):
        self._source_type = source_type.lower()
        self._video_standard = video_standard.lower()

        if self._source_type in ("auto", "digital"):
            # Existing behaviour — auto-detect or use explicit source
            if str(source).lower() == "auto":
                self._source = find_default_camera()
            else:
                self._source = int(source) if str(source).isdigit() else source
        elif self._source_type == "analog":
            # Analog: source must be a device index or /dev/videoX path
            if str(source).lower() == "auto":
                self._source = find_default_camera()
            else:
                self._source = int(source) if str(source).isdigit() else source
        else:
            logger.warning("Unknown source_type '%s', treating as auto", source_type)
            self._source_type = "auto"
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
        # Signalled by close() to interrupt the reconnect-backoff sleep —
        # otherwise shutdown blocks for up to 30 s during reconnect.
        self._stop_evt = threading.Event()

    # ------------------------------------------------------------------
    def _device_path(self) -> str:
        """Return the /dev/videoX path for the current source."""
        if isinstance(self._source, int):
            return f"/dev/video{self._source}"
        return str(self._source)

    def open(self) -> bool:
        """Open the capture device and start the grab thread.

        If the device isn't available yet (e.g. unplugged at boot), the grab
        thread is still started so it can reconnect in the background with
        exponential backoff. Frames start flowing once the device appears.
        Always returns True — a missing camera is a degraded runtime state,
        not a hard failure (see issue #122).
        """
        if self._source_type == "analog":
            # Configure V4L2 composite input before opening
            _configure_analog_input(self._device_path(), self._video_standard)
            # Force V4L2 backend for analog capture dongles
            self._cap = cv2.VideoCapture(self._source, cv2.CAP_V4L2)
        else:
            self._cap = cv2.VideoCapture(self._source)

        if not self._cap.isOpened():
            logger.warning(
                "Camera not found at %s — will keep retrying in the background. "
                "Check USB connection, device path, or camera.source in config.ini.",
                self._source,
            )
            self._cap.release()
            self._cap = None
            # Start grab thread anyway — it reconnects with backoff.
            self._running = True
            self._thread = threading.Thread(target=self._grab_loop, daemon=True)
            self._thread.start()
            return True

        self._configure_and_start()

        # Log actual resolution after first configuration
        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if self._source_type == "analog":
            std_label = self._video_standard.upper()
            logger.info(
                "Analog camera opened: %s (%dx%d @ %d fps, standard=%s)",
                self._source, actual_w, actual_h, self._fps, std_label,
            )
        else:
            logger.info(
                "Camera opened: %s (%dx%d @ %d fps)",
                self._source, actual_w, actual_h, self._fps,
            )
        return True

    def close(self) -> None:
        """Stop capture and release resources."""
        self._running = False
        # Wake the grab thread if it's sleeping inside the reconnect backoff.
        self._stop_evt.set()
        if self._thread is not None:
            # Short timeout is now sufficient — the stop event interrupts
            # backoff sleeps immediately.
            self._thread.join(timeout=5.0)
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
                # Use Event.wait so close() can interrupt the backoff sleep.
                if self._stop_evt.wait(timeout=backoff):
                    break
                backoff = min(backoff * 2, 30.0)
                if self._cap is not None:
                    # Release any lingering (unopened) capture before replacing
                    self._cap.release()
                if self._source_type == "analog":
                    _configure_analog_input(
                        self._device_path(), self._video_standard,
                    )
                    self._cap = cv2.VideoCapture(self._source, cv2.CAP_V4L2)
                else:
                    self._cap = cv2.VideoCapture(self._source)
                if self._cap.isOpened():
                    # Apply resolution/fps after reconnect — otherwise camera
                    # runs at default settings after a reconnect.
                    self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
                    self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
                    self._cap.set(cv2.CAP_PROP_FPS, self._fps)
                continue

            ok, frame = self._cap.read()
            if not ok:
                logger.warning("Frame grab failed, will reconnect.")
                self._cap.release()
                self._cap = None
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
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._cap is not None:
            self._cap.release()
        # Reset the stop event so the new grab thread can sleep in backoff.
        self._stop_evt.clear()

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
    def source_type(self) -> str:
        return self._source_type

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
