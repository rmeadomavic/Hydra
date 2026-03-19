"""Detection event logger — CSV/JSON output with full-frame snapshots and geo-tagging."""

from __future__ import annotations

import csv
import json
import logging
import queue
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np

from .tracker import TrackedObject, TrackingResult

logger = logging.getLogger(__name__)

# Sentinel that signals the background writer thread to stop.
_STOP = object()


class DetectionLogger:
    """Logs detection events to CSV or JSON-lines, with optional image saving.

    Supports:
    - Full-frame annotated JPEG snapshots (like v1.0)
    - Optional cropped object images
    - GPS geo-tagging when coordinates are provided
    - Recent detections buffer for the web UI

    All file I/O (JPEG writes, CSV/JSONL writes) is offloaded to a daemon
    background thread so the detection hot-loop is never blocked by slow
    storage.  A bounded queue (maxsize=100) caps memory growth; if the
    writer falls behind, new work items are dropped with a warning rather
    than stalling the caller.
    """

    _QUEUE_MAXSIZE = 100

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
        self._disabled = False

        # Recent detections ring buffer for web UI.
        # Updated on the caller thread so the web API sees results immediately.
        self._recent: deque[Dict[str, Any]] = deque(maxlen=self._max_recent)
        self._recent_lock = threading.Lock()

        # Background writer state.
        self._write_queue: queue.Queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._writer_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create directories, open output file, and start background writer."""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            if self._save_images:
                self._image_dir.mkdir(parents=True, exist_ok=True)
            if self._save_crops:
                self._crop_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create logging directories: %s", exc)
            self._disabled = True
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
            self._disabled = True
            return

        # Start the background I/O thread.
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="detection-logger-writer",
            daemon=True,
        )
        self._writer_thread.start()

    def stop(self) -> None:
        """Drain the write queue, join the writer thread, and close log files."""
        if self._writer_thread is not None and self._writer_thread.is_alive():
            self._write_queue.put(_STOP)
            self._writer_thread.join()
            self._writer_thread = None

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

    # ------------------------------------------------------------------
    # Hot-path method (called from the detection thread)
    # ------------------------------------------------------------------

    def log(
        self,
        tracking_result: TrackingResult,
        frame: Optional[np.ndarray] = None,
        gps: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Enqueue tracking results for a single frame.

        Record metadata and the recent buffer are updated immediately on the
        caller thread so the web UI sees results without waiting for the
        background writer.  Actual file I/O is handled off-thread.

        Args:
            tracking_result: Tracked objects this frame.
            frame: The BGR frame (for image saving).
            gps: GPS dict with keys lat, lon, alt, fix (raw MAVLink ints).
        """
        if self._disabled or len(tracking_result) == 0:
            self._frame_count += 1
            return

        self._frame_count += 1
        ts = datetime.now(timezone.utc)
        ts_iso = ts.isoformat()
        ts_file = ts.strftime("%Y%m%d_%H%M%S")
        frame_no = self._frame_count

        # Parse GPS (cheap, no I/O).
        lat, lon, alt, fix = None, None, None, 0
        if gps:
            fix = gps.get("fix", 0)
            if fix >= 3 and gps.get("lat") is not None:
                lat = gps["lat"] / 1e7
                lon = gps["lon"] / 1e7
                alt = gps["alt"] / 1000 if gps.get("alt") is not None else None

        # Derive the image filename now so records are complete for the web UI
        # even before the file is physically written.
        img_filename: str | None = None
        if self._save_images and frame is not None:
            img_filename = f"{ts_file}_{frame_no:06d}.jpg"

        # Build records and update the recent buffer immediately (caller thread).
        records: list[Dict[str, Any]] = []
        for track in tracking_result:
            record: Dict[str, Any] = {
                "timestamp": ts_iso,
                "frame": frame_no,
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
            records.append(record)

            with self._recent_lock:
                self._recent.append(record)  # deque evicts oldest when full

        # Copy the frame only when we need it for writing (avoids the copy
        # entirely when image/crop saving is disabled or there is no frame).
        frame_copy: np.ndarray | None = None
        if (self._save_images or self._save_crops) and frame is not None:
            frame_copy = frame.copy()

        work_item = {
            "records": records,
            "frame": frame_copy,
            "frame_no": frame_no,
            "img_filename": img_filename,
            "tracking_result": list(tracking_result),
            "flush": (frame_no % 30 == 0),
        }

        try:
            self._write_queue.put_nowait(work_item)
        except queue.Full:
            logger.warning(
                "Detection logger queue full — dropping frame %d "
                "(storage too slow?)",
                frame_no,
            )

    def get_recent(self, n: int = 20) -> list[Dict[str, Any]]:
        """Return the N most recent detection records (for web UI)."""
        with self._recent_lock:
            return list(self._recent)[-n:]

    # ------------------------------------------------------------------
    # Background writer loop
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        """Consume work items from the queue and perform all file I/O."""
        while True:
            try:
                item = self._write_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is _STOP:
                # Drain any remaining items before exiting so stop() is a
                # clean flush — no detections are silently discarded.
                while True:
                    try:
                        remaining = self._write_queue.get_nowait()
                    except queue.Empty:
                        break
                    if remaining is not _STOP:
                        self._process_work_item(remaining)
                break

            self._process_work_item(item)

    def _process_work_item(self, item: Dict[str, Any]) -> None:
        """Write a single work item to disk (runs on the background thread)."""
        records: list[Dict[str, Any]] = item["records"]
        frame: np.ndarray | None = item["frame"]
        frame_no: int = item["frame_no"]
        img_filename: str | None = item["img_filename"]
        tracking_result: list[TrackedObject] = item["tracking_result"]
        do_flush: bool = item["flush"]

        # Save full-frame snapshot.
        if self._save_images and frame is not None and img_filename is not None:
            try:
                cv2.imwrite(
                    str(self._image_dir / img_filename),
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self._image_quality],
                )
            except Exception as exc:
                logger.warning("Failed to save snapshot: %s", exc)

        # Write log records.
        for record, track in zip(records, tracking_result):
            if self._log_format == "csv" and self._csv_writer is not None:
                try:
                    self._csv_writer.writerow([
                        record["timestamp"], frame_no, track.track_id,
                        track.label, track.class_id,
                        f"{track.confidence:.3f}",
                        f"{track.x1:.1f}", f"{track.y1:.1f}",
                        f"{track.x2:.1f}", f"{track.y2:.1f}",
                        record["lat"], record["lon"], record["alt"],
                        record["fix"], img_filename,
                    ])
                except Exception as exc:
                    logger.warning("Failed to write CSV record: %s", exc)
            elif self._json_file is not None:
                try:
                    self._json_file.write(json.dumps(record) + "\n")
                except Exception as exc:
                    logger.warning("Failed to write JSONL record: %s", exc)

            # Save cropped object image.
            if self._save_crops and frame is not None:
                self._save_crop(frame, track, frame_no)

        # Periodic flush to bound data loss on crash.
        if do_flush:
            try:
                if self._csv_file is not None:
                    self._csv_file.flush()
                if self._json_file is not None:
                    self._json_file.flush()
            except OSError as exc:
                logger.warning("Failed to flush log file: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_crop(
        self, frame: np.ndarray, track: TrackedObject, frame_no: int
    ) -> None:
        """Save a cropped image of the tracked object."""
        h, w = frame.shape[:2]
        x1 = max(0, int(track.x1))
        y1 = max(0, int(track.y1))
        x2 = min(w, int(track.x2))
        y2 = min(h, int(track.y2))

        if x2 <= x1 or y2 <= y1:
            return

        crop = frame[y1:y2, x1:x2]
        fname = f"frame{frame_no:06d}_id{track.track_id}_{track.label}.jpg"
        try:
            cv2.imwrite(str(self._crop_dir / fname), crop)
        except Exception as exc:
            logger.warning("Failed to save crop: %s", exc)
