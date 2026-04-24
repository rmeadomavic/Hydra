#!/usr/bin/env python3
"""Storage rotation — preview or apply per-category file retention.

Usage:
    python scripts/storage_rotation.py [--config config.ini] [--data output_data/] [--apply]

Dry-run is the default. Pass --apply to delete files.

Exit codes:
    0  Normal exit (dry-run or cleanup completed, even if errors occurred).
    1  Config error or unreadable output_data/ directory.
"""

from __future__ import annotations

import argparse
import configparser
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve the repo root relative to this script.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(REPO_ROOT))

from hydra_detect.storage_rotation import (  # noqa: E402
    CleanupPlan,
    CATEGORY_DIRS,
    disk_status,
    execute_cleanup,
    plan_cleanup,
)


def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / (1024 ** 3):.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / (1024 ** 2):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _print_plan(plan: CleanupPlan, dry_run: bool) -> None:
    """Print a table of planned deletions."""
    col_w = 22

    header = f"{'Category':<{col_w}}  {'Files':>10}  {'Size':>12}"
    print(header)
    print("-" * len(header))

    for category in CATEGORY_DIRS:
        cat_plan = plan.categories.get(category)
        if cat_plan:
            count = len(cat_plan.files)
            size = _fmt_bytes(cat_plan.total_bytes)
        else:
            count = 0
            size = "0 B"
        print(f"{category:<{col_w}}  {count:>10}  {size:>12}")

    print("-" * len(header))
    total_size = _fmt_bytes(plan.total_bytes)
    print(f"{'Total':<{col_w}}  {plan.total_files:>10}  {total_size:>12}")
    print()

    if plan.skipped_floor:
        print(f"Skipped (younger than floor): {plan.skipped_floor} file(s).")
    if plan.skipped_traversal:
        print(f"Skipped (path traversal blocked): {plan.skipped_traversal} file(s).")
    if plan.skipped_floor or plan.skipped_traversal:
        print()

    if dry_run:
        if plan.total_files == 0:
            print("Dry run. Nothing to remove.")
        else:
            print("Dry run. Run with --apply to delete.")
    else:
        if plan.total_files == 0:
            print("Nothing to remove.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Storage rotation: preview or apply retention rules.",
    )
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config.ini"),
        help="Path to config.ini (default: repo root config.ini)",
    )
    parser.add_argument(
        "--data",
        default=str(REPO_ROOT / "output_data"),
        help="Path to output_data/ directory (default: repo root output_data/)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Delete files. Without this flag, runs dry-run only.",
    )
    args = parser.parse_args(argv)

    # --- Load config ---
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(config_path)

    # --- Validate output_data/ ---
    data_root = Path(args.data)
    if not data_root.exists():
        print(f"output_data/ not found at {data_root}. Nothing to do.", file=sys.stderr)
        return 1
    if not data_root.is_dir():
        print(f"{data_root} is not a directory.", file=sys.stderr)
        return 1

    # --- Disk status ---
    status, reason = disk_status(cfg, data_root)
    print(f"Disk: {status} — {reason}")
    print()

    # --- Plan ---
    now = datetime.now(timezone.utc)
    plan = plan_cleanup(cfg, data_root, now)

    dry_run = not args.apply
    _print_plan(plan, dry_run=dry_run)

    if dry_run:
        return 0

    # --- Apply ---
    if plan.total_files == 0:
        return 0

    result = execute_cleanup(plan, audit_root=data_root)

    freed = _fmt_bytes(result.total_bytes_freed)
    print(
        f"Removed {result.total_removed} files ({freed})."
    )
    if result.total_errors:
        print(
            f"{result.total_errors} error(s). Check the audit log: "
            f"{result.audit_path}",
            file=sys.stderr,
        )
    if result.audit_path:
        print(f"Audit log: {result.audit_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
