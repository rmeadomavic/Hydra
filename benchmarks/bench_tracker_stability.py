"""Benchmark tracker ID stability.

Generates synthetic moving objects across 200 frames with realistic
challenges (occlusion, confidence jitter, missed detections) and
counts track ID switches. Lower = better.

The tracker parameters are read from config.ini [tracker] section,
so evo can mutate them and re-run.

Usage:
    python benchmarks/bench_tracker_stability.py
"""
from __future__ import annotations

import configparser
import json
import sys
import types

# supervision 0.27 needs the `deprecate` package which has a broken build.
# Provide a no-op shim so the import chain succeeds.
if "deprecate" not in sys.modules:
    _shim = types.ModuleType("deprecate")
    def _deprecated(func=None, **kwargs):
        return (lambda f: f) if func is None else func
    _shim.deprecated = _deprecated
    sys.modules["deprecate"] = _shim

import numpy as np

sys.path.insert(0, ".")
from hydra_detect.detectors.base import Detection, DetectionResult
from hydra_detect.tracker import ByteTracker


NUM_OBJECTS = 8
NUM_FRAMES = 200
FRAME_W, FRAME_H = 1920, 1080


def _generate_sequence(
    rng: np.random.Generator,
) -> list[list[Detection]]:
    """Generate moving objects with occlusion gaps and confidence jitter."""
    starts_x = rng.uniform(200, FRAME_W - 200, size=NUM_OBJECTS)
    starts_y = rng.uniform(200, FRAME_H - 200, size=NUM_OBJECTS)
    vel_x = rng.uniform(-8, 8, size=NUM_OBJECTS)
    vel_y = rng.uniform(-5, 5, size=NUM_OBJECTS)
    box_w = rng.uniform(60, 180, size=NUM_OBJECTS)
    box_h = rng.uniform(60, 180, size=NUM_OBJECTS)

    frames: list[list[Detection]] = []
    for f in range(NUM_FRAMES):
        dets = []
        for i in range(NUM_OBJECTS):
            cx = starts_x[i] + vel_x[i] * f
            cy = starts_y[i] + vel_y[i] * f
            cx = np.clip(cx, box_w[i] / 2, FRAME_W - box_w[i] / 2)
            cy = np.clip(cy, box_h[i] / 2, FRAME_H - box_h[i] / 2)

            if rng.random() < 0.08:
                continue

            conf = np.clip(rng.normal(0.75, 0.12), 0.15, 0.99)

            jx = rng.normal(0, 3)
            jy = rng.normal(0, 3)

            dets.append(Detection(
                x1=float(cx - box_w[i] / 2 + jx),
                y1=float(cy - box_h[i] / 2 + jy),
                x2=float(cx + box_w[i] / 2 + jx),
                y2=float(cy + box_h[i] / 2 + jy),
                confidence=float(conf),
                class_id=i % 3,
                label=f"class_{i % 3}",
            ))
        frames.append(dets)
    return frames


def _count_id_switches(
    sequence: list[list[Detection]],
    tracker: ByteTracker,
) -> int:
    """Run tracker on sequence and count ID switches.

    An ID switch occurs when the same spatial object (matched by IoU
    to the previous frame's position) gets a different track_id.
    """
    prev_positions: dict[int, tuple[float, float]] = {}
    obj_to_track: dict[int, int] = {}
    switches = 0

    for frame_dets in sequence:
        det_result = DetectionResult(detections=frame_dets)
        track_result = tracker.update(det_result)

        curr_positions: dict[int, tuple[float, float]] = {}
        for track in track_result:
            cx, cy = track.center
            curr_positions[track.track_id] = (cx, cy)

            best_obj = -1
            best_dist = float("inf")
            for obj_idx, (px, py) in prev_positions.items():
                d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                if d < best_dist and d < 150:
                    best_dist = d
                    best_obj = obj_idx

            if best_obj >= 0:
                if best_obj in obj_to_track and obj_to_track[best_obj] != track.track_id:
                    switches += 1
                obj_to_track[best_obj] = track.track_id

        prev_positions = curr_positions

    return switches


def run_benchmark() -> dict:
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")

    track_thresh = cfg.getfloat("tracker", "track_thresh", fallback=0.5)
    track_buffer = cfg.getint("tracker", "track_buffer", fallback=30)
    match_thresh = cfg.getfloat("tracker", "match_thresh", fallback=0.8)

    scores = []
    for seed in range(5):
        rng = np.random.default_rng(seed)
        sequence = _generate_sequence(rng)

        tracker = ByteTracker(
            track_thresh=track_thresh,
            track_buffer=track_buffer,
            match_thresh=match_thresh,
            frame_rate=30,
        )
        tracker.init()

        switches = _count_id_switches(sequence, tracker)
        scores.append(switches)

    avg_switches = sum(scores) / len(scores)

    return {
        "track_thresh": track_thresh,
        "track_buffer": track_buffer,
        "match_thresh": match_thresh,
        "seeds": len(scores),
        "switches_per_seed": scores,
        "avg_switches": round(avg_switches, 1),
        "score": round(avg_switches, 1),
    }


if __name__ == "__main__":
    result = run_benchmark()
    print(json.dumps(result))
