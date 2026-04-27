"""Migration 001 — add [meta] schema_version field.

Applies to configs that have no [meta] section (i.e. version 0).
Idempotent: if [meta] already exists with schema_version, this is a no-op
(the runner won't call it at all since from_version check gates it).
"""

from __future__ import annotations

import configparser

from_version: int = 0
to_version: int = 1


def migrate(cfg: configparser.ConfigParser) -> None:
    """Insert [meta] schema_version = 1 if the section is absent."""
    if not cfg.has_section("meta"):
        cfg.add_section("meta")
    cfg.set("meta", "schema_version", "1")
