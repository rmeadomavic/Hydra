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


def _draw_corner_brackets(
    frame: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    colour: tuple[int, int, int],
    thickness: int = 3,
) -> None:
    """Draw L-shaped corner brackets around a bounding box."""
    blen = max(10, min(x2 - x1, y2 - y1) // 4)
    for bx, by, dx, dy in [
        (x1, y1, 1, 1), (x2, y1, -1, 1),
        (x1, y2, 1, -1), (x2, y2, -1, -1),
    ]:
        cv2.line(frame, (bx, by), (bx + dx * blen, by), colour, thickness)
        cv2.line(frame, (bx, by), (bx, by + dy * blen), colour, thickness)


def _draw_single_track(
    frame: np.ndarray,
    track: TrackedObject,
    is_locked: bool,
    lock_mode: str | None,
    blink_on: bool,
) -> None:
    """Draw a single tracked object on the frame."""
    h, w = frame.shape[:2]
    colour = _PALETTE[track.class_id % len(_PALETTE)]
    # Clamp coordinates to frame bounds
    x1 = max(0, int(track.x1))
    y1 = max(0, int(track.y1))
    x2 = min(w - 1, int(track.x2))
    y2 = min(h - 1, int(track.y2))
    if x2 <= x1 or y2 <= y1:
        return  # Degenerate box — skip
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

    if is_locked and lock_mode == "strike":
        # ── STRIKE MODE: blinking red box with X crosshair ──
        if blink_on:
            cv2.rectangle(frame, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), _STRIKE_COLOUR, 3)
        _draw_corner_brackets(frame, x1, y1, x2, y2, _STRIKE_COLOUR)
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
        _draw_corner_brackets(frame, x1, y1, x2, y2, _TRACK_COLOUR)
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

    # Label background — clamp to frame bounds so labels near edges don't
    # produce negative coordinates or extend past the frame.
    text = f"#{track.track_id} {track.label} {track.confidence:.0%}"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    lx1 = max(0, x1)
    ly1 = max(0, y1 - th - 6)
    lx2 = min(w - 1, x1 + tw + 4)
    ly2 = max(0, y1)
    if lx2 > lx1 and ly2 > ly1:
        cv2.rectangle(frame, (lx1, ly1), (lx2, ly2), colour, -1)
    cv2.putText(
        frame, text, (max(0, x1 + 2), max(th + 2, y1 - 4)),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
    )


def draw_tracks(
    frame: np.ndarray,
    tracking: TrackingResult,
    inference_ms: float = 0.0,
    fps: float = 0.0,
    locked_track_id: int | None = None,
    lock_mode: str | None = None,
    alert_classes: set[str] | None = None,
) -> np.ndarray:
    """Draw tracked detections and a HUD overlay on the frame (in-place).

    Args:
        locked_track_id: If set, highlight this track with a distinct marker.
        lock_mode: "track" for keep-in-frame, "strike" for strike approach.
        alert_classes: If set, only tracks whose label is in this set render at
            full opacity; all other tracks are drawn at ~35% opacity. Locked
            tracks always render at full opacity regardless of this filter.
            Pass None to disable dimming (all tracks render at full opacity).
    """
    h, w = frame.shape[:2]
    # Used for blink effect on strike mode (~3 Hz blink)
    blink_on = (int(time.monotonic() * 6) % 2) == 0

    # Separate tracks into alert (full opacity) and dimmed
    alert_tracks = []
    dimmed_tracks = []
    for track in tracking:
        is_locked = (locked_track_id is not None and track.track_id == locked_track_id)
        if is_locked or alert_classes is None or track.label in alert_classes:
            alert_tracks.append(track)
        else:
            dimmed_tracks.append(track)

    # Pass 1: Draw dimmed tracks on overlay, blend at ~35% opacity
    if dimmed_tracks:
        overlay = frame.copy()
        for track in dimmed_tracks:
            _draw_single_track(overlay, track, False, None, blink_on)
        cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)

    # Pass 2: Draw alert tracks at full opacity
    for track in alert_tracks:
        is_locked = (locked_track_id is not None and track.track_id == locked_track_id)
        _draw_single_track(frame, track, is_locked, lock_mode, blink_on)

    # HUD top-left
    hud_lines = [
        f"FPS: {fps:.1f}",
        f"Inference: {inference_ms:.1f} ms",
        f"Tracks: {len(tracking)}",
    ]
    if locked_track_id is not None:
        mode_str = "STRIKE" if lock_mode == "strike" else "TRACK"
        hud_lines.append(f"LOCKED: #{locked_track_id} [{mode_str}]")

    # Semi-transparent dark backdrop behind HUD text
    if hud_lines:
        hud_h = len(hud_lines) * 22 + 12
        hud_w = 220
        overlay_roi = frame[2:2 + hud_h, 4:4 + hud_w]
        if overlay_roi.size > 0:
            cv2.addWeighted(overlay_roi, 0.5, np.zeros_like(overlay_roi), 0.5, 0, overlay_roi)

    for i, line in enumerate(hud_lines):
        y = 24 + i * 22
        cv2.putText(
            frame, line, (10, y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA,
        )
        cv2.putText(
            frame, line, (10, y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA,
        )

    return frame
