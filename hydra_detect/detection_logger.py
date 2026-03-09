"""Detection event logger — CSV and JSON output with optional image crops."""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .tracker import TrackedObject, TrackingResult

logger = logging.getLogger(__name__)


class DetectionLogger:
    """Logs detection events to CSV or JSON, with optional image crops."""

    def __init__(
        self,
        log_dir: str = "logs",
        log_format: str = "csv",
        save_crops: bool = False,
        crop_dir: str = "crops",
    ):
        self._log_dir = Path(log_dir)
        self._log_format = log_format.lower()
        self._save_crops = save_crops
        self._crop_dir = Path(crop_dir)

        self._csv_writer = None
        self._csv_file = None
        self._json_file = None
        self._frame_count = 0

    def start(self) -> None:
        """Create log directory and open output file."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        if self._save_crops:
            self._crop_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if self._log_format == "csv":
            path = self._log_dir / f"detections_{timestamp}.csv"
            self._csv_file = open(path, "w", newline="")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow([
                "timestamp", "frame", "track_id", "label", "class_id",
                "confidence", "x1", "y1", "x2", "y2",
            ])
            logger.info("Logging detections to %s", path)
        else:
            path = self._log_dir / f"detections_{timestamp}.jsonl"
            self._json_file = open(path, "w")
            logger.info("Logging detections to %s", path)

    def stop(self) -> None:
        """Flush and close log files."""
        if self._csv_file is not None:
            self._csv_file.close()
            self._csv_file = None
        if self._json_file is not None:
            self._json_file.close()
            self._json_file = None

    def log(
        self,
        tracking_result: TrackingResult,
        frame: Optional[np.ndarray] = None,
    ) -> None:
        """Write tracking results for a single frame."""
        self._frame_count += 1
        ts = datetime.now(timezone.utc).isoformat()

        for track in tracking_result:
            if self._log_format == "csv" and self._csv_writer is not None:
                self._csv_writer.writerow([
                    ts,
                    self._frame_count,
                    track.track_id,
                    track.label,
                    track.class_id,
                    f"{track.confidence:.3f}",
                    f"{track.x1:.1f}",
                    f"{track.y1:.1f}",
                    f"{track.x2:.1f}",
                    f"{track.y2:.1f}",
                ])
            elif self._json_file is not None:
                record = {
                    "timestamp": ts,
                    "frame": self._frame_count,
                    "track_id": track.track_id,
                    "label": track.label,
                    "class_id": track.class_id,
                    "confidence": round(track.confidence, 3),
                    "bbox": [
                        round(track.x1, 1),
                        round(track.y1, 1),
                        round(track.x2, 1),
                        round(track.y2, 1),
                    ],
                }
                self._json_file.write(json.dumps(record) + "\n")

            # Save image crop
            if self._save_crops and frame is not None:
                self._save_crop(frame, track)

        # Flush periodically
        if self._frame_count % 30 == 0:
            if self._csv_file is not None:
                self._csv_file.flush()
            if self._json_file is not None:
                self._json_file.flush()

    def _save_crop(self, frame: np.ndarray, track: TrackedObject) -> None:
        """Save a cropped image of the tracked object."""
        h, w = frame.shape[:2]
        x1 = max(0, int(track.x1))
        y1 = max(0, int(track.y1))
        x2 = min(w, int(track.x2))
        y2 = min(h, int(track.y2))

        if x2 <= x1 or y2 <= y1:
            return

        crop = frame[y1:y2, x1:x2]
        fname = f"frame{self._frame_count:06d}_id{track.track_id}_{track.label}.jpg"
        cv2.imwrite(str(self._crop_dir / fname), crop)
