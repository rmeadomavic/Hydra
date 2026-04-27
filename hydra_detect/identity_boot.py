"""First-boot identity check for Hydra Detect.

Called from __main__.py after config migration and before pipeline init.
Does NOT auto-generate identity. Identity requires operator-initiated
Platform Setup so the plaintext password can be displayed and captured.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def maybe_generate_identity(config_path: str | Path) -> None:
    """Check for a valid unit identity in config.ini. Warn if absent.

    If [identity] is fully populated and the callsign matches the expected
    HYDRA-NN-PROFILE pattern, logs a confirmation and returns quietly.

    If the identity is missing or incomplete, logs a loud WARNING. The unit
    will still boot and operate — the warning is for the operator, not a
    hard failure. Identity is required before operational use.

    Duplicate-callsign detection: if a callsign starts with HYDRA-00-,
    that is the placeholder value from golden-image flashing. Warn loudly
    so the operator knows Platform Setup has not been run.
    """
    from .identity import load_identity_from_config, is_callsign_valid

    identity = load_identity_from_config(config_path)

    if identity is None:
        logger.warning(
            "Unit identity not set. Run Platform Setup before operational use."
            " (scripts/platform_setup.py)"
        )
        return

    if not is_callsign_valid(identity.callsign):
        logger.warning(
            "Unit identity present but callsign format is invalid: %r — "
            "run Platform Setup to regenerate.",
            identity.callsign,
        )
        return

    # Detect unconfigured golden-image placeholder.
    if identity.callsign.startswith("HYDRA-00-"):
        logger.warning(
            "Callsign %r is a golden-image placeholder. "
            "Run Platform Setup to assign a real unit number.",
            identity.callsign,
        )
        return

    logger.info(
        "Unit identity: callsign=%s hostname=%s version=%s commit=%s",
        identity.callsign,
        identity.hostname,
        identity.software_version,
        identity.commit_hash[:8] if identity.commit_hash != "unknown" else "unknown",
    )
