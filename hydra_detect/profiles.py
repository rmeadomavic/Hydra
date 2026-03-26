"""Mission profile loading and validation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = {
    "id", "name", "description", "model", "confidence",
    "yolo_classes", "alert_classes", "auto_loiter_on_detect",
    "strike_distance_m",
}


def load_profiles(path: str) -> dict:
    """Load and validate mission profiles from a JSON file.

    Returns a dict with 'profiles' (list) and 'default_profile' (str | None).
    On any error, returns empty profiles list (graceful degradation).
    """
    result: dict = {"profiles": [], "default_profile": None}
    p = Path(path)
    if not p.exists():
        logger.info("No profiles file at %s — profiles disabled.", path)
        return result
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load profiles from %s: %s", path, exc)
        return result

    if not isinstance(raw, dict):
        logger.warning("Profiles file must be a JSON object.")
        return result

    result["default_profile"] = raw.get("default_profile")

    for entry in raw.get("profiles", []):
        missing = _REQUIRED_FIELDS - set(entry.keys())
        if missing:
            logger.warning("Profile '%s' missing fields %s — skipped.",
                           entry.get("id", "?"), missing)
            continue
        result["profiles"].append(entry)

    return result


def get_profile(profiles: dict, profile_id: str) -> dict | None:
    """Look up a profile by ID. Returns None if not found."""
    for p in profiles.get("profiles", []):
        if p.get("id") == profile_id:
            return p
    return None
