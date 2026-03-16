"""MSP v1 DisplayPort OSD driver for HDZero VTX.

Speaks the MSP DisplayPort protocol over a dedicated serial UART to draw
detection telemetry directly on the HDZero VTX's OSD canvas, bypassing the
flight controller entirely.

Protocol reference:
- MSP v1 frame: ``$M<`` + payload_size (1 B) + command_id (1 B) + payload + XOR checksum
- MSP command 182 (``MSP_DISPLAYPORT``) sub-commands:
    0 = Heartbeat  — keeps VTX OSD alive
    2 = Clear      — clears the canvas
    3 = Write      — writes a string at (row, col) with attribute byte
    4 = Draw       — commits buffered writes to the display
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from dataclasses import dataclass

import serial

logger = logging.getLogger(__name__)

# MSP v1 constants
_MSP_HEADER = b"$M<"
_MSP_CMD_DISPLAYPORT = 182

# DisplayPort sub-commands
_DP_SUB_HEARTBEAT = 0
_DP_SUB_CLEAR = 2
_DP_SUB_WRITE = 3
_DP_SUB_DRAW = 4

# Default HD canvas size (HDZero / Walksnail HD)
DEFAULT_CANVAS_COLS = 50
DEFAULT_CANVAS_ROWS = 18


def _msp_frame(command: int, payload: bytes | bytearray) -> bytes:
    """Build an MSP v1 frame: ``$M<`` + size + cmd + payload + XOR checksum.

    Args:
        command: MSP command ID (e.g. 182 for DisplayPort).
        payload: Raw payload bytes.

    Returns:
        Complete MSP v1 frame ready for serial transmission.
    """
    size = len(payload)
    # Checksum is XOR of size, command, and every payload byte
    checksum = size ^ command
    for b in payload:
        checksum ^= b
    checksum &= 0xFF
    header = _MSP_HEADER + struct.pack("BB", size, command)
    return header + bytes(payload) + struct.pack("B", checksum)


def heartbeat_frame(rows: int = DEFAULT_CANVAS_ROWS, cols: int = DEFAULT_CANVAS_COLS) -> bytes:
    """Build an MSP DisplayPort heartbeat frame.

    The heartbeat must be sent every OSD cycle to keep the VTX OSD alive.

    Args:
        rows: Canvas row count.
        cols: Canvas column count.

    Returns:
        Complete MSP v1 frame with heartbeat sub-command.
    """
    payload = bytearray([_DP_SUB_HEARTBEAT, rows, cols, 0, 0])
    return _msp_frame(_MSP_CMD_DISPLAYPORT, payload)


def clear_frame() -> bytes:
    """Build an MSP DisplayPort clear-screen frame.

    Returns:
        Complete MSP v1 frame with clear sub-command.
    """
    return _msp_frame(_MSP_CMD_DISPLAYPORT, bytearray([_DP_SUB_CLEAR]))


def write_string_frame(row: int, col: int, text: str, attr: int = 0) -> bytes:
    """Build an MSP DisplayPort write-string frame.

    Args:
        row: Row position (0-based).
        col: Column position (0-based).
        text: ASCII string to write.
        attr: Display attribute byte (0 = normal).

    Returns:
        Complete MSP v1 frame with write sub-command.
    """
    ascii_bytes = text.encode("ascii", errors="replace")
    payload = bytearray([_DP_SUB_WRITE, row, col, attr]) + ascii_bytes
    return _msp_frame(_MSP_CMD_DISPLAYPORT, payload)


def draw_frame() -> bytes:
    """Build an MSP DisplayPort draw (commit) frame.

    Returns:
        Complete MSP v1 frame with draw sub-command.
    """
    return _msp_frame(_MSP_CMD_DISPLAYPORT, bytearray([_DP_SUB_DRAW]))


@dataclass
class MspOsdData:
    """Thread-safe snapshot of data to render on the MSP OSD.

    This is populated by the OSD update path and consumed by the MSP
    serial thread.
    """

    fps: float = 0.0
    inference_ms: float = 0.0
    active_tracks: int = 0
    locked_track_id: int | None = None
    lock_mode: str | None = None
    locked_label: str = ""
    gps_lat: float | None = None
    gps_lon: float | None = None
    latest_det_label: str = ""
    latest_det_conf: float = 0.0


class MspDisplayPort:
    """MSP DisplayPort OSD driver running in a dedicated daemon thread.

    Opens a serial connection to the HDZero VTX and continuously renders
    detection telemetry on the HD OSD canvas. Handles serial disconnects
    gracefully with automatic reconnection.

    Args:
        serial_port: Path to the serial device (e.g. ``/dev/ttyUSB0``).
        serial_baud: Baud rate for the serial connection.
        canvas_rows: Number of OSD canvas rows.
        canvas_cols: Number of OSD canvas columns.
        update_interval: Minimum seconds between OSD frame cycles.
    """

    def __init__(
        self,
        serial_port: str = "/dev/ttyUSB0",
        serial_baud: int = 115200,
        canvas_rows: int = DEFAULT_CANVAS_ROWS,
        canvas_cols: int = DEFAULT_CANVAS_COLS,
        update_interval: float = 0.1,
    ):
        self._port = serial_port
        self._baud = serial_baud
        self._rows = canvas_rows
        self._cols = canvas_cols
        self._interval = max(0.05, update_interval)

        self._lock = threading.Lock()
        self._data = MspOsdData()
        self._running = False
        self._thread: threading.Thread | None = None
        self._ser: serial.Serial | None = None

    def start(self) -> None:
        """Start the background serial thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="msp-displayport",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "MSP DisplayPort thread started: port=%s baud=%d canvas=%dx%d interval=%.2fs",
            self._port, self._baud, self._cols, self._rows, self._interval,
        )

    def stop(self) -> None:
        """Stop the background serial thread and close the port."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._close_serial()

    def update(self, data: MspOsdData) -> None:
        """Update the OSD data snapshot (called from the pipeline thread).

        Args:
            data: New telemetry snapshot to render on next OSD cycle.
        """
        with self._lock:
            self._data = data

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open_serial(self) -> bool:
        """Attempt to open the serial port. Returns True on success."""
        try:
            self._ser = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                timeout=1.0,
                write_timeout=1.0,
            )
            logger.info("MSP serial port opened: %s @ %d", self._port, self._baud)
            return True
        except (serial.SerialException, OSError) as exc:
            logger.warning("MSP serial open failed (%s): %s", self._port, exc)
            self._ser = None
            return False

    def _close_serial(self) -> None:
        """Close the serial port if open."""
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def _write_bytes(self, data: bytes) -> bool:
        """Write bytes to serial, returning False on failure."""
        if self._ser is None:
            return False
        try:
            self._ser.write(data)
            return True
        except (serial.SerialException, OSError) as exc:
            logger.warning("MSP serial write failed: %s", exc)
            self._close_serial()
            return False

    def _format_status_line(self, data: MspOsdData) -> str:
        """Format the top status line (row 0).

        Format: ``T:<tracks> <fps>fps <ms>ms LK#<id><mode>``
        """
        parts: list[str] = []
        parts.append(f"T:{data.active_tracks}")
        parts.append(f"{data.fps:.0f}fps")
        parts.append(f"{data.inference_ms:.0f}ms")

        if data.locked_track_id is not None:
            mode_tag = "STK" if data.lock_mode == "strike" else "TRK"
            parts.append(f"LK#{data.locked_track_id}{mode_tag}")

        line = " ".join(parts)
        return line[:self._cols]

    def _format_gps_line(self, data: MspOsdData) -> str:
        """Format the GPS position string for row 17, left side.

        Shows decimal lat/lon by default.  If the ``mgrs`` module is
        available the MGRS grid reference is appended after the coordinates,
        space permitting.
        """
        if data.gps_lat is None or data.gps_lon is None:
            return "NO GPS"
        latlon = f"{data.gps_lat:.5f},{data.gps_lon:.5f}"
        try:
            import mgrs
            m = mgrs.MGRS()
            grid = m.toMGRS(data.gps_lat, data.gps_lon)
            combined = f"{latlon} {grid}"
            return combined[:self._cols]
        except Exception:
            return latlon

    def _format_det_line(self, data: MspOsdData) -> str:
        """Format the most recent detection for row 17, right-aligned."""
        if not data.latest_det_label:
            return ""
        return f"{data.latest_det_label} {data.latest_det_conf:.2f}"

    def _render_frame(self, data: MspOsdData) -> None:
        """Send one complete OSD frame cycle: heartbeat → clear → writes → draw."""
        # Heartbeat — MUST be sent every frame
        if not self._write_bytes(heartbeat_frame(self._rows, self._cols)):
            return

        # Clear screen
        if not self._write_bytes(clear_frame()):
            return

        # Row 0: status line (left-aligned)
        status = self._format_status_line(data)
        if status:
            if not self._write_bytes(write_string_frame(0, 0, status)):
                return

        # Row 17: GPS position (left-aligned)
        gps_text = self._format_gps_line(data)
        if gps_text:
            if not self._write_bytes(write_string_frame(self._rows - 1, 0, gps_text)):
                return

        # Row 17: latest detection (right-aligned)
        det_text = self._format_det_line(data)
        if det_text:
            col = max(0, self._cols - len(det_text))
            if not self._write_bytes(write_string_frame(self._rows - 1, col, det_text)):
                return

        # Draw — commit buffered writes
        self._write_bytes(draw_frame())

    def _run(self) -> None:
        """Background thread main loop with reconnection logic."""
        while self._running:
            # Ensure serial is open
            if self._ser is None:
                if not self._open_serial():
                    # Retry after 2 seconds on connection failure
                    for _ in range(20):  # 20 × 0.1s = 2s, checking _running
                        if not self._running:
                            return
                        time.sleep(0.1)
                    continue

            # Snapshot current data
            with self._lock:
                data = self._data

            # Render one OSD frame
            self._render_frame(data)

            # Sleep for the configured interval
            time.sleep(self._interval)

        # Clean up on exit
        self._close_serial()
