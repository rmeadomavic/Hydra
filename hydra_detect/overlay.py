"""Draw bounding boxes, track IDs, and HUD info on frames."""

from __future__ import annotations

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


def draw_tracks(
    frame: np.ndarray,
    tracking: TrackingResult,
    inference_ms: float = 0.0,
    fps: float = 0.0,
) -> np.ndarray:
    """Draw tracked detections and a HUD overlay on the frame (in-place)."""
    for track in tracking:
        colour = _PALETTE[track.class_id % len(_PALETTE)]
        x1, y1, x2, y2 = int(track.x1), int(track.y1), int(track.x2), int(track.y2)

        # Bounding box
        cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)

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
    for i, line in enumerate(hud_lines):
        y = 24 + i * 22
        cv2.putText(
            frame, line, (10, y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA,
        )

    return frame
