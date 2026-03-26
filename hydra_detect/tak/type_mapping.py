"""Map YOLO COCO class labels to MIL-STD-2525 CoT type codes."""

from __future__ import annotations

# Affiliation is "unknown" (u) — operator determines intent.
# Battle dimensions: G=Ground, A=Air, S=Surface, U=Subsurface
YOLO_TO_COT_TYPE: dict[str, str] = {
    # Persons
    "person":       "a-u-G-U-C-I",    # unknown ground unit civilian individual

    # Ground vehicles
    "car":          "a-u-G-E-V-C",    # unknown ground equipment vehicle civilian
    "truck":        "a-u-G-E-V-C",
    "bus":          "a-u-G-E-V-C",
    "motorcycle":   "a-u-G-E-V-C",
    "bicycle":      "a-u-G-E-V-C",

    # Maritime
    "boat":         "a-u-S-X",        # unknown surface other

    # Airborne
    "airplane":     "a-u-A",          # unknown air

    # Animals
    "dog":          "a-u-G",          # unknown ground
    "horse":        "a-u-G",

    # Objects of interest
    "backpack":     "a-u-G-I",        # unknown ground installation
    "suitcase":     "a-u-G-I",
    "handbag":      "a-u-G-I",
    "cell phone":   "a-u-G-I",
    "laptop":       "a-u-G-I",
    "knife":        "a-u-G-I",
    "scissors":     "a-u-G-I",
    "baseball bat": "a-u-G-I",
    "bottle":       "a-u-G-I",
    "umbrella":     "a-u-G-I",
}

DEFAULT_COT_TYPE = "a-u-G"


def get_cot_type(label: str) -> str:
    """Return the MIL-STD-2525 CoT type string for a YOLO class label."""
    return YOLO_TO_COT_TYPE.get(label, DEFAULT_COT_TYPE)
