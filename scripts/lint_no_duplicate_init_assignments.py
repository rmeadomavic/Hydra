#!/usr/bin/env python3
"""Fail when __init__ repeats top-level self attribute assignments."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _target_attr(stmt: ast.stmt) -> str | None:
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
        return None
    target = stmt.targets[0]
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return target.attr
    return None


def duplicate_init_assignments(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if not isinstance(child, ast.FunctionDef) or child.name != "__init__":
                continue
            seen: dict[str, int] = {}
            for stmt in child.body:
                attr = _target_attr(stmt)
                if attr is None:
                    continue
                if attr in seen:
                    errors.append(
                        f"{path}:{stmt.lineno}: duplicate top-level self.{attr} assignment in __init__"
                    )
                seen[attr] = stmt.lineno
    return errors


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    errors: list[str] = []
    for path in paths:
        errors.extend(duplicate_init_assignments(path))
    if errors:
        print("\n".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
