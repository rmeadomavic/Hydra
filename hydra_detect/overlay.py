"""Draw bounding boxes, track IDs, and HUD info on frames."""

from __future__ import annotations

import time

import cv2
import numpy as np

from .tracker import TrackedObject, TrackingResult


# Colour palette (BGR) for up to 20 class IDs, then wraps
_PALETTE = [
    (0, 255, 255),   # yellow
    (0, 255, 0),     # green
    (255, 128, 0),   # blue-ish
    (0, 128, 255),   # orange
    (255, 0, 255),   # magenta
    (255, 255, 0),   # cyan
    (0, 0, 255),     # red
    (128, 255, 0),   # lime
    (255, 0, 128),   # pink
    (0, 255, 128),   # spring green
]

_TRACK_COLOUR = (0, 255, 0)    # green
_STRIKE_COLOUR = (0, 0, 255)   # red (BGR)


def draw_tracks(
    frame: np.ndarray,
    tracking: TrackingResult,
    inference_ms: float = 0.0,
    fps: float = 0.0,
    locked_track_id: int | None = None,
    lock_mode: str | None = None,
) -> np.ndarray:
    """Draw tracked detections and a HUD overlay on the frame (in-place).

    Args:
        locked_track_id: If set, highlight this track with a distinct marker.
        lock_mode: "track" for keep-in-frame, "strike" for strike approach.
    """
    h, w = frame.shape[:2]
    # Used for blink effect on strike mode (~3 Hz blink)
    blink_on = (int(time.monotonic() * 6) % 2) == 0

    for track in tracking:
        is_locked = (locked_track_id is not None and track.track_id == locked_track_id)
        colour = _PALETTE[track.class_id % len(_PALETTE)]
        x1, y1, x2, y2 = int(track.x1), int(track.y1), int(track.x2), int(track.y2)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

        if is_locked and lock_mode == "strike":
            # ── STRIKE MODE: blinking red box with X crosshair ──
            if blink_on:
                cv2.rectangle(frame, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), _STRIKE_COLOUR, 3)
            # Corner brackets (always visible)
            blen = max(10, min(x2 - x1, y2 - y1) // 4)
            for bx, by, dx, dy in [
                (x1, y1, 1, 1), (x2, y1, -1, 1),
                (x1, y2, 1, -1), (x2, y2, -1, -1),
            ]:
                cv2.line(frame, (bx, by), (bx + dx * blen, by), _STRIKE_COLOUR, 3)
                cv2.line(frame, (bx, by), (bx, by + dy * blen), _STRIKE_COLOUR, 3)
            # X crosshair at center (diagonal lines — large and bold)
            xlen = max(25, min(x2 - x1, y2 - y1) // 3)
            cv2.line(frame, (cx - xlen, cy - xlen), (cx + xlen, cy + xlen), _STRIKE_COLOUR, 3)
            cv2.line(frame, (cx - xlen, cy + xlen), (cx + xlen, cy - xlen), _STRIKE_COLOUR, 3)
            # Centroid dot
            cv2.circle(frame, (cx, cy), 5, _STRIKE_COLOUR, -1)
            # Mode label
            cv2.putText(
                frame, "STRIKE", (x1, y2 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, _STRIKE_COLOUR, 2, cv2.LINE_AA,
            )

        elif is_locked and lock_mode == "track":
            # ── TRACK MODE: solid green box with + crosshair ──
            cv2.rectangle(frame, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), _TRACK_COLOUR, 3)
            # Corner brackets
            blen = max(10, min(x2 - x1, y2 - y1) // 4)
            for bx, by, dx, dy in [
                (x1, y1, 1, 1), (x2, y1, -1, 1),
                (x1, y2, 1, -1), (x2, y2, -1, -1),
            ]:
                cv2.line(frame, (bx, by), (bx + dx * blen, by), _TRACK_COLOUR, 3)
                cv2.line(frame, (bx, by), (bx, by + dy * blen), _TRACK_COLOUR, 3)
            # + crosshair at center
            cv2.line(frame, (cx - 12, cy), (cx + 12, cy), _TRACK_COLOUR, 1)
            cv2.line(frame, (cx, cy - 12), (cx, cy + 12), _TRACK_COLOUR, 1)
            # Centroid dot
            cv2.circle(frame, (cx, cy), 3, _TRACK_COLOUR, -1)
            # Mode label
            cv2.putText(
                frame, "TRACKING", (x1, y2 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, _TRACK_COLOUR, 2, cv2.LINE_AA,
            )
        else:
            # ── Normal detection ──
            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
            # Centroid dot (small, so operator can always see what point
            # the system considers "center" for bearing calculations)
            cv2.circle(frame, (cx, cy), 3, colour, -1)

        # Label background
        text = f"#{track.track_id} {track.label} {track.confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), colour, -1)
        cv2.putText(
            frame, text, (x1 + 2, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
        )

    # HUD top-left
    hud_lines = [
        f"FPS: {fps:.1f}",
        f"Inference: {inference_ms:.1f} ms",
        f"Tracks: {len(tracking)}",
    ]
    if locked_track_id is not None:
        mode_str = "STRIKE" if lock_mode == "strike" else "TRACK"
        hud_lines.append(f"LOCKED: #{locked_track_id} [{mode_str}]")

    for i, line in enumerate(hud_lines):
        y = 24 + i * 22
        cv2.putText(
            frame, line, (10, y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA,
        )

    return frame
