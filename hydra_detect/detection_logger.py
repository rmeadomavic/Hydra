"""Detection event logger — CSV/JSON output with full-frame snapshots and geo-tagging."""

from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np

from .tracker import TrackedObject, TrackingResult

logger = logging.getLogger(__name__)


class DetectionLogger:
    """Logs detection events to CSV or JSON-lines, with optional image saving.

    Supports:
    - Full-frame annotated JPEG snapshots (like v1.0)
    - Optional cropped object images
    - GPS geo-tagging when coordinates are provided
    - Recent detections buffer for the web UI
    """

    def __init__(
        self,
        log_dir: str = "/data/logs",
        log_format: str = "jsonl",
        save_images: bool = True,
        image_dir: str = "/data/images",
        image_quality: int = 90,
        save_crops: bool = False,
        crop_dir: str = "crops",
        max_recent: int = 50,
    ):
        self._log_dir = Path(log_dir)
        self._log_format = log_format.lower()
        self._save_images = save_images
        self._image_dir = Path(image_dir)
        self._image_quality = image_quality
        self._save_crops = save_crops
        self._crop_dir = Path(crop_dir)
        self._max_recent = max_recent

        self._csv_writer = None
        self._csv_file = None
        self._json_file = None
        self._frame_count = 0

        # Recent detections ring buffer for web UI
        self._recent: list[Dict[str, Any]] = []

    def start(self) -> None:
        """Create directories and open output file."""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            if self._save_images:
                self._image_dir.mkdir(parents=True, exist_ok=True)
            if self._save_crops:
                self._crop_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create logging directories: %s", exc)
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        try:
            if self._log_format == "csv":
                path = self._log_dir / f"detections_{timestamp}.csv"
                self._csv_file = open(path, "w", newline="")
                self._csv_writer = csv.writer(self._csv_file)
                self._csv_writer.writerow([
                    "timestamp", "frame", "track_id", "label", "class_id",
                    "confidence", "x1", "y1", "x2", "y2",
                    "lat", "lon", "alt", "fix", "image",
                ])
                logger.info("Logging detections to %s", path)
            else:
                path = self._log_dir / f"detections_{timestamp}.jsonl"
                self._json_file = open(path, "w")
                logger.info("Logging detections to %s", path)
        except OSError as exc:
            logger.error("Failed to open detection log file: %s", exc)

    def stop(self) -> None:
        """Flush and close log files."""
        for fh_name in ("_csv_file", "_json_file"):
            fh = getattr(self, fh_name, None)
            if fh is not None:
                try:
                    fh.flush()
                    fh.close()
                except OSError as exc:
                    logger.warning("Error closing log file: %s", exc)
                finally:
                    setattr(self, fh_name, None)
        self._csv_writer = None

    def log(
        self,
        tracking_result: TrackingResult,
        frame: Optional[np.ndarray] = None,
        gps: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write tracking results for a single frame.

        Args:
            tracking_result: Tracked objects this frame.
            frame: The BGR frame (for image saving).
            gps: GPS dict with keys lat, lon, alt, fix (raw MAVLink ints).
        """
        if len(tracking_result) == 0:
            self._frame_count += 1
            return

        self._frame_count += 1
        ts = datetime.now(timezone.utc)
        ts_iso = ts.isoformat()
        ts_file = ts.strftime("%Y%m%d_%H%M%S")

        # Parse GPS
        lat, lon, alt, fix = None, None, None, 0
        if gps:
            fix = gps.get("fix", 0)
            if fix >= 3 and gps.get("lat") is not None:
                lat = gps["lat"] / 1e7
                lon = gps["lon"] / 1e7
                alt = gps["alt"] / 1000 if gps.get("alt") is not None else None

        # Save full-frame snapshot
        img_filename = None
        if self._save_images and frame is not None:
            img_filename = f"{ts_file}_{self._frame_count:06d}.jpg"
            try:
                cv2.imwrite(
                    str(self._image_dir / img_filename),
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self._image_quality],
                )
            except Exception as exc:
                logger.warning("Failed to save snapshot: %s", exc)
                img_filename = None

        for track in tracking_result:
            record = {
                "timestamp": ts_iso,
                "frame": self._frame_count,
                "track_id": track.track_id,
                "label": track.label,
                "class_id": track.class_id,
                "confidence": round(track.confidence, 3),
                "bbox": [
                    round(track.x1, 1), round(track.y1, 1),
                    round(track.x2, 1), round(track.y2, 1),
                ],
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "fix": fix,
                "image": img_filename,
            }

            if self._log_format == "csv" and self._csv_writer is not None:
                self._csv_writer.writerow([
                    ts_iso, self._frame_count, track.track_id,
                    track.label, track.class_id,
                    f"{track.confidence:.3f}",
                    f"{track.x1:.1f}", f"{track.y1:.1f}",
                    f"{track.x2:.1f}", f"{track.y2:.1f}",
                    lat, lon, alt, fix, img_filename,
                ])
            elif self._json_file is not None:
                self._json_file.write(json.dumps(record) + "\n")

            # Add to recent buffer
            self._recent.append(record)
            if len(self._recent) > self._max_recent:
                self._recent.pop(0)

            # Save cropped object image
            if self._save_crops and frame is not None:
                self._save_crop(frame, track)

        # Periodic flush
        if self._frame_count % 30 == 0:
            try:
                if self._csv_file is not None:
                    self._csv_file.flush()
                if self._json_file is not None:
                    self._json_file.flush()
            except OSError as exc:
                logger.warning("Failed to flush log file: %s", exc)

    def get_recent(self, n: int = 20) -> list[Dict[str, Any]]:
        """Return the N most recent detection records (for web UI)."""
        return list(self._recent[-n:])

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
        try:
            cv2.imwrite(str(self._crop_dir / fname), crop)
        except Exception as exc:
            logger.warning("Failed to save crop: %s", exc)
