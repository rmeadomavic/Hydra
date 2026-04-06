"""External integration wiring for Pipeline."""

from __future__ import annotations


def _get_stream_state():
    from ..web.server import stream_state

    return stream_state


class PipelineIntegrations:
    def __init__(self, pipeline: object):
        self.pipeline = pipeline

    def register_web_callbacks(self, adapter: object) -> None:
        _get_stream_state().set_callbacks(**adapter.callbacks())
