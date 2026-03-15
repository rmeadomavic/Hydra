"""ByteTrack multi-object tracker wrapper."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .detectors.base import Detection, DetectionResult

logger = logging.getLogger(__name__)


@dataclass
class TrackedObject:
    """A tracked object with persistent ID."""

    track_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    label: str = ""

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


@dataclass
class TrackingResult:
    """Tracking output for a single frame."""

    tracks: List[TrackedObject] = field(default_factory=list)
    active_ids: int = 0

    def __len__(self) -> int:
        return len(self.tracks)

    def __iter__(self):
        return iter(self.tracks)

    def find(self, track_id: int) -> TrackedObject | None:
        """Find a track by ID, or None if not present."""
        for t in self.tracks:
            if t.track_id == track_id:
                return t
        return None


class ByteTracker:
    """Wrapper around the ``byte_tracker`` / ``supervision`` ByteTrack implementation."""

    def __init__(
        self,
        track_thresh: float = 0.5,
        track_buffer: int = 30,
        match_thresh: float = 0.8,
    ):
        self._track_thresh = track_thresh
        self._track_buffer = track_buffer
        self._match_thresh = match_thresh
        self._tracker = None

    def init(self) -> None:
        """Initialise the underlying tracker."""
        try:
            import supervision as sv

            self._tracker = sv.ByteTrack(
                track_activation_threshold=self._track_thresh,
                lost_track_buffer=self._track_buffer,
                minimum_matching_threshold=self._match_thresh,
                frame_rate=30,
            )
            logger.info("ByteTrack initialised (supervision back-end).")
        except ImportError:
            logger.warning(
                "supervision not installed — tracking disabled. "
                "Install with: pip install supervision"
            )

    def update(
        self,
        detection_result: DetectionResult,
        frame_shape: tuple[int, int] | None = None,
    ) -> TrackingResult:
        """Feed detections and return tracked objects."""
        if self._tracker is None:
            # Pass-through: assign sequential IDs
            tracks = [
                TrackedObject(
                    track_id=i,
                    x1=d.x1, y1=d.y1, x2=d.x2, y2=d.y2,
                    confidence=d.confidence,
                    class_id=d.class_id,
                    label=d.label,
                )
                for i, d in enumerate(detection_result)
            ]
            return TrackingResult(tracks=tracks, active_ids=len(tracks))

        import supervision as sv

        dets = detection_result.detections
        if not dets:
            self._tracker.update_with_detections(sv.Detections.empty())
            return TrackingResult()

        xyxy = np.array([[d.x1, d.y1, d.x2, d.y2] for d in dets], dtype=np.float32)
        confidence = np.array([d.confidence for d in dets], dtype=np.float32)
        class_ids = np.array([d.class_id for d in dets], dtype=int)

        sv_dets = sv.Detections(
            xyxy=xyxy,
            confidence=confidence,
            class_id=class_ids,
        )

        tracked = self._tracker.update_with_detections(sv_dets)

        # Build label lookup from original detections
        label_map: Dict[int, str] = {d.class_id: d.label for d in dets}

        tracks: list[TrackedObject] = []
        for i in range(len(tracked)):
            tid = int(tracked.tracker_id[i]) if tracked.tracker_id is not None else i
            x1, y1, x2, y2 = tracked.xyxy[i].tolist()
            cls = int(tracked.class_id[i]) if tracked.class_id is not None else 0
            conf = float(tracked.confidence[i]) if tracked.confidence is not None else 0.0
            tracks.append(
                TrackedObject(
                    track_id=tid,
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    confidence=conf,
                    class_id=cls,
                    label=label_map.get(cls, str(cls)),
                )
            )

        return TrackingResult(tracks=tracks, active_ids=len(tracks))

    def reset(self) -> None:
        """Reset tracker state."""
        if self._tracker is not None:
            self._tracker.reset()
