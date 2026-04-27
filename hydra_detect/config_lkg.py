"""Last-known-good (LKG) config snapshot for Hydra Detect.

Provides snapshot and restore functions for config.ini.lkg.
Called after a clean boot + healthcheck pass. Restore is available
to the CLI; a dashboard UI button is tracked separately (#75).

Atomic writes only. No network calls.
"""

from __future__ import annotations

import configparser
import logging
import os
import shutil
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _lkg_path(config_path: Path | str) -> Path:
    """Return the .lkg path for a given config path."""
    return Path(str(config_path) + ".lkg")


def snapshot_if_healthy(
    config_path: Path | str,
    health_check_fn: Callable[[], bool],
) -> bool:
    """Copy config.ini to config.ini.lkg if health_check_fn returns True.

    Uses an atomic write pattern (tmp -> fsync -> os.replace) so a crash
    during the snapshot cannot corrupt the existing .lkg.

    Parameters
    ----------
    config_path:
        Path to the active config.ini.
    health_check_fn:
        Zero-argument callable that returns True when the system is healthy.
        Must not block for more than a few seconds. Called once.

    Returns
    -------
    True if the snapshot was written, False if health check failed or an
    error occurred.
    """
    config_path = Path(config_path)
    lkg = _lkg_path(config_path)

    if not config_path.exists():
        logger.warning("snapshot_if_healthy: config path %s does not exist", config_path)
        return False

    # Verify config parses cleanly before snapshotting.
    cfg = configparser.ConfigParser()
    try:
        cfg.read(config_path)
        if not cfg.sections():
            logger.warning("snapshot_if_healthy: config has no sections — skipping snapshot")
            return False
    except configparser.Error as exc:
        logger.warning("snapshot_if_healthy: config parse error — skipping snapshot: %s", exc)
        return False

    try:
        healthy = health_check_fn()
    except Exception as exc:
        logger.warning("snapshot_if_healthy: health check raised — skipping snapshot: %s", exc)
        return False

    if not healthy:
        logger.debug("snapshot_if_healthy: health check returned False — no snapshot")
        return False

    # Strip [identity] before snapshotting. The LKG must never hold
    # credentials. If it did, restore_lkg would silently roll back the
    # active API token and password hash to the snapshot-time values,
    # which can give stale credentials an extra life.
    had_identity = cfg.has_section("identity")
    if had_identity:
        cfg.remove_section("identity")

    tmp_path = Path(str(lkg) + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            cfg.write(f)
            f.flush()
            # fsync for durability. Best-effort; some filesystems ignore it.
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, lkg)
        if had_identity:
            logger.info("LKG snapshot written to %s ([identity] stripped)", lkg)
        else:
            logger.info("LKG snapshot written to %s", lkg)
        return True
    except OSError as exc:
        logger.warning("snapshot_if_healthy: write failed: %s", exc)
        return False
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def restore_lkg(config_path: Path | str) -> bool:
    """Restore config.ini from config.ini.lkg using an atomic write.

    Parameters
    ----------
    config_path:
        Path to the active config.ini (will be overwritten).

    Returns
    -------
    True if the restore succeeded, False if no .lkg exists or an error occurred.
    """
    config_path = Path(config_path)
    lkg = _lkg_path(config_path)

    if not lkg.exists():
        logger.info("restore_lkg: no .lkg snapshot found at %s", lkg)
        return False

    tmp_path = Path(str(config_path) + ".tmp")
    try:
        shutil.copy2(lkg, tmp_path)
        try:
            with open(tmp_path, "r+b") as f:
                os.fsync(f.fileno())
        except OSError:
            pass  # non-fatal
        os.replace(tmp_path, config_path)
        logger.warning("Config restored from LKG snapshot: %s -> %s", lkg, config_path)
        return True
    except OSError as exc:
        logger.warning("restore_lkg: restore failed: %s", exc)
        return False
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def has_lkg(config_path: Path | str) -> bool:
    """Return True if a .lkg snapshot exists."""
    return _lkg_path(config_path).exists()
