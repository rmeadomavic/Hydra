"""Migration 004 — add re-ID keys to [tracker].

Defaults preserve existing behaviour: reid_enabled=false keeps ByteTrack
as the active tracker so deployed units that do not flip the flag pay
zero runtime cost. The boxmot dependency is optional (see
requirements-extra.txt) — installing it is only required when an
operator turns reid_enabled=true on a specific unit.

Idempotent: keys are only inserted when absent.
"""

from __future__ import annotations

import configparser

from_version: int = 3
to_version: int = 4


_DEFAULTS: dict[str, str] = {
    "reid_enabled": "false",
    "reid_tracker_type": "botsort",
}


def migrate(cfg: configparser.ConfigParser) -> None:
    """Insert re-ID keys into [tracker] if missing."""
    if not cfg.has_section("tracker"):
        cfg.add_section("tracker")
    for key, value in _DEFAULTS.items():
        if not cfg.has_option("tracker", key):
            cfg.set("tracker", key, value)
