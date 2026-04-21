#!/usr/bin/env python3
"""
Verify that config.ini.factory, hydra_detect/config_schema.py SCHEMA, and
runtime cfg.get*(..., fallback=...) calls all agree.

Catches the drift described in CLAUDE.md:
  "Config fallbacks must match schema defaults — when adding a new config key,
   ensure the fallback= in pipeline.py matches the default= in config_schema.py.
   Mismatches bypass schema validation."

Checks:
  1. Every [section]/key in config.ini.factory exists in SCHEMA.
  2. Every required=True key in SCHEMA is present in config.ini.factory.
  3. Every cfg.get*(section, key, fallback=X) call in the pipeline references
     a known SCHEMA key, and X matches SCHEMA[section][key].default after type
     coercion.

[vehicle.<name>] profile-overlay sections are skipped (their keys use dotted
`section.key` form and merge into real sections at startup — see
hydra_detect/pipeline/bootstrap.py).

Sentinel probes are recognized and skipped: a `cfg.get(..., fallback="")` on
a non-STRING field is treated as a presence check, not a default value, and
exempted from the fallback/default match rule.

Exit 0 on clean, 1 on any inconsistency.
"""
from __future__ import annotations

import ast
import configparser
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hydra_detect.config_schema import SCHEMA, FieldType  # noqa: E402

SCANNED_SOURCES = [
    "hydra_detect/pipeline/bootstrap.py",
    "hydra_detect/pipeline/control.py",
    "hydra_detect/pipeline/facade.py",
]

GETTERS = {"get", "getint", "getfloat", "getboolean"}


def _literal(node: ast.AST):
    """Extract a Python literal from an AST node, or return Ellipsis if non-literal."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(
        node.operand, ast.Constant
    ):
        return -node.operand.value
    return ...  # sentinel: non-literal expression


def _coerce(value, field_type: FieldType):
    """Coerce a fallback value to the schema type for comparison."""
    if value is None:
        return None
    if field_type == FieldType.BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "yes", "1", "on")
    if field_type == FieldType.INT:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field_type == FieldType.FLOAT:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return str(value) if value is not None else value


def _check_fallbacks() -> list[str]:
    errors: list[str] = []
    for rel in SCANNED_SOURCES:
        path = ROOT / rel
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in GETTERS):
                continue
            if len(node.args) < 2:
                continue
            section = _literal(node.args[0])
            key = _literal(node.args[1])
            if section is ... or key is ...:
                continue

            fallback = ...
            for kw in node.keywords:
                if kw.arg == "fallback":
                    fallback = _literal(kw.value)
            if fallback is ...:
                continue  # no fallback given or non-literal — nothing to compare

            if section not in SCHEMA or key not in SCHEMA[section]:
                errors.append(
                    f"{rel}:{node.lineno} cfg.{func.attr}({section!r}, {key!r}, "
                    f"fallback=...) references a key absent from SCHEMA."
                )
                continue

            spec = SCHEMA[section][key]

            # Sentinel probe: fallback="" on a non-STRING field is a presence
            # check, not a default value. Exempt from the match rule.
            if fallback == "" and spec.type != FieldType.STRING:
                continue

            coerced = _coerce(fallback, spec.type)
            if coerced != spec.default:
                errors.append(
                    f"{rel}:{node.lineno} [{section}].{key}: code fallback={fallback!r} "
                    f"but SCHEMA default={spec.default!r}. Align one or the other — "
                    f"mismatches bypass schema validation."
                )
    return errors


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

    # 3. Every cfg.get*(..., fallback=X) in the scanned pipeline modules must
    #    match SCHEMA[section][key].default.
    errors.extend(_check_fallbacks())

    if errors:
        print("Config consistency check FAILED:\n", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            f"\n{len(errors)} issue(s). Factory config, schema, and runtime "
            f"fallbacks must stay in sync.",
            file=sys.stderr,
        )
        return 1

    total_fields = sum(len(fields) for fields in SCHEMA.values())
    print(
        f"OK: config.ini.factory matches SCHEMA ({total_fields} fields across "
        f"{len(SCHEMA)} sections); fallbacks in {len(SCANNED_SOURCES)} pipeline "
        f"modules align with schema defaults."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
