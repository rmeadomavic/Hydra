"""Migration 003 — add attitude-compensation keys to [guidance].

Adds two new keys so the roll/pitch rotation introduced in guidance.py can be
disabled per-platform (mostly: vehicles with a level-stabilized gimbal should
set gimbal_stabilized=true, since the camera is already level).

Idempotent: keys are inserted only when absent so a user who pre-edited the
config keeps their value.
"""

from __future__ import annotations

import configparser

from_version: int = 2
to_version: int = 3


_DEFAULTS: dict[str, str] = {
    "attitude_compensation_enabled": "true",
    "gimbal_stabilized": "false",
}


def migrate(cfg: configparser.ConfigParser) -> None:
    """Insert attitude-compensation keys into [guidance] if missing."""
    if not cfg.has_section("guidance"):
        cfg.add_section("guidance")
    for key, value in _DEFAULTS.items():
        if not cfg.has_option("guidance", key):
            cfg.set("guidance", key, value)
