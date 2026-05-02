"""Migration 002 — add forward-predictor keys to [guidance].

Adds four new keys to existing user config files so the alpha-beta predictor
introduced in guidance.py can read them. Idempotent: keys are only inserted
when absent, so a config that already has any of them (e.g. user pre-edited)
keeps the user's value.

The predictor is enabled by default. To shadow it, set predictor_enabled to
false in [guidance] and re-run migrations (or just edit the value — the
schema accepts a manual override).
"""

from __future__ import annotations

import configparser

from_version: int = 1
to_version: int = 2


_DEFAULTS: dict[str, str] = {
    "loop_delay_ms": "100.0",
    "predictor_enabled": "true",
    "predictor_alpha": "0.5",
    "predictor_beta": "0.05",
}


def migrate(cfg: configparser.ConfigParser) -> None:
    """Insert predictor keys into [guidance] if they are missing."""
    if not cfg.has_section("guidance"):
        cfg.add_section("guidance")
    for key, value in _DEFAULTS.items():
        if not cfg.has_option("guidance", key):
            cfg.set("guidance", key, value)
