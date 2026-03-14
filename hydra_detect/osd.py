"""FPV OSD overlay via MAVLink — sends detection data to FC onboard OSD chip.

Supports two modes:
- 'statustext': Sends formatted STATUSTEXT messages shown in the OSD message
  panel. Works on any ArduPilot FC with OSD. No Lua script required.
- 'named_value': Sends structured NAMED_VALUE_FLOAT/INT messages for a Lua
  script on the FC to decode and render at specific OSD positions. Richer
  display but requires the companion Lua script on the FC SD card.

Compatible FC boards (with onboard OSD chip):
- Matek H743 (AT7456E)
- SpeedyBee F405-Wing (AT7456E)
- Any ArduPilot FC with MAX7456/AT7456E OSD

NOTE: Pixhawk 6C does not have an onboard OSD chip. Use the web dashboard
overlay for Pixhawk platforms instead.
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass

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


# Max length for NAMED_VALUE_FLOAT name field in MAVLink
_NV_NAME_MAX = 10


class FpvOsd:
    """Formats and sends OSD data to the FC over an existing MAVLink link.

    This class does NOT own the MAVLink connection — it borrows a reference
    to send OSD-specific messages alongside normal telemetry.
    """

    _VALID_MODES = ("statustext", "named_value")

    def __init__(
        self,
        mavlink_io,  # hydra_detect.mavlink_io.MAVLinkIO instance
        *,
        mode: str = "statustext",
        update_interval: float = 0.2,
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

        if self._mode == "named_value":
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


def build_osd_state(
    track_result: TrackingResult,
    fps: float,
    inference_ms: float,
    locked_track_id: int | None,
    lock_mode: str | None,
    gps: dict | None,
) -> OSDState:
    """Build an OSDState snapshot from current pipeline data."""
    state = OSDState(
        fps=fps,
        inference_ms=inference_ms,
        active_tracks=len(track_result),
        locked_track_id=locked_track_id,
        lock_mode=lock_mode,
        gps_fix=gps.get("fix", 0) if gps else 0,
    )

    # Resolve locked target label
    if locked_track_id is not None:
        for t in track_result:
            if t.track_id == locked_track_id:
                state.locked_label = t.label
                break

    return state
