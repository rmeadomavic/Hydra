"""Pipeline runtime lifecycle helpers."""

from __future__ import annotations


class PipelineRuntime:
    """Small runtime helper for start/stop lifecycle ordering."""

    def __init__(self, pipeline: object):
        self.pipeline = pipeline

    def start_components(self) -> None:
        p = self.pipeline
        if p._det_logger is not None:
            p._det_logger.start()
        p._running = True

    def stop_components(self) -> None:
        p = self.pipeline
        if getattr(p, "_servo_tracker", None) is not None:
            p._servo_tracker.safe()
        if getattr(p, "_det_logger", None) is not None:
            p._det_logger.stop()
        p._running = False
