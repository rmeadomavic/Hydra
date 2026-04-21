"""Benchmark overlay rendering throughput.

Measures how many frames/sec draw_tracks() can process on a synthetic
workload: 1080p frame with 15 tracked objects (mixed locked/dimmed).
This is the hot-path rendering cost paid every frame in the pipeline.

Usage:
    python benchmarks/bench_overlay.py          # prints JSON result
    python benchmarks/bench_overlay.py --frames 500
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np

sys.path.insert(0, ".")
from hydra_detect.overlay import draw_tracks
from hydra_detect.tracker import TrackedObject, TrackingResult


def _make_tracks(n: int, w: int, h: int) -> list[TrackedObject]:
    rng = np.random.default_rng(42)
    tracks = []
    for i in range(n):
        cx = rng.integers(100, w - 100)
        cy = rng.integers(100, h - 100)
        bw = rng.integers(40, 200)
        bh = rng.integers(40, 200)
        tracks.append(TrackedObject(
            track_id=i + 1,
            x1=float(cx - bw // 2),
            y1=float(cy - bh // 2),
            x2=float(cx + bw // 2),
            y2=float(cy + bh // 2),
            confidence=rng.uniform(0.4, 0.95),
            class_id=i % 10,
            label=f"class_{i % 5}",
        ))
    return tracks


def run_benchmark(num_frames: int = 300) -> dict:
    W, H = 1920, 1080
    tracks = _make_tracks(15, W, H)
    tracking = TrackingResult(tracks=tracks, active_ids=len(tracks))
    alert_classes = {"class_0", "class_1", "class_2"}
    base_frame = np.zeros((H, W, 3), dtype=np.uint8)

    times = []
    for i in range(num_frames):
        frame = base_frame.copy()
        t0 = time.perf_counter()
        draw_tracks(
            frame,
            tracking,
            inference_ms=12.5,
            fps=30.0,
            locked_track_id=3,
            lock_mode="strike" if i % 2 == 0 else "track",
            alert_classes=alert_classes,
        )
        times.append(time.perf_counter() - t0)

    times_ms = [t * 1000 for t in times]
    avg_ms = sum(times_ms) / len(times_ms)
    p50 = sorted(times_ms)[len(times_ms) // 2]
    p99 = sorted(times_ms)[int(len(times_ms) * 0.99)]
    fps = 1000.0 / avg_ms if avg_ms > 0 else 0

    return {
        "frames": num_frames,
        "avg_ms": round(avg_ms, 3),
        "p50_ms": round(p50, 3),
        "p99_ms": round(p99, 3),
        "fps": round(fps, 1),
        "score": round(fps, 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=300)
    args = parser.parse_args()
    result = run_benchmark(args.frames)
    print(json.dumps(result))
