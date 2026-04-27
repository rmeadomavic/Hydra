#!/usr/bin/env python3
"""Platform Setup — assigns a permanent identity to a Hydra unit.

Run this once per unit before operational deployment. Generates callsign,
API token, and dashboard password. The password is printed once. Write it
down. It is not shown again.

Interactive mode (default):
    python scripts/platform_setup.py

Non-interactive mode (scripted / golden-image flashing):
    python scripts/platform_setup.py --unit 3 --profile ugv --yes

The script writes [identity] to config.ini. It does not rename any existing
setup scripts — #151 handles renames.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the repo root is on sys.path when invoked directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hydra_detect.identity import (  # noqa: E402
    generate_identity,
    write_identity_to_config,
    load_identity_from_config,
)

_DEFAULT_CONFIG = _REPO_ROOT / "config.ini"

_PROFILES = ["drone", "usv", "ugv", "fw"]


def _banner(lines: list[str]) -> None:
    """Print a plain-text banner block."""
    width = max(len(line) for line in lines) + 4
    print("-" * width)
    for line in lines:
        print(f"  {line}")
    print("-" * width)


def _print_unit_ready(callsign: str, hostname: str, password: str) -> None:
    _banner([
        "UNIT-READY",
        f"Callsign:  {callsign}",
        f"Hostname:  {hostname}",
        "API token: generated (hidden)",
        f"Password:  {password}",
        "",
        "Save the password now. It is not shown again.",
    ])


def _confirm_overwrite(existing_callsign: str) -> bool:
    """Prompt operator before overwriting an existing identity."""
    print(f"\nThis unit already has an identity: {existing_callsign}")
    answer = input("Overwrite? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def interactive_setup(config_path: Path) -> int:
    """Prompt for unit number and profile, then generate identity."""
    print("\nHydra Platform Setup")
    print("Assign callsign, API token, and dashboard password to this unit.")
    print(f"Config: {config_path}\n")

    existing = load_identity_from_config(config_path)
    if existing is not None:
        if not _confirm_overwrite(existing.callsign):
            print("Cancelled.")
            return 0

    # Unit number
    while True:
        raw = input("Unit number (1-99): ").strip()
        try:
            unit_number = int(raw)
            if 1 <= unit_number <= 99:
                break
            print("  Enter a number between 1 and 99.")
        except ValueError:
            print("  Enter a whole number.")

    # Profile
    profile_list = ", ".join(_PROFILES)
    while True:
        raw = input(f"Profile [{profile_list}]: ").strip().lower()
        if raw in _PROFILES:
            profile = raw
            break
        print(f"  Choose one of: {profile_list}")

    # Confirm
    preview_callsign = f"HYDRA-{unit_number:02d}-{profile.upper()}"
    print(f"\nCallsign will be: {preview_callsign}")
    answer = input("Proceed? [Y/n] ").strip().lower()
    if answer in ("n", "no"):
        print("Cancelled.")
        return 0

    return _generate_and_write(unit_number, profile, config_path)


def non_interactive_setup(
    unit_number: int,
    profile: str,
    config_path: Path,
) -> int:
    """Generate identity without prompts. Overwrites any existing identity."""
    return _generate_and_write(unit_number, profile, config_path)


def _generate_and_write(unit_number: int, profile: str, config_path: Path) -> int:
    """Generate identity, write to config, print UNIT-READY banner."""
    try:
        identity, plaintext_password = generate_identity(
            unit_number=unit_number,
            profile=profile,
            repo_root=_REPO_ROOT,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        write_identity_to_config(identity, config_path)
    except OSError as exc:
        print(f"ERROR: could not write config: {exc}", file=sys.stderr)
        return 1

    _print_unit_ready(identity.callsign, identity.hostname, plaintext_password)

    # Securely zero the password reference so it is not held in memory.
    del plaintext_password

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hydra Platform Setup. Assign unit identity.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive (default):
  python scripts/platform_setup.py

  # Non-interactive / scripted:
  python scripts/platform_setup.py --unit 3 --profile ugv --yes

  # Custom config path:
  python scripts/platform_setup.py --config /data/hydra/config.ini --unit 1 --profile drone --yes
""",
    )
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG),
        help="Path to config.ini (default: repo root config.ini)",
    )
    parser.add_argument(
        "--unit",
        type=int,
        metavar="N",
        help="Unit number 1-99 (required for --yes mode)",
    )
    parser.add_argument(
        "--profile",
        choices=_PROFILES,
        help=f"Platform profile: {', '.join(_PROFILES)}",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive: skip all prompts (requires --unit and --profile)",
    )

    args = parser.parse_args()
    config_path = Path(args.config)

    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1

    if args.yes:
        if args.unit is None or args.profile is None:
            print(
                "ERROR: --yes requires both --unit and --profile",
                file=sys.stderr,
            )
            return 1
        if not 1 <= args.unit <= 99:
            print("ERROR: --unit must be 1-99", file=sys.stderr)
            return 1
        return non_interactive_setup(args.unit, args.profile, config_path)

    return interactive_setup(config_path)


if __name__ == "__main__":
    sys.exit(main())
