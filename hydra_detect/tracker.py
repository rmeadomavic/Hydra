"""Multi-object tracker wrappers.

Two implementations live here:

- ``ByteTracker``: the default. Wraps the supervision ByteTrack backend.
  Cheap, robust, but swaps IDs on full occlusion and in groups.

- ``ReIDTracker``: optional, gated by ``[tracker] reid_enabled = true``.
  Wraps boxmot (BoT-SORT / DeepOCSORT / etc.) which layers an appearance
  embedding on top of motion-based tracking. Survives target crossings in
  cluttered scenes — the textbook fix for Follow-mode flying to the wrong
  person. Requires the optional ``boxmot`` dependency from
  ``requirements-extra.txt``; the import is lazy so deployed units that
  do not enable the flag pay zero runtime cost.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .detectors.base import DetectionResult

logger = logging.getLogger(__name__)


_BOXMOT_INSTALL_HINT = (
    "Install with: pip install -r requirements-extra.txt "
    "(boxmot is optional and not in the base requirements set)."
)


def reid_dependency_available() -> bool:
    """Return True iff the optional ``boxmot`` package is importable."""
    spec = importlib.util.find_spec("boxmot")
    return spec is not None


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
        frame_rate: int = 30,
    ):
        self._track_thresh = track_thresh
        self._track_buffer = track_buffer
        self._match_thresh = match_thresh
        self._frame_rate = frame_rate
        self._tracker = None

    def init(self) -> None:
        """Initialise the underlying tracker."""
        try:
            import supervision as sv

            self._tracker = sv.ByteTrack(
                track_activation_threshold=self._track_thresh,
                lost_track_buffer=self._track_buffer,
                minimum_matching_threshold=self._match_thresh,
                frame_rate=self._frame_rate,
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
        frame: np.ndarray | None = None,
    ) -> TrackingResult:
        """Feed detections and return tracked objects.

        ``frame`` is accepted for API compatibility with ``ReIDTracker`` but
        ignored — ByteTrack does not use appearance embeddings.
        """
        del frame  # unused
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

        # Build label lookup from original detections.
        # class_id → label is 1:1 for YOLO models (names dict is canonical source)
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


class ReIDTracker:
    """Wraps a boxmot motion + appearance tracker (BoT-SORT by default).

    Layered on top of ByteTrack-style motion association by an OSNet
    appearance embedding so identity survives full occlusions and target
    crossings in cluttered scenes — the documented failure mode of plain
    ByteTrack on Follow-mode group scenarios.

    The boxmot dependency is optional: ``init()`` lazy-imports it and
    raises a clear ImportError pointing at ``requirements-extra.txt`` if
    the package is absent, so deployed units that do not flip
    ``[tracker] reid_enabled = true`` pay zero runtime cost beyond the
    optional install footprint.

    Public API mirrors ``ByteTracker``: ``init()``, ``update(det, frame=...)``,
    ``reset()``. ``update()`` requires a frame — appearance embeddings cannot
    be extracted without the image data.
    """

    def __init__(
        self,
        tracker_type: str = "botsort",
        reid_weights: Optional[str] = None,
        device: str = "cuda:0",
        half: bool = True,
    ):
        self._tracker_type = tracker_type
        self._reid_weights = reid_weights
        self._device = device
        self._half = half
        self._tracker = None  # boxmot tracker handle, populated by init()

    def init(self) -> None:
        """Lazy-import boxmot and instantiate the underlying tracker.

        Raises ImportError with an install hint if boxmot is not present.
        """
        try:
            boxmot = importlib.import_module("boxmot")
        except ImportError as exc:
            raise ImportError(
                f"ReIDTracker requires the optional boxmot package. "
                f"{_BOXMOT_INSTALL_HINT}"
            ) from exc

        # boxmot.create_tracker accepts tracker_type as a positional or
        # keyword arg depending on version; pass as kw for clarity.
        kwargs: dict = {"tracker_type": self._tracker_type}
        if self._reid_weights:
            kwargs["reid_weights"] = self._reid_weights
        kwargs["device"] = self._device
        kwargs["half"] = self._half

        try:
            self._tracker = boxmot.create_tracker(**kwargs)
        except TypeError:
            # Older boxmot versions take tracker_type positionally only.
            self._tracker = boxmot.create_tracker(self._tracker_type)
        logger.info(
            "ReIDTracker initialised (boxmot %s, device=%s).",
            self._tracker_type, self._device,
        )

    def update(
        self,
        detection_result: DetectionResult,
        frame_shape: tuple[int, int] | None = None,
        frame: np.ndarray | None = None,
    ) -> TrackingResult:
        """Feed detections + frame and return tracked objects.

        Unlike ByteTracker, ``frame`` is required — the appearance
        embedding extractor needs the actual image. Passing ``None``
        raises ValueError rather than silently degrading to ID-swap-prone
        motion-only tracking.
        """
        if self._tracker is None:
            raise RuntimeError("ReIDTracker.init() must be called before update().")
        if frame is None:
            raise ValueError(
                "ReIDTracker.update() requires the current frame for "
                "appearance-embedding extraction; got frame=None."
            )

        dets = detection_result.detections
        if not dets:
            # Some boxmot versions accept an empty Nx6; others reject it.
            # Skip the call entirely to keep behaviour deterministic.
            return TrackingResult()

        # boxmot wants Nx6: x1, y1, x2, y2, conf, cls.
        det_array = np.array(
            [[d.x1, d.y1, d.x2, d.y2, d.confidence, d.class_id] for d in dets],
            dtype=np.float32,
        )

        result = self._tracker.update(det_array, frame)

        # boxmot returns Nx7 or Nx8 depending on version: x1, y1, x2, y2,
        # id, conf, cls, [ind]. Read the first 7 columns; treat missing
        # rows as no-tracks-this-frame.
        if result is None or len(result) == 0:
            return TrackingResult()

        result = np.asarray(result)
        label_map: Dict[int, str] = {d.class_id: d.label for d in dets}

        tracks: list[TrackedObject] = []
        for row in result:
            x1, y1, x2, y2 = float(row[0]), float(row[1]), float(row[2]), float(row[3])
            tid = int(row[4])
            conf = float(row[5]) if len(row) > 5 else 0.0
            cls = int(row[6]) if len(row) > 6 else 0
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
        """Reset tracker state. boxmot trackers expose .reset() in
        recent versions; older ones are reset by re-instantiation."""
        if self._tracker is None:
            return
        if hasattr(self._tracker, "reset"):
            self._tracker.reset()
        else:
            # Re-init from scratch; the original parameters are still
            # held on this instance.
            self.init()
