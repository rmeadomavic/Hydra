"""TAK/ATAK CoT integration package.

Public surface:
    * :func:`get_tak_output_cls` — return the active outbound CoT emitter
      class. Selection is driven by the ``HYDRA_COT_BACKEND`` environment
      variable:

        - ``pytak`` (default): :class:`pytak_emitter.PyTAKOutput`
        - ``legacy``: :class:`tak_output.TAKOutput`

    * :data:`TAKOutput` — re-export of whichever backend
      ``get_tak_output_cls()`` returned at import time. Existing callers
      that ``from hydra_detect.tak import TAKOutput`` (or even
      ``from hydra_detect.tak.tak_output import TAKOutput``) keep working —
      the legacy path is intentionally preserved as a one-week safety net
      so we can fall back without a code change if pytak misbehaves in the
      field.

The legacy emitter (``hydra_detect.tak.tak_output``) and CoT XML builders
(``hydra_detect.tak.cot_builder``) are NOT touched. The pytak path
delegates XML construction back to ``cot_builder`` so the bytes on the
wire stay byte-for-byte identical to the legacy emitter — only the
network plumbing changes.

NOTE: this module governs OUTBOUND CoT only. The inbound CoT command
listener (``hydra_detect.tak.tak_input``) is adversarial-gated and not
part of this migration.
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
            from .tak_output import TAKOutput
            return TAKOutput
    # legacy
    from .tak_output import TAKOutput
    return TAKOutput


# Eager re-export for callers using ``from hydra_detect.tak import TAKOutput``.
TAKOutput = get_tak_output_cls()

__all__ = ["get_tak_output_cls", "TAKOutput", "BACKEND_ENV_VAR", "DEFAULT_BACKEND"]
