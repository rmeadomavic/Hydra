"""Minimal CoT XML builders — vendored from hydra_detect/tak/cot_builder.py.

Kept in sync so UIDs match on both sides. If you extend the detection marker
(e.g. add remarks fields), mirror the change in ``hydra_detect/tak/cot_builder.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

YOLO_TO_COT_TYPE: dict[str, str] = {
    "person": "a-u-G-U-C-I",
    "car": "a-u-G-E-V-C",
    "truck": "a-u-G-E-V-C",
    "bus": "a-u-G-E-V-C",
    "motorcycle": "a-u-G-E-V-C",
    "bicycle": "a-u-G-E-V-C",
    "boat": "a-u-S-X",
    "airplane": "a-u-A",
}
DEFAULT_COT_TYPE = "a-u-G"


def get_cot_type(label: str) -> str:
    return YOLO_TO_COT_TYPE.get(label.lower(), DEFAULT_COT_TYPE)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso_stale(seconds: float) -> str:
    t = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_detection_marker(
    *,
    uid: str,
    callsign: str,
    cot_type: str,
    lat: float,
    lon: float,
    hae: float,
    confidence: float,
    label: str,
    track_id: int,
    stale_seconds: float = 60.0,
) -> bytes:
    now = _iso_now()
    stale = _iso_stale(stale_seconds)

    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", uid)
    event.set("type", cot_type)
    event.set("time", now)
    event.set("start", now)
    event.set("stale", stale)
    event.set("how", "h-e")

    point = ET.SubElement(event, "point")
    point.set("lat", f"{lat:.7f}")
    point.set("lon", f"{lon:.7f}")
    point.set("hae", f"{hae:.1f}")
    point.set("ce", "50")
    point.set("le", "50")

    detail = ET.SubElement(event, "detail")
    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", callsign)
    remarks = ET.SubElement(detail, "remarks")
    remarks.text = (
        f"Hydra relay: {label} #{track_id} conf={confidence:.0%}"
    )
    return ET.tostring(event, encoding="utf-8", xml_declaration=True)
