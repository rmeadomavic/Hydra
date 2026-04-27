"""Config schema migration runner.

Runs pending migrations N->N+1 against config.ini. Atomic write via
tempfile+fsync+rename. Backs up the config before any change.

Usage (from __main__.py before pipeline start)::

    from hydra_detect.config_migrate import run_migrations, MigrationError

    try:
        result = run_migrations(config_path)
        if result.applied:
            logger.info(
                "Config migrated v%d -> v%d | applied: %s | backup: %s",
                result.from_version, result.to_version,
                result.applied, result.backup_path,
            )
    except MigrationError as exc:
        logger.critical("Config migration failed; refusing to start: %s", exc)
        sys.exit(1)

Migration files live in hydra_detect/migrations/ and are named NNN_description.py.
Each must export:
    from_version: int
    to_version: int
    def migrate(cfg: configparser.ConfigParser) -> None: ...

The runner loads them in from_version order and applies all pending steps to reach
CURRENT_SCHEMA_VERSION. MigrationError leaves the config file untouched.
"""

from __future__ import annotations

import configparser
import importlib.util
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)

# Bump this when adding a new migration file.
CURRENT_SCHEMA_VERSION: int = 1

# Migration modules directory (monkeypatched in tests to point at fixtures).
_MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class MigrationError(Exception):
    """Raised when a migration fails. Config file is left untouched."""


@dataclass
class MigrationResult:
    """Structured result returned by run_migrations()."""
    applied: list[str] = field(default_factory=list)
    from_version: int = 0
    to_version: int = 0
    backup_path: Path | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_schema_version(cfg: configparser.ConfigParser) -> int:
    """Return the schema_version from [meta], defaulting to 0 if absent."""
    if not cfg.has_section("meta"):
        return 0
    raw = cfg.get("meta", "schema_version", fallback="0").strip()
    try:
        return int(raw)
    except ValueError:
        raise MigrationError(
            f"[meta] schema_version is not an integer: {raw!r}"
        )


def _load_migration_modules(migrations_dir: Path) -> list[tuple[int, int, str, ModuleType]]:
    """Discover and load migration modules from migrations_dir.

    Returns a list of (from_version, to_version, filename_stem, module) tuples,
    sorted by from_version ascending.

    Raises MigrationError if a module is missing required attributes.
    """
    modules: list[tuple[int, int, str, ModuleType]] = []

    candidates = sorted(migrations_dir.glob("*.py"))
    for path in candidates:
        if path.name.startswith("_"):
            continue  # skip __init__.py etc.

        spec = importlib.util.spec_from_file_location(
            f"hydra_detect.migrations.{path.stem}", path
        )
        if spec is None or spec.loader is None:
            raise MigrationError(f"Cannot load migration module: {path}")

        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception as exc:
            raise MigrationError(
                f"Error executing migration module {path.name}: {exc}"
            ) from exc

        # Validate required attributes
        missing = [
            attr for attr in ("from_version", "to_version", "migrate")
            if not hasattr(mod, attr)
        ]
        if missing:
            raise MigrationError(
                f"Migration {path.name} is missing required attributes: "
                f"{', '.join(missing)}"
            )

        if not callable(mod.migrate):
            raise MigrationError(
                f"Migration {path.name}: 'migrate' must be callable"
            )

        modules.append((mod.from_version, mod.to_version, path.stem, mod))

    modules.sort(key=lambda t: t[0])
    return modules


def _backup_config(config_path: Path) -> Path:
    """Copy config_path to config_path.premigrate.<ISO8601>. Returns backup path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = config_path.parent / f"{config_path.name}.premigrate.{ts}"
    shutil.copy2(config_path, backup_path)
    return backup_path


def _atomic_write(config_path: Path, cfg: configparser.ConfigParser) -> None:
    """Write cfg to config_path atomically: tempfile → fsync → os.replace.

    Matches the pattern in config_api.py:write_config().
    On failure, the .tmp file is cleaned up and the original is untouched.

    Advisory file locking (fcntl) is used on Linux/Jetson. On Windows (dev
    environment) fcntl is unavailable and os.replace requires the lock_fd to
    be closed first, so we release the fd before replacing.
    """
    tmp_path = config_path.parent / (config_path.name + ".tmp")
    try:
        try:
            import fcntl
            _have_fcntl = True
        except ImportError:
            _have_fcntl = False

        if _have_fcntl:
            # Linux/Jetson: use advisory exclusive lock while writing.
            lock_fd = os.open(str(config_path), os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                with open(tmp_path, "w") as f:
                    cfg.write(f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, config_path)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        else:
            # Windows dev environment: no fcntl; write and replace directly.
            with open(tmp_path, "w") as f:
                cfg.write(f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, config_path)
    finally:
        # Clean up orphan .tmp if os.replace never ran.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_migrations(config_path: Path) -> MigrationResult:
    """Load config, detect version, apply pending migrations, write back atomically.

    Args:
        config_path: Path to config.ini.

    Returns:
        MigrationResult with applied migration names, from/to versions, and
        backup path (None if no migrations were needed).

    Raises:
        MigrationError: Migration failed. Config file is left untouched.
    """
    config_path = Path(config_path)

    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(config_path)

    current_version = _read_schema_version(cfg)
    result = MigrationResult(
        from_version=current_version,
        to_version=current_version,
    )

    if current_version >= CURRENT_SCHEMA_VERSION:
        logger.debug(
            "Config schema at v%d; no migrations needed",
            current_version,
        )
        return result

    # Discover migrations
    all_modules = _load_migration_modules(_MIGRATIONS_DIR)

    # Filter to only pending migrations (from_version >= current_version)
    pending = [
        (fv, tv, stem, mod)
        for fv, tv, stem, mod in all_modules
        if fv >= current_version
    ]

    if not pending:
        logger.debug("No pending migrations for v%d", current_version)
        return result

    # Validate the chain is contiguous (no gaps between from/to versions)
    chain_ver = current_version
    for fv, tv, stem, _ in pending:
        if fv != chain_ver:
            raise MigrationError(
                f"Migration chain gap: expected from_version={chain_ver}, "
                f"got {stem} (from_version={fv})"
            )
        chain_ver = tv

    # Backup before any change
    if config_path.exists():
        result.backup_path = _backup_config(config_path)
        logger.info("Config backed up to %s", result.backup_path)

    # Apply migrations in sequence
    applied: list[str] = []
    try:
        for fv, tv, stem, mod in pending:
            logger.info(
                "Applying migration %s: v%d → v%d", stem, fv, tv
            )
            try:
                mod.migrate(cfg)
            except Exception as exc:
                raise MigrationError(
                    f"Migration {stem} failed: {exc}"
                ) from exc
            applied.append(stem)

        # Update schema_version in config
        if not cfg.has_section("meta"):
            cfg.add_section("meta")
        cfg.set("meta", "schema_version", str(chain_ver))

        # Atomic write
        _atomic_write(config_path, cfg)

    except MigrationError:
        # Restore from backup so the original is untouched
        if result.backup_path is not None and result.backup_path.exists():
            shutil.copy2(result.backup_path, config_path)
        raise

    result.applied = applied
    result.to_version = chain_ver
    return result
