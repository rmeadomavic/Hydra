"""Operating mode — system environment descriptor.

Modes are orthogonal to vehicle profile and mission profile. They describe
the environment Hydra is operating in, not what the platform is doing.

Modes: SIM | BENCH | OBSERVE | FIELD | ARMED | MAINTENANCE
Default: OBSERVE (on fresh install or factory reset)

ARMED is the only mode that requires a double-confirmation (confirmed_twice=True).
All mode transitions are written atomically to config.ini via the existing
file-lock pattern and emit one event-timeline entry.
"""

from __future__ import annotations

import configparser
import logging
import os
import shutil
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Optional event logger reference — injected by pipeline at startup.
# None when running in tests or before pipeline initialises.
_event_logger: Any = None


def register_event_logger(event_logger: Any) -> None:
    """Inject the EventLogger instance so mode transitions are recorded."""
    global _event_logger
    _event_logger = event_logger


def _get_event_logger() -> Any | None:
    """Return the registered EventLogger, or None."""
    return _event_logger


class OperatingMode(str, Enum):
    """Operating environment for Hydra Detect.

    Values equal their names so round-tripping through config strings is
    unambiguous: OperatingMode("FIELD") == OperatingMode.FIELD.
    """
    SIM = "SIM"
    BENCH = "BENCH"
    OBSERVE = "OBSERVE"
    FIELD = "FIELD"
    ARMED = "ARMED"
    MAINTENANCE = "MAINTENANCE"


class ModeTransitionError(Exception):
    """Raised when a mode transition is rejected."""


_DEFAULT_MODE = OperatingMode.OBSERVE


def get_config_path() -> Path:
    """Return the current config.ini path (delegates to config_api)."""
    from hydra_detect.web.config_api import get_config_path as _gcp
    return _gcp()


def current_mode(cfg: configparser.ConfigParser) -> OperatingMode:
    """Read the current operating mode from a parsed config.

    Returns OBSERVE if the key or section is missing, or if the stored
    value is not a recognised mode name.
    """
    try:
        raw = cfg.get("system", "mode").strip().upper()
        return OperatingMode(raw)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return _DEFAULT_MODE
    except ValueError:
        logger.warning("Unknown operating mode in config; defaulting to OBSERVE")
        return _DEFAULT_MODE


def _write_mode_atomic(path: Path, new_mode: OperatingMode) -> None:
    """Write mode value atomically to config.ini.

    Uses file-lock + os.replace to match config_api's write_config() pattern.
    fcntl is imported lazily so this module can be imported on Windows;
    the call will fail on Windows (production targets Linux/Jetson only).
    """
    import fcntl  # Linux-only — production Jetson; tests mock get_config_path

    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(path)

    if not cfg.has_section("system"):
        cfg.add_section("system")
    cfg.set("system", "mode", new_mode.value)

    bak_path = Path(str(path) + ".bak")
    if path.exists():
        shutil.copy2(path, bak_path)

    tmp_path = Path(str(path) + ".tmp")
    lock_fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        with open(tmp_path, "w") as f:
            cfg.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def set_mode(
    cfg: configparser.ConfigParser,
    new_mode: OperatingMode,
    reason: str = "",
    confirmed_twice: bool = False,
    actor: str = "api",
) -> None:
    """Transition to new_mode and persist to config.ini.

    ARMED transitions require confirmed_twice=True, otherwise
    ModeTransitionError is raised. All other modes accept confirmed_twice
    in any state (it's ignored for non-ARMED transitions).

    The transition is written atomically via file-lock + os.replace.
    An event-timeline entry is emitted if an EventLogger has been registered.

    Args:
        cfg: Parsed ConfigParser (used to read current mode for the event).
        new_mode: Target OperatingMode.
        reason: Human-readable reason for the transition.
        confirmed_twice: Must be True when transitioning to ARMED.
        actor: "api" | "boot" | other attribution string for the event log.

    Raises:
        ModeTransitionError: ARMED transition without confirmed_twice=True.
    """
    if new_mode is OperatingMode.ARMED and not confirmed_twice:
        raise ModeTransitionError(
            "Transition to ARMED requires confirmed_twice=True. "
            "Send the request twice with confirm=true."
        )

    from_mode = current_mode(cfg)

    # Resolve config path (patchable in tests).
    path = get_config_path()
    _write_mode_atomic(path, new_mode)

    logger.info(
        "Mode: %s → %s (reason=%r actor=%s)",
        from_mode.value, new_mode.value, reason, actor,
    )

    # Emit event-timeline entry.
    el = _get_event_logger()
    if el is not None:
        try:
            el.log_action("mode.transition", {
                "from": from_mode.value,
                "to": new_mode.value,
                "reason": reason,
                "actor": actor,
            })
        except Exception as exc:  # never let event logging crash the caller
            logger.debug("Mode event log error: %s", exc)
