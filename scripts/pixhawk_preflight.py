#!/usr/bin/env python3
"""
pixhawk_preflight.py: Validate live ArduPilot params against a profile manifest.

Usage:
    python scripts/pixhawk_preflight.py --profile ugv --conn /dev/ttyACM0
    python scripts/pixhawk_preflight.py --profile drone_10in --conn udp:127.0.0.1:14550

Exit codes:
    0  All required params pass (warnings allowed).
    1  One or more required params fail.
    2  Connection failure or timeout before params collected.

Read-only — never writes params to the flight controller.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_PROFILES_DIR = _REPO_ROOT / "hydra_detect" / "profiles"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class PreflightResult:
    """Single parameter check result."""

    __slots__ = ("name", "status", "actual", "expected", "message")

    def __init__(
        self,
        name: str,
        status: str,
        actual: float | None,
        expected: Any,
        message: str,
    ) -> None:
        self.name = name
        self.status = status        # "PASS", "FAIL", or "WARN"
        self.actual = actual
        self.expected = expected
        self.message = message

    def __repr__(self) -> str:
        return (
            f"PreflightResult(name={self.name!r}, status={self.status!r}, "
            f"actual={self.actual!r}, expected={self.expected!r})"
        )


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def _parse_manifest_yaml(path: Path) -> dict:
    """Load YAML from path. Raises yaml.YAMLError on parse failure."""
    with open(path, "r") as fh:
        return yaml.safe_load(fh)


def _validate_manifest_schema(data: dict, path: Path) -> None:
    """Raise ValueError if required top-level keys are missing."""
    for key in ("profile", "firmware"):
        if key not in data:
            raise ValueError(
                f"Manifest {path} missing required key '{key}'"
            )


def load_manifest(profile: str) -> dict:
    """Load and validate a profile manifest from hydra_detect/profiles/<profile>/pixhawk_prereqs.yaml.

    Raises:
        FileNotFoundError: Profile directory or manifest file does not exist.
        ValueError: Manifest is missing required schema keys.
        yaml.YAMLError: YAML parse failure.
    """
    manifest_path = _PROFILES_DIR / profile / "pixhawk_prereqs.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No manifest for profile '{profile}' at {manifest_path}"
        )
    data = _parse_manifest_yaml(manifest_path)
    _validate_manifest_schema(data, manifest_path)
    # Ensure expected list keys exist
    data.setdefault("required", [])
    data.setdefault("recommended", [])
    data.setdefault("stream_rates", {})
    return data


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------

def validate_params(manifest: dict, live_params: dict[str, float]) -> list[PreflightResult]:
    """Compare live ArduPilot params against a manifest.

    Required params: PASS if value matches expected exactly; FAIL otherwise.
    Recommended params: PASS if matches; WARN if not.
    Stream rates: PASS if live_value >= expected; FAIL if below or missing.

    Args:
        manifest:    Loaded manifest dict (from load_manifest()).
        live_params: Dict of {param_name: float_value} from the flight controller.

    Returns:
        List of PreflightResult, one per checked param.
    """
    results: list[PreflightResult] = []

    # Required params — exact match
    for entry in manifest.get("required", []):
        name = entry["name"]
        expected = entry["expected"]
        if name not in live_params:
            results.append(PreflightResult(
                name=name,
                status="FAIL",
                actual=None,
                expected=expected,
                message=f"{name} missing from flight controller params",
            ))
        else:
            actual = live_params[name]
            if _values_match(actual, expected):
                results.append(PreflightResult(
                    name=name,
                    status="PASS",
                    actual=actual,
                    expected=expected,
                    message=f"{name} = {_fmt(actual)}",
                ))
            else:
                results.append(PreflightResult(
                    name=name,
                    status="FAIL",
                    actual=actual,
                    expected=expected,
                    message=f"{name} = {_fmt(actual)} (expected {expected})",
                ))

    # Recommended params — mismatch becomes WARN, not FAIL
    for entry in manifest.get("recommended", []):
        name = entry["name"]
        expected = entry["expected"]
        if name not in live_params:
            results.append(PreflightResult(
                name=name,
                status="WARN",
                actual=None,
                expected=expected,
                message=f"{name} missing (recommended {expected})",
            ))
        else:
            actual = live_params[name]
            if _values_match(actual, expected):
                results.append(PreflightResult(
                    name=name,
                    status="PASS",
                    actual=actual,
                    expected=expected,
                    message=f"{name} = {_fmt(actual)}",
                ))
            else:
                results.append(PreflightResult(
                    name=name,
                    status="WARN",
                    actual=actual,
                    expected=expected,
                    message=f"{name} = {_fmt(actual)} (recommended {expected})",
                ))

    # Stream rates — minimum threshold check (live >= expected)
    for rate_name, min_hz in manifest.get("stream_rates", {}).items():
        if rate_name not in live_params:
            results.append(PreflightResult(
                name=rate_name,
                status="FAIL",
                actual=None,
                expected=min_hz,
                message=f"{rate_name} missing (expected ≥ {min_hz})",
            ))
        else:
            actual = live_params[rate_name]
            if actual >= min_hz:
                results.append(PreflightResult(
                    name=rate_name,
                    status="PASS",
                    actual=actual,
                    expected=min_hz,
                    message=f"{rate_name} = {_fmt(actual)}",
                ))
            else:
                results.append(PreflightResult(
                    name=rate_name,
                    status="FAIL",
                    actual=actual,
                    expected=min_hz,
                    message=f"{rate_name} = {_fmt(actual)} (expected ≥ {min_hz})",
                ))

    return results


def _values_match(actual: float, expected: Any) -> bool:
    """Return True if actual float matches expected value within tolerance."""
    try:
        return abs(actual - float(expected)) < 0.01
    except (TypeError, ValueError):
        return False


def _fmt(value: float | None) -> str:
    """Format a param value for display. Integers shown as int, floats with 1 decimal."""
    if value is None:
        return "None"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(profile: str, firmware: str, results: list[PreflightResult]) -> str:
    """Format a human-readable preflight report string."""
    lines = []
    lines.append(f"PIXHAWK PREFLIGHT  profile={profile}  firmware={firmware}")
    lines.append("-" * 48)
    for r in results:
        lines.append(f"[{r.status:<4}] {r.message}")
    lines.append("-" * 48)
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")
    lines.append(f"Summary: {pass_count} PASS, {fail_count} FAIL, {warn_count} WARN")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Exit code
# ---------------------------------------------------------------------------

def compute_exit_code(results: list[PreflightResult]) -> int:
    """Return 0 if all PASS/WARN, 1 if any FAIL."""
    if any(r.status == "FAIL" for r in results):
        return 1
    return 0


# ---------------------------------------------------------------------------
# MAVLink param collection
# ---------------------------------------------------------------------------

def collect_params(
    conn: Any,
    timeout: float = 30.0,
    quiescent: float = 3.0,
) -> dict[str, float]:
    """Request and collect all parameters from a MAVLink connection.

    Sends PARAM_REQUEST_LIST, then collects PARAM_VALUE messages until no new
    params arrive for `quiescent` seconds or `timeout` seconds total.

    Args:
        conn:      pymavlink connection (mavutil.mavlink_connection result).
        timeout:   Hard timeout in seconds before giving up.
        quiescent: Seconds of silence that signals collection is complete.

    Returns:
        Dict of {param_name: float_value}.
    """
    params: dict[str, float] = {}
    conn.mav.param_request_list_send(conn.target_system, conn.target_component)

    deadline = time.monotonic() + timeout
    last_new = time.monotonic()

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        # Use a short recv window so we can check quiescent condition
        msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=min(0.5, remaining))
        if msg is None:
            # No message in window — check quiescent
            if time.monotonic() - last_new >= quiescent:
                break
            continue
        # Strip null bytes from MAVLink fixed-length param_id strings
        param_name = msg.param_id.rstrip("\x00").strip()
        if param_name and param_name not in params:
            params[param_name] = float(msg.param_value)
            last_new = time.monotonic()

    return params


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Pixhawk params against a Hydra profile manifest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--profile",
        required=True,
        choices=["ugv", "usv", "drone_10in"],
        help="Vehicle profile to validate against.",
    )
    parser.add_argument(
        "--conn",
        default="/dev/ttyACM0",
        help="MAVLink connection string (default: /dev/ttyACM0).",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate (default: 115200, ignored for UDP/TCP connections).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Seconds to wait for param collection (default: 30).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI main — returns exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Load manifest before connecting so a bad profile arg fails fast
    try:
        manifest = load_manifest(args.profile)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Import pymavlink here so the rest of the module is importable without it
    try:
        from pymavlink import mavutil
    except ImportError:
        print("ERROR: pymavlink not installed. Run: pip install pymavlink", file=sys.stderr)
        return 2

    # Connect
    try:
        conn = mavutil.mavlink_connection(args.conn, baud=args.baud)
        print(f"Connecting to {args.conn} ...", flush=True)
        conn.wait_heartbeat(timeout=args.timeout)
    except Exception as exc:
        print(f"ERROR: Connection failed: {exc}", file=sys.stderr)
        return 2

    print(f"Connected: system {conn.target_system} component {conn.target_component}")
    print("Requesting params ...", flush=True)

    try:
        live_params = collect_params(conn, timeout=args.timeout)
    except Exception as exc:
        print(f"ERROR: Param collection failed: {exc}", file=sys.stderr)
        return 2

    if not live_params:
        print("ERROR: No params received (timeout).", file=sys.stderr)
        return 2

    print(f"Received {len(live_params)} params.")

    results = validate_params(manifest, live_params)
    report = format_report(manifest["profile"], manifest["firmware"], results)
    print(report)

    return compute_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
