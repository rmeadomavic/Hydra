#!/usr/bin/env python3
"""
Verify that config.ini.factory and hydra_detect/config_schema.py agree.

Catches the drift described in CLAUDE.md:
  "Config fallbacks must match schema defaults — when adding a new config key,
   ensure the fallback= in pipeline.py matches the default= in config_schema.py.
   Mismatches bypass schema validation."

Checks:
  1. Every [section]/key in config.ini.factory exists in SCHEMA (no typos / dead keys).
  2. Every required=True key in SCHEMA is present in config.ini.factory.

[vehicle.<name>] profile-overlay sections are skipped (their keys use dotted
`section.key` form and merge into real sections at startup — see
hydra_detect/pipeline/bootstrap.py). Validating those cross-references is
a follow-up once existing vestigial overlay keys are cleaned up.

Exit 0 on clean, 1 on any inconsistency. Prints a diff so the failure is actionable.
"""
from __future__ import annotations

import configparser
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hydra_detect.config_schema import SCHEMA  # noqa: E402


def main() -> int:
    factory = ROOT / "config.ini.factory"
    if not factory.exists():
        print(f"ERROR: {factory} not found", file=sys.stderr)
        return 1

    cfg = configparser.ConfigParser()
    cfg.read(factory)

    errors: list[str] = []

    # 1. Every section/key in factory must exist in SCHEMA.
    #    Skip [vehicle.*] profile overlays — handled by bootstrap.py at startup.
    for section in cfg.sections():
        if section.startswith("vehicle."):
            continue
        if section not in SCHEMA:
            errors.append(
                f"config.ini.factory has [{section}] but it is absent from SCHEMA "
                f"in hydra_detect/config_schema.py"
            )
            continue
        for key in cfg[section]:
            if key not in SCHEMA[section]:
                errors.append(
                    f"config.ini.factory: [{section}].{key} is not declared in SCHEMA. "
                    f"Add a FieldSpec or remove the key."
                )

    # 2. Every required=True key in SCHEMA must be in factory.
    for section, fields in SCHEMA.items():
        for key, spec in fields.items():
            if spec.required and (section not in cfg or key not in cfg[section]):
                errors.append(
                    f"SCHEMA marks [{section}].{key} as required=True but it is missing "
                    f"from config.ini.factory"
                )

    if errors:
        print("Config consistency check FAILED:\n", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            f"\n{len(errors)} issue(s). Factory config and schema must stay in sync.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: config.ini.factory matches SCHEMA "
        f"({sum(len(fields) for fields in SCHEMA.values())} fields checked across "
        f"{len(SCHEMA)} sections)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
