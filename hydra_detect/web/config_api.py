"""Config file read/write with file locking for Jetson safety."""

from __future__ import annotations

import configparser
import fcntl
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generate_api_token() -> str:
    """Generate a random API token for this Jetson instance."""
    return secrets.token_hex(32)

# Default config path — can be overridden by pipeline at startup
_config_path: Path | None = None

# Fields that require a service restart to take effect
RESTART_REQUIRED_FIELDS = {
    "web": {"host", "port"},
    "mavlink": {"connection_string", "baud", "source_system"},
    "camera": {"source", "width", "height"},
    "detector": {"yolo_model"},
}

# Fields that must be redacted in GET responses
REDACTED_FIELDS = {
    "web": {"api_token"},
    "rf_homing": {"kismet_pass"},
}

REDACTED_VALUE = "***"
MAX_BODY_SIZE = 65536  # 64KB


def set_config_path(path: Path | str) -> None:
    """Set the config.ini path (called by pipeline at startup)."""
    global _config_path
    _config_path = Path(path)


def get_config_path() -> Path:
    """Return the current config.ini path."""
    if _config_path is None:
        return Path("config.ini")
    return _config_path


def read_config() -> dict[str, dict[str, str]]:
    """Read config.ini and return as nested dict. Redacts sensitive fields."""
    path = get_config_path()
    config = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    config.read(path)

    result: dict[str, dict[str, str]] = {}
    for section in config.sections():
        result[section] = dict(config[section])
        # Redact sensitive fields
        if section in REDACTED_FIELDS:
            for field in REDACTED_FIELDS[section]:
                if field in result[section] and result[section][field]:
                    result[section][field] = REDACTED_VALUE

    return result


def write_config(updates: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Merge updates into config.ini with atomic write and file locking.

    Returns a dict with 'restart_required' and 'skipped' lists.
    """
    path = get_config_path()
    restart_needed: list[str] = []
    skipped: list[str] = []

    # Read current config
    config = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    config.read(path)

    # Apply updates
    for section, fields in updates.items():
        if not isinstance(fields, dict):
            continue
        if not config.has_section(section):
            skipped.append(f"{section} (unknown section)")
            continue
        for key, value in fields.items():
            if not isinstance(value, str):
                value = str(value)
            # Skip redacted placeholder — preserve existing value
            if section in REDACTED_FIELDS and key in REDACTED_FIELDS[section]:
                if value == REDACTED_VALUE:
                    continue
            if not config.has_option(section, key):
                skipped.append(f"{section}.{key} (unknown field)")
                continue
            old_value = config.get(section, key)
            if old_value != value:
                config.set(section, key, value)
                # Check if restart required
                if section in RESTART_REQUIRED_FIELDS and key in RESTART_REQUIRED_FIELDS[section]:
                    restart_needed.append(f"{section}.{key}")

    # Backup existing file
    bak_path = Path(str(path) + ".bak")
    if path.exists():
        shutil.copy2(path, bak_path)

    # Write config with file locking.
    # NOTE: os.replace() / os.rename() fail on Docker bind mounts (EXDEV —
    # cross-device link).  Instead, lock-then-write directly to the target.
    lock_fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        with open(path, "w") as f:
            config.write(f)
        logger.info("Config written to %s (%d fields updated)", path, len(restart_needed))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    return {"restart_required": restart_needed, "skipped": skipped}


def restore_backup() -> bool:
    """Restore config.ini from config.ini.bak. Returns True on success."""
    path = get_config_path()
    bak_path = Path(str(path) + ".bak")
    if not bak_path.exists():
        return False
    shutil.copy2(bak_path, path)
    logger.info("Config restored from backup: %s", bak_path)
    return True


def has_backup() -> bool:
    """Check if a config backup exists."""
    path = get_config_path()
    return Path(str(path) + ".bak").exists()
