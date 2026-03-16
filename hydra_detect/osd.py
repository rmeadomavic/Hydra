"""FPV OSD overlay — sends detection data to an OSD renderer.

Supports three modes:
- 'statustext': Sends formatted STATUSTEXT messages shown in the OSD message
  panel. Works on any ArduPilot FC with OSD. No Lua script required.
- 'named_value': Sends structured NAMED_VALUE_FLOAT/INT messages for a Lua
  script on the FC to decode and render at specific OSD positions. Richer
  display but requires the companion Lua script on the FC SD card.
- 'msp_displayport': Speaks MSP v1 DisplayPort protocol over a dedicated
  serial UART directly to an HDZero VTX, bypassing the flight controller.
  Draws detection telemetry on the HD OSD canvas (50×18 by default).

Compatible FC boards (with onboard OSD chip — statustext/named_value modes):
- Matek H743 (AT7456E)
- SpeedyBee F405-Wing (AT7456E)
- Any ArduPilot FC with MAX7456/AT7456E OSD

NOTE: Pixhawk 6C does not have an onboard OSD chip. Use msp_displayport mode
for direct VTX OSD, or the web dashboard overlay for Pixhawk platforms.
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass

from .msp_displayport import MspDisplayPort, MspOsdData
from .tracker import TrackingResult

logger = logging.getLogger(__name__)


@dataclass
class OSDState:
    """Snapshot of data to render on the FPV OSD."""

    fps: float = 0.0
    inference_ms: float = 0.0
    active_tracks: int = 0
    locked_track_id: int | None = None
    lock_mode: str | None = None
    locked_label: str = ""
    gps_fix: int = 0
    gps_lat: float | None = None
    gps_lon: float | None = None
    latest_det_label: str = ""
    latest_det_conf: float = 0.0


# Max length for NAMED_VALUE_FLOAT name field in MAVLink
_NV_NAME_MAX = 10


class FpvOsd:
    """Formats and sends OSD data to an OSD renderer.

    For ``statustext`` and ``named_value`` modes this class borrows an
    existing MAVLink connection.  For ``msp_displayport`` mode it owns a
    dedicated serial link to the VTX and runs a background thread.

    Args:
        mavlink_io: MAVLinkIO instance (used by statustext/named_value modes).
        mode: OSD mode — ``statustext``, ``named_value``, or ``msp_displayport``.
        update_interval: Minimum seconds between OSD updates.
        serial_port: Serial device for MSP DisplayPort (only used in that mode).
        serial_baud: Baud rate for MSP serial (only used in msp_displayport mode).
        canvas_rows: OSD canvas rows (only used in msp_displayport mode).
        canvas_cols: OSD canvas columns (only used in msp_displayport mode).
    """

    _VALID_MODES = ("statustext", "named_value", "msp_displayport")

    def __init__(
        self,
        mavlink_io,  # hydra_detect.mavlink_io.MAVLinkIO instance
        *,
        mode: str = "statustext",
        update_interval: float = 0.2,
        serial_port: str = "/dev/ttyUSB0",
        serial_baud: int = 115200,
        canvas_rows: int = 18,
        canvas_cols: int = 50,
    ):
        if mode not in self._VALID_MODES:
            logger.warning(
                "Invalid OSD mode '%s', falling back to 'statustext'. "
                "Valid modes: %s", mode, ", ".join(self._VALID_MODES),
            )
            mode = "statustext"
        self._mav = mavlink_io
        self._mode = mode
        self._interval = max(0.05, update_interval)
        self._last_send: float = 0.0
        self._lock = threading.Lock()

        # MSP DisplayPort driver (lazy-started on first update)
        self._msp: MspDisplayPort | None = None
        if mode == "msp_displayport":
            self._msp = MspDisplayPort(
                serial_port=serial_port,
                serial_baud=serial_baud,
                canvas_rows=canvas_rows,
                canvas_cols=canvas_cols,
                update_interval=update_interval,
            )
            self._msp.start()

    @property
    def mode(self) -> str:
        return self._mode

    def update(self, state: OSDState) -> None:
        """Send OSD data if enough time has elapsed since the last send."""
        now = time.monotonic()
        with self._lock:
            if (now - self._last_send) < self._interval:
                return
            self._last_send = now

        if self._mode == "msp_displayport":
            self._send_msp_displayport(state)
        elif self._mode == "named_value":
            self._send_named_values(state)
        else:
            self._send_statustext(state)

    # ------------------------------------------------------------------
    # STATUSTEXT mode — simple, works on any FC with OSD message panel
    # ------------------------------------------------------------------
    def _send_statustext(self, state: OSDState) -> None:
        """Format and send a single STATUSTEXT with key OSD info."""
        parts: list[str] = []

        parts.append(f"T:{state.active_tracks}")
        parts.append(f"{state.fps:.0f}fps")
        parts.append(f"{state.inference_ms:.0f}ms")

        if state.locked_track_id is not None:
            mode_char = "S" if state.lock_mode == "strike" else "T"
            label = state.locked_label[:8] if state.locked_label else "?"
            parts.append(f"LK#{state.locked_track_id}{mode_char}:{label}")

        # STATUSTEXT is limited to 50 chars
        msg = " ".join(parts)
        self._mav.send_statustext(msg[:50], severity=6)  # 6 = INFO

    # ------------------------------------------------------------------
    # NAMED_VALUE mode — structured data for Lua script on FC
    # ------------------------------------------------------------------
    def _send_named_values(self, state: OSDState) -> None:
        """Send individual NAMED_VALUE_FLOAT messages for Lua to decode."""
        self._send_named_float("osd_fps", state.fps)
        self._send_named_float("osd_infms", state.inference_ms)
        self._send_named_int("osd_trks", state.active_tracks)

        if state.locked_track_id is not None:
            self._send_named_int("osd_lkid", state.locked_track_id)
            # Encode lock mode as int: 1=track, 2=strike
            mode_val = 2 if state.lock_mode == "strike" else 1
            self._send_named_int("osd_lkmod", mode_val)
        else:
            self._send_named_int("osd_lkid", -1)

        self._send_named_int("osd_gfix", state.gps_fix)

    def _send_named_float(self, name: str, value: float) -> None:
        """Send a NAMED_VALUE_FLOAT via the MAVLink connection."""
        if self._mav._mav is None:
            return
        try:
            # Truncate name to 10 bytes (MAVLink spec)
            name_bytes = name[:_NV_NAME_MAX].encode("utf-8")[:_NV_NAME_MAX]
            with self._mav._lock:
                self._mav._mav.mav.named_value_float_send(
                    int(time.monotonic() * 1000) & 0xFFFFFFFF,
                    name_bytes,
                    value,
                )
        except Exception as exc:
            logger.debug("OSD named_value_float send failed: %s", exc)

    def _send_named_int(self, name: str, value: int) -> None:
        """Send a NAMED_VALUE_INT via the MAVLink connection."""
        if self._mav._mav is None:
            return
        try:
            name_bytes = name[:_NV_NAME_MAX].encode("utf-8")[:_NV_NAME_MAX]
            with self._mav._lock:
                self._mav._mav.mav.named_value_int_send(
                    int(time.monotonic() * 1000) & 0xFFFFFFFF,
                    name_bytes,
                    value,
                )
        except Exception as exc:
            logger.debug("OSD named_value_int send failed: %s", exc)

    # ------------------------------------------------------------------
    # MSP DisplayPort mode — direct serial to HDZero VTX
    # ------------------------------------------------------------------
    def _send_msp_displayport(self, state: OSDState) -> None:
        """Forward OSD state to the MSP DisplayPort driver thread."""
        if self._msp is None:
            return
        self._msp.update(MspOsdData(
            fps=state.fps,
            inference_ms=state.inference_ms,
            active_tracks=state.active_tracks,
            locked_track_id=state.locked_track_id,
            lock_mode=state.lock_mode,
            locked_label=state.locked_label,
            gps_lat=state.gps_lat,
            gps_lon=state.gps_lon,
            latest_det_label=state.latest_det_label,
            latest_det_conf=state.latest_det_conf,
        ))


def build_osd_state(
    track_result: TrackingResult,
    fps: float,
    inference_ms: float,
    locked_track_id: int | None,
    lock_mode: str | None,
    gps: dict | None,
) -> OSDState:
    """Build an OSDState snapshot from current pipeline data."""
    # Extract GPS lat/lon (MAVLink stores as int × 1e7)
    gps_lat: float | None = None
    gps_lon: float | None = None
    if gps and gps.get("lat") is not None and gps.get("lon") is not None:
        gps_lat = gps["lat"] / 1e7
        gps_lon = gps["lon"] / 1e7

    # Pick the highest-confidence detection for OSD display
    latest_label = ""
    latest_conf = 0.0
    for t in track_result:
        if t.confidence > latest_conf:
            latest_conf = t.confidence
            latest_label = t.label

    state = OSDState(
        fps=fps,
        inference_ms=inference_ms,
        active_tracks=len(track_result),
        locked_track_id=locked_track_id,
        lock_mode=lock_mode,
        gps_fix=gps.get("fix", 0) if gps else 0,
        gps_lat=gps_lat,
        gps_lon=gps_lon,
        latest_det_label=latest_label,
        latest_det_conf=latest_conf,
    )

    # Resolve locked target label
    if locked_track_id is not None:
        for t in track_result:
            if t.track_id == locked_track_id:
                state.locked_label = t.label
                break

    return state
