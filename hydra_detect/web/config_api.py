"""Config file read/write with file locking for Jetson safety."""

from __future__ import annotations

import configparser
import fcntl
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)
audit_log = logging.getLogger("hydra.audit")


def generate_api_token() -> str:
    """Generate a random API token for this Jetson instance."""
    return secrets.token_hex(32)

# Default config path — can be overridden by pipeline at startup
_config_path: Path | None = None

# -- Engagement safety lock ------------------------------------------------

# Fields locked while autonomous engagement is active.
# None = ALL keys in the section are locked; a set = only those keys.
SAFETY_LOCKED_FIELDS: dict[str, set[str] | None] = {
    "autonomous": None,  # entire section locked
    "servo_tracking": {"strike_channel", "strike_pwm_fire", "strike_pwm_safe", "pan_channel"},
}

_engagement_active_cb: Callable[[], bool] | None = None


def set_engagement_check(cb: Callable[[], bool]) -> None:
    """Register callback that returns True when safety config should be locked."""
    global _engagement_active_cb
    _engagement_active_cb = cb

# Fields that require a service restart to take effect
RESTART_REQUIRED_FIELDS = {
    "web": {"host", "port"},
    "mavlink": {"connection_string", "baud", "source_system"},
    "camera": {"source", "width", "height"},
    "detector": set(),
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
    locked: list[str] = []

    # Check if engagement is active (determines safety field locking)
    engagement_active = (
        _engagement_active_cb is not None and _engagement_active_cb()
    )

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
            # Check safety lock during active engagement
            if engagement_active and section in SAFETY_LOCKED_FIELDS:
                section_keys = SAFETY_LOCKED_FIELDS[section]
                if section_keys is None or key in section_keys:
                    reason = f"{section}.{key} (locked — active engagement)"
                    locked.append(reason)
                    skipped.append(reason)
                    audit_log.warning(
                        "CONFIG WRITE REJECTED (engagement active): %s.%s",
                        section, key,
                    )
                    continue
            old_value = config.get(section, key)
            if old_value != value:
                config.set(section, key, value)
                audit_log.info("CONFIG WRITE: %s.%s = %s", section, key, value)
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
            f.flush()
            os.fsync(f.fileno())
        logger.info("Config written to %s (%d fields updated)", path, len(restart_needed))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    return {"restart_required": restart_needed, "skipped": skipped, "locked": locked}


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


def backup_on_boot() -> None:
    """Copy current config.ini to config.ini.bak on successful boot.

    Only backs up if the config parses successfully — preserves
    last-known-good .bak when current config is corrupted.
    """
    path = get_config_path()
    if not path.exists():
        return
    # Only backup if config parses successfully
    cfg = configparser.ConfigParser()
    try:
        cfg.read(path)
        if not cfg.sections():
            logger.warning("Config has no sections — skipping backup")
            return
    except configparser.Error:
        logger.warning(
            "Config parse error — skipping backup to preserve "
            "last-known-good .bak"
        )
        return
    bak_path = Path(str(path) + ".bak")
    shutil.copy2(path, bak_path)
    logger.info("Config backed up to %s", bak_path)


def has_factory() -> bool:
    """Check if factory defaults file exists."""
    path = get_config_path()
    return Path(str(path) + ".factory").exists()


def restore_factory() -> bool:
    """Restore config.ini from config.ini.factory. Returns True on success."""
    path = get_config_path()
    factory_path = Path(str(path) + ".factory")
    if not factory_path.exists():
        return False
    # Back up current config before overwriting
    bak_path = Path(str(path) + ".bak")
    if path.exists():
        shutil.copy2(path, bak_path)
    shutil.copy2(factory_path, path)
    logger.info("Config restored from factory defaults: %s", factory_path)
    return True
