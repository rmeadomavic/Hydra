"""TAK/ATAK CoT integration package.

Public surface:
    * :func:`get_tak_output_cls` — return the active outbound CoT emitter
      class. Selection is driven by the ``HYDRA_COT_BACKEND`` environment
      variable:

        - ``pytak`` (default): :class:`pytak_emitter.PyTAKOutput`
        - ``legacy``: :class:`tak_output.TAKOutput`

    * :data:`TAKOutput` — re-export of whichever backend
      ``get_tak_output_cls()`` returned at import time.

Why this module rebinds ``hydra_detect.tak.tak_output.TAKOutput``:

    The pipeline facade (HARD DO-NOT-TOUCH per the migration spec)
    imports the emitter via ``from ..tak.tak_output import TAKOutput``,
    which bypasses this package's ``__init__``. To honour the
    ``HYDRA_COT_BACKEND`` flag without editing the facade, we resolve
    the active backend class here and patch the ``TAKOutput`` symbol
    in the ``tak_output`` submodule **at import time**. Modules
    importing it later (or before, since Python caches) get the active
    class without any callsite changes.

    The legacy class itself stays untouched on disk — we only rebind
    the public symbol. ``HYDRA_COT_BACKEND=legacy`` puts the original
    class back in place, so the one-week safety-net rollback works
    with a single env-var flip and no code change.

    The CoT bytes on the wire are identical between the two backends —
    both emitters route XML construction through ``cot_builder.py``,
    so the only thing that changes is the network plumbing.

NOTE: this module governs OUTBOUND CoT only. The inbound CoT command
listener (``hydra_detect.tak.tak_input``) is adversarial-gated and is
not part of this migration.
"""

from __future__ import annotations

import logging
import os
from typing import Type

logger = logging.getLogger(__name__)

# Single source of truth for the env var name.
BACKEND_ENV_VAR = "HYDRA_COT_BACKEND"
DEFAULT_BACKEND = "pytak"
_VALID_BACKENDS = ("pytak", "legacy")


def _selected_backend() -> str:
    """Resolve the configured CoT backend, with a sane fallback."""
    raw = os.environ.get(BACKEND_ENV_VAR, DEFAULT_BACKEND).strip().lower()
    if raw not in _VALID_BACKENDS:
        logger.warning(
            "%s=%r is not one of %s; falling back to %r",
            BACKEND_ENV_VAR, raw, _VALID_BACKENDS, DEFAULT_BACKEND,
        )
        return DEFAULT_BACKEND
    return raw


def get_tak_output_cls() -> Type:
    """Return the active outbound TAK output class.

    Resolved on every call so tests can flip ``HYDRA_COT_BACKEND`` at
    runtime without re-importing the package.
    """
    backend = _selected_backend()
    if backend == "pytak":
        try:
            from .pytak_emitter import PyTAKOutput
            return PyTAKOutput
        except ImportError as exc:
            logger.error(
                "pytak backend selected but import failed (%s); "
                "falling back to legacy emitter", exc,
            )
            from .tak_output import TAKOutput as _Legacy
            return _Legacy
    # legacy
    from .tak_output import TAKOutput as _Legacy
    return _Legacy


def _bind_active_backend_to_legacy_module() -> Type:
    """Rebind ``tak_output.TAKOutput`` to the active backend.

    The pipeline facade imports the emitter via
    ``from ..tak.tak_output import TAKOutput``. To honour
    ``HYDRA_COT_BACKEND`` without editing the facade (HARD DO-NOT-TOUCH
    per the migration spec), we patch the symbol in-place at import
    time. The original legacy class stays available via
    ``tak_output._LegacyTAKOutput`` for ``HYDRA_COT_BACKEND=legacy`` to
    flip back to.

    Returns the active class so the package re-export below stays in
    sync.
    """
    from . import tak_output as _legacy_mod

    # Stash the original class once so repeated imports / reloads keep
    # working — without this, a second call would think the rebound
    # PyTAKOutput is the legacy class and lose the original.
    if not hasattr(_legacy_mod, "_LegacyTAKOutput"):
        _legacy_mod._LegacyTAKOutput = _legacy_mod.TAKOutput  # type: ignore[attr-defined]

    backend = _selected_backend()
    if backend == "pytak":
        try:
            from .pytak_emitter import PyTAKOutput
            _legacy_mod.TAKOutput = PyTAKOutput  # type: ignore[assignment]
            return PyTAKOutput
        except ImportError as exc:
            logger.error(
                "pytak backend selected but import failed (%s); "
                "leaving legacy TAKOutput in place", exc,
            )
            legacy_cls = _legacy_mod._LegacyTAKOutput  # type: ignore[attr-defined]
            _legacy_mod.TAKOutput = legacy_cls  # type: ignore[assignment]
            return legacy_cls

    # legacy: restore the original class (covers the case where a
    # previous import bound PyTAKOutput and the env var was flipped
    # back, e.g. in tests).
    legacy_cls = _legacy_mod._LegacyTAKOutput  # type: ignore[attr-defined]
    _legacy_mod.TAKOutput = legacy_cls  # type: ignore[assignment]
    return legacy_cls


# Bind on import. Eager re-export so callers using
# ``from hydra_detect.tak import TAKOutput`` get the active class.
TAKOutput = _bind_active_backend_to_legacy_module()

__all__ = [
    "get_tak_output_cls",
    "TAKOutput",
    "BACKEND_ENV_VAR",
    "DEFAULT_BACKEND",
]
