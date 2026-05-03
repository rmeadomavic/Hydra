"""Migration 004 — ensure [web] hud_layout key exists.

Older configs (pre-Phase 2 dashboard) lack the [web] hud_layout key. The
field is schema-validated and consumed by ops.js, so missing-key configs
silently fall back to the schema default. This migration writes the
default explicitly so the value round-trips through GET/POST
/api/settings/hud_layout without surprises.

Idempotent: only inserts the key when absent so a user who already chose
a layout keeps their preference.
"""

from __future__ import annotations

import configparser

from_version: int = 3
to_version: int = 4


_DEFAULTS: dict[str, str] = {
    "hud_layout": "classic",
}


def migrate(cfg: configparser.ConfigParser) -> None:
    """Insert hud_layout default into [web] when absent."""
    if not cfg.has_section("web"):
        cfg.add_section("web")
    for key, value in _DEFAULTS.items():
        if not cfg.has_option("web", key):
            cfg.set("web", key, value)
