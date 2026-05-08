"""Config file read/write with file locking for Jetson safety."""

from __future__ import annotations

import configparser
import datetime
import logging
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl  # POSIX only — Linux/Jetson production target.
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover — Windows dev workstations
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

from hydra_detect.config_schema import SCHEMA, FieldType

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


# Fields that require a service restart to take effect.
# Note: `detector.yolo_model` hot-swaps via `switch_model()` — intentionally
# excluded. `yolo_imgsz` is included because it rebuilds the inference engine.
RESTART_REQUIRED_FIELDS = {
    "web": {"host", "port"},
    "mavlink": {"connection_string", "baud", "source_system"},
    "camera": {"source", "width", "height"},
    "detector": {"yolo_imgsz"},
}

# Fields that must be redacted in GET responses
REDACTED_FIELDS = {
    "web": {"api_token", "web_password"},
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
    updated: list[str] = []
    changed = False

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
                changed = True
                updated.append(f"{section}.{key}")
                is_redacted = (
                    section in REDACTED_FIELDS
                    and key in REDACTED_FIELDS[section]
                )
                log_value = "[REDACTED]" if is_redacted else value
                audit_log.info("CONFIG WRITE: %s.%s = %s", section, key, log_value)
                # Check if restart required
                if section in RESTART_REQUIRED_FIELDS and key in RESTART_REQUIRED_FIELDS[section]:
                    restart_needed.append(f"{section}.{key}")

    if not changed:
        return {
            "updated": updated, "restart_required": restart_needed,
            "skipped": skipped, "locked": locked,
        }

    # Backup existing file
    bak_path = Path(str(path) + ".bak")
    if path.exists():
        shutil.copy2(path, bak_path)

    # Atomic write: write-to-.tmp → fsync → os.replace. The .tmp sits in the
    # same directory as the target so both paths share a filesystem — no
    # EXDEV risk, even on Docker bind mounts. A power cut before os.replace
    # leaves the original file untouched; an orphan .tmp is cleaned up below.
    tmp_path = Path(str(path) + ".tmp")
    lock_fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        if _HAS_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        with open(tmp_path, "w") as f:
            config.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        logger.info("Config written to %s (%d fields updated)", path, len(updated))
    finally:
        if _HAS_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        # Clean up orphan .tmp if os.replace never ran (exception path).
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return {
        "updated": updated, "restart_required": restart_needed,
        "skipped": skipped, "locked": locked,
    }


def validate_config_updates(updates: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Validate update payload values against schema field specs.

    Returns a dict of field errors keyed as "section.key".
    Unknown fields are ignored here and still handled by write_config().
    """
    field_errors: dict[str, str] = {}
    for section, fields in updates.items():
        if not isinstance(fields, dict):
            continue
        schema_fields = SCHEMA.get(section)
        if not schema_fields:
            continue

        for key, value in fields.items():
            spec = schema_fields.get(key)
            if spec is None:
                continue

            # Preserve existing redacted secret behavior.
            is_redacted = (
                section in REDACTED_FIELDS
                and key in REDACTED_FIELDS[section]
                and value == REDACTED_VALUE
            )
            if is_redacted:
                continue

            raw = value if isinstance(value, str) else str(value)
            raw = raw.strip()
            field_path = f"{section}.{key}"

            if spec.type == FieldType.BOOL:
                if raw.lower() not in ("true", "false", "yes", "no", "1", "0", "on", "off"):
                    field_errors[field_path] = "must be a boolean (true/false)"
                continue

            if spec.type == FieldType.ENUM:
                if spec.choices and raw.lower() not in [c.lower() for c in spec.choices]:
                    choices = ", ".join(spec.choices)
                    field_errors[field_path] = f"must be one of: {choices}"
                continue

            if spec.type == FieldType.INT:
                try:
                    num = int(raw)
                except ValueError:
                    field_errors[field_path] = "must be an integer"
                    continue
                if spec.min_val is not None and num < spec.min_val:
                    field_errors[field_path] = f"must be >= {int(spec.min_val)}"
                    continue
                if spec.max_val is not None and num > spec.max_val:
                    field_errors[field_path] = f"must be <= {int(spec.max_val)}"
                continue

            if spec.type == FieldType.FLOAT:
                try:
                    num = float(raw)
                except ValueError:
                    field_errors[field_path] = "must be a number"
                    continue
                if spec.min_val is not None and num < spec.min_val:
                    field_errors[field_path] = f"must be >= {spec.min_val}"
                    continue
                if spec.max_val is not None and num > spec.max_val:
                    field_errors[field_path] = f"must be <= {spec.max_val}"

    return field_errors


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


# -- Issue #75 — Student config recovery -----------------------------------

# Versions stamped into export payloads. Bumping requires a corresponding
# bump in config_migrate; the export_version is a separate axis from the
# config schema_version (which describes the [meta] section of config.ini).
EXPORT_VERSION = 1

# Filename for the timestamped pre-reset snapshot. Distinct from the rolling
# .bak written by write_config — that one gets clobbered by every save and is
# useless if a student saves AFTER the reset.
PRE_RESET_PREFIX = "config.ini.before-reset."

# Filename-safe callsign pattern. Anything outside this set gets stripped
# from the filename so we can't accidentally produce paths with quotes,
# spaces, or path separators in a Content-Disposition header.
_SAFE_CALLSIGN_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_callsign(raw: str | None) -> str:
    if not raw:
        return "HYDRA"
    cleaned = _SAFE_CALLSIGN_RE.sub("-", raw.strip())
    cleaned = cleaned.strip("-._") or "HYDRA"
    return cleaned[:48]


def _utc_stamp() -> str:
    """UTC timestamp safe for filenames (YYYYMMDDTHHMMSSZ)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy src to dst via tmp + os.replace. Caller-side fsync.

    Uses an in-directory .tmp so src and dst share a filesystem (no EXDEV
    on Docker bind mounts). A power cut before os.replace leaves dst
    untouched; orphan .tmp is removed on exception.
    """
    tmp_path = Path(str(dst) + ".tmp")
    try:
        shutil.copy2(src, tmp_path)
        try:
            with open(tmp_path, "r+b") as f:
                os.fsync(f.fileno())
        except OSError:
            pass  # non-fatal — some filesystems ignore fsync
        os.replace(tmp_path, dst)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def factory_reset_with_backup() -> dict[str, Any]:
    """Restore config.ini from config.ini.factory, archiving current to a
    timestamped snapshot first.

    Distinct from restore_factory() which only writes the rolling .bak —
    that file is clobbered by the next save, so it's not enough for a
    student who pushes [save] right after a reset. The snapshot lives at
    ``config.ini.before-reset.<utc>`` and is never touched by write_config.

    Returns a dict with keys:
      - backup_path: str — absolute path to the snapshot, or "" if no
        prior config existed (fresh-install reset).
      - restart_required: bool — always True; the running service still
        holds the pre-reset values in memory.

    Raises:
      FileNotFoundError if config.ini.factory does not exist.
      configparser.Error if config.ini.factory is unparseable.
      OSError if the write fails after the snapshot succeeded.
    """
    path = get_config_path()
    factory_path = Path(str(path) + ".factory")

    if not factory_path.exists():
        raise FileNotFoundError(f"factory defaults not found at {factory_path}")

    # Validate factory parses cleanly BEFORE clobbering current config —
    # corrupted factory file would otherwise leave the device unbootable.
    factory_cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    factory_cfg.read(factory_path)
    if not factory_cfg.sections():
        raise configparser.Error(
            f"factory defaults at {factory_path} parsed to zero sections"
        )

    backup_path = ""
    if path.exists():
        snapshot = path.parent / f"{PRE_RESET_PREFIX}{_utc_stamp()}"
        # Avoid collision if two resets fire in the same second.
        suffix_idx = 0
        while snapshot.exists():
            suffix_idx += 1
            snapshot = path.parent / f"{PRE_RESET_PREFIX}{_utc_stamp()}-{suffix_idx}"
        _atomic_copy(path, snapshot)
        backup_path = str(snapshot.resolve())
        logger.info("Pre-reset snapshot saved to %s", snapshot)

    # Atomic factory copy. If this raises after the snapshot, the snapshot
    # is still on disk and the current config is untouched.
    _atomic_copy(factory_path, path)
    audit_log.warning("CONFIG FACTORY RESET (backup=%s)", backup_path or "<none>")
    logger.info("Config restored from factory defaults: %s", factory_path)

    return {"backup_path": backup_path, "restart_required": True}


def export_config_payload() -> dict[str, Any]:
    """Build the exported-config JSON document.

    Shape:
      {
        "export_version": <int>,
        "exported_at": "<UTC ISO-8601>",
        "schema_version": <int from [meta]>,
        "callsign": "<sanitized callsign or HYDRA>",
        "sections": { ... read_config() output, secrets redacted ... }
      }

    Secrets remain redacted ("***") — same contract as GET /api/config/full.
    Round-trip via /api/config/import preserves redacted values because
    write_config() leaves "***" placeholders untouched.
    """
    sections = read_config()  # already redacts api_token / kismet_pass
    schema_version = 0
    meta = sections.get("meta") or {}
    raw = meta.get("schema_version")
    if raw is not None:
        try:
            schema_version = int(raw)
        except (TypeError, ValueError):
            schema_version = 0

    # Prefer [identity].callsign (set by Platform Setup) over [tak].callsign.
    callsign_raw = (
        (sections.get("identity") or {}).get("callsign")
        or (sections.get("tak") or {}).get("callsign")
        or "HYDRA"
    )

    return {
        "export_version": EXPORT_VERSION,
        "exported_at": datetime.datetime.now(datetime.timezone.utc)
            .replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
        "schema_version": schema_version,
        "callsign": _safe_callsign(callsign_raw),
        "sections": sections,
    }


def export_filename(payload: dict[str, Any]) -> str:
    """Return the suggested filename for a Content-Disposition header."""
    callsign = _safe_callsign(payload.get("callsign"))
    stamp = _utc_stamp()
    return f"hydra-config-{callsign}-{stamp}.json"


# Sections allowed in an import payload. Anything outside this set is
# rejected with 400 — the import endpoint is the only path where untrusted
# JSON crosses into the config writer, so it earns the strict gate. Note:
# this is wider than SCHEMA because vehicle.* and meta are valid sections
# even though only some of their keys are schema-validated.
_IMPORT_ALLOWED_SECTIONS = set(SCHEMA.keys())
# `[identity]` is set by Platform Setup, not by users — refuse to import
# it so a malicious export can't rotate api_token / web_password / callsign.
_IMPORT_FORBIDDEN_SECTIONS = {"identity"}


def validate_import_payload(payload: Any) -> dict[str, Any]:
    """Validate a config import payload before any disk write.

    Accepts either the full export envelope (with "sections") or a bare
    {section: {key: value}} dict — same shape POST /api/config/full uses.

    Returns a dict:
      {
        "ok": bool,
        "updates": dict[str, dict[str, str]] — what to feed write_config,
        "errors": list[str] — human-readable rejection reasons,
        "field_errors": dict[str, str] — per-field validation messages,
      }

    Rejects on first sign of trouble:
      - non-dict payload
      - unknown section names
      - forbidden sections (identity)
      - keys not in SCHEMA for that section
      - values failing type/range/enum validation
    """
    out: dict[str, Any] = {"ok": False, "updates": {}, "errors": [], "field_errors": {}}

    if not isinstance(payload, dict):
        out["errors"].append("payload must be a JSON object")
        return out

    # Accept both the full export envelope and a bare section dict.
    if "sections" in payload and isinstance(payload["sections"], dict):
        sections = payload["sections"]
    else:
        sections = payload

    if not isinstance(sections, dict):
        out["errors"].append("'sections' must be a JSON object")
        return out

    updates: dict[str, dict[str, str]] = {}
    for section, fields in sections.items():
        if not isinstance(section, str):
            out["errors"].append(f"section name must be a string: {section!r}")
            continue
        if section in _IMPORT_FORBIDDEN_SECTIONS:
            out["errors"].append(
                f"section '{section}' cannot be imported (set by platform setup)"
            )
            continue
        if section not in _IMPORT_ALLOWED_SECTIONS:
            out["errors"].append(f"unknown section: {section}")
            continue
        if not isinstance(fields, dict):
            out["errors"].append(f"section '{section}' must contain an object")
            continue

        schema_section = SCHEMA[section]
        section_updates: dict[str, str] = {}
        for key, value in fields.items():
            if not isinstance(key, str):
                out["errors"].append(f"key in '{section}' must be a string: {key!r}")
                continue
            if key not in schema_section:
                out["errors"].append(f"unknown field: {section}.{key}")
                continue
            section_updates[key] = value if isinstance(value, str) else str(value)
        if section_updates:
            updates[section] = section_updates

    if out["errors"]:
        return out

    field_errors = validate_config_updates(updates)
    if field_errors:
        out["field_errors"] = field_errors
        return out

    out["ok"] = True
    out["updates"] = updates
    return out
