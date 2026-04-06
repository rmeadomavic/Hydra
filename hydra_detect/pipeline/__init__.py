"""Pipeline package exports with lazy loading."""

from __future__ import annotations

__all__ = ["Pipeline", "_build_detector", "RFHuntController"]


def __getattr__(name: str):
    if name in __all__:
        from .facade import Pipeline, RFHuntController, _build_detector

        mapping = {
            "Pipeline": Pipeline,
            "RFHuntController": RFHuntController,
            "_build_detector": _build_detector,
        }
        return mapping[name]
    raise AttributeError(name)
