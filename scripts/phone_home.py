#!/usr/bin/env python3
"""phone_home.py — send a health telemetry payload to the configured collector.

Usage:
    python scripts/phone_home.py [--config PATH] [--dry-run]

Options:
    --config PATH   Path to config.ini (default: config.ini in current dir)
    --dry-run       Print the payload as JSON without sending

Exit codes:
    0   Payload sent successfully
    1   Send failed — payload queued locally for the next run
    2   Configuration error (telemetry disabled, missing url, bad config)
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import sys
from pathlib import Path

# Ensure the repo root is on sys.path when run directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hydra_detect.telemetry.phone_home import (
    build_payload,
    flush_queue,
    queue_payload,
    send_payload,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s phone_home: %(message)s",
)
logger = logging.getLogger("phone_home")


def _load_config(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    read = cfg.read(path)
    if not read:
        raise FileNotFoundError(f"Config not found: {path}")
    return cfg


def _check_enabled(cfg: configparser.ConfigParser) -> tuple[str, str]:
    """Validate the [telemetry] section and return (url, api_token).

    Raises SystemExit(2) on any config problem.
    """
    try:
        enabled = cfg.getboolean("telemetry", "enabled", fallback=False)
    except ValueError as exc:
        logger.error("[telemetry] enabled must be true or false: %s", exc)
        sys.exit(2)

    if not enabled:
        logger.info("[telemetry] enabled = false — nothing to send (set enabled = true to activate)")
        sys.exit(2)

    opt_out = cfg.getboolean("telemetry", "opt_out", fallback=False)
    if opt_out:
        logger.info("[telemetry] opt_out = true — skipping send")
        sys.exit(2)

    url = cfg.get("telemetry", "collector_url", fallback="").strip()
    if not url:
        logger.error(
            "[telemetry] collector_url is empty — set it to the collector endpoint"
        )
        sys.exit(2)

    api_token = cfg.get("telemetry", "api_token", fallback="").strip()
    return url, api_token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send Hydra unit health telemetry to the configured collector.",
        epilog="Collector side is not defined yet — see issue #153.",
    )
    parser.add_argument(
        "--config",
        default="config.ini",
        metavar="PATH",
        help="Path to config.ini (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payload as JSON without sending",
    )
    args = parser.parse_args(argv)

    # Locate repo root relative to the config file location so output_data/
    # paths resolve correctly regardless of cwd.
    config_path = Path(args.config).resolve()
    root = config_path.parent

    try:
        cfg = _load_config(str(config_path))
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2

    if args.dry_run:
        # Dry-run: build and print, no config-enabled check.
        payload = build_payload(cfg, root)
        print(json.dumps(payload, indent=2))
        return 0

    # Live send path — enforce config guards.
    try:
        url, api_token = _check_enabled(cfg)
    except SystemExit as exc:
        return int(exc.code)

    # Flush any queued payloads before sending the current one.
    flush_queue(root, url, api_token)

    payload = build_payload(cfg, root)
    result = send_payload(url, payload, api_token)

    if result.ok:
        logger.info("sent OK (HTTP %s)", result.status_code)
        return 0
    else:
        logger.warning(
            "send failed (HTTP %s): %s — queuing locally",
            result.status_code,
            result.error,
        )
        queue_payload(root, payload)
        return 1


if __name__ == "__main__":
    sys.exit(main())
