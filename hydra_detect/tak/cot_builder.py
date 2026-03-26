"""Build Cursor on Target (CoT) XML events for TAK/ATAK integration."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone


def _iso_now() -> str:
    """Return current UTC time as ISO 8601 string with 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso_stale(seconds: float) -> str:
    """Return UTC time + offset as ISO 8601 string."""
    t = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _build_event(
    uid: str,
    cot_type: str,
    how: str,
    lat: float,
    lon: float,
    hae: float,
    ce: str,
    le: str,
    stale_seconds: float,
) -> ET.Element:
    """Build the common CoT <event> skeleton."""
    now = _iso_now()
    stale = _iso_stale(stale_seconds)

    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", uid)
    event.set("type", cot_type)
    event.set("time", now)
    event.set("start", now)
    event.set("stale", stale)
    event.set("how", how)

    point = ET.SubElement(event, "point")
    point.set("lat", f"{lat:.7f}")
    point.set("lon", f"{lon:.7f}")
    point.set("hae", f"{hae:.1f}")
    point.set("ce", ce)
    point.set("le", le)

    ET.SubElement(event, "detail")
    return event


def _to_bytes(event: ET.Element) -> bytes:
    """Serialize an ElementTree element to UTF-8 XML bytes."""
    return ET.tostring(event, encoding="unicode", xml_declaration=True).encode("utf-8")


def build_self_sa(
    uid: str,
    callsign: str,
    lat: float,
    lon: float,
    hae: float,
    heading: float | None = None,
    speed: float | None = None,
    stale_seconds: float = 30.0,
) -> bytes:
    """Build a self-SA CoT event (drone position).

    Type ``a-f-A-M-F-Q`` = friendly air military fixed-wing rotary (UAV).
    """
    event = _build_event(
        uid=uid,
        cot_type="a-f-A-M-F-Q",
        how="m-g",
        lat=lat, lon=lon, hae=hae,
        ce="9999999", le="9999999",
        stale_seconds=stale_seconds,
    )
    detail = event.find("detail")
    assert detail is not None

    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", callsign)

    if heading is not None or speed is not None:
        track = ET.SubElement(detail, "track")
        track.set("course", f"{heading:.1f}" if heading is not None else "0.0")
        track.set("speed", f"{speed:.1f}" if speed is not None else "0.0")

    ET.SubElement(detail, "precisionlocation").set("altsrc", "GPS")
    ET.SubElement(detail, "remarks").text = "Hydra Detect UAS"

    return _to_bytes(event)


def build_detection_marker(
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
    """Build a detection marker CoT event.

    ``how="h-e"`` = human-estimated (machine-detected, position estimated).
    ``ce="50"`` = 50 m circular error (honest about camera projection uncertainty).
    """
    event = _build_event(
        uid=uid,
        cot_type=cot_type,
        how="h-e",
        lat=lat, lon=lon, hae=hae,
        ce="50", le="50",
        stale_seconds=stale_seconds,
    )
    detail = event.find("detail")
    assert detail is not None

    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", callsign)

    remarks = ET.SubElement(detail, "remarks")
    remarks.text = f"Hydra: {label} #{track_id} conf={confidence:.0%}"

    return _to_bytes(event)


def build_video_feed(
    uid: str,
    callsign: str,
    rtsp_url: str,
    lat: float,
    lon: float,
    hae: float,
    stale_seconds: float = 120.0,
) -> bytes:
    """Build a video feed announcement CoT event (type ``b-i-v``)."""
    event = _build_event(
        uid=uid,
        cot_type="b-i-v",
        how="h-e",
        lat=lat, lon=lon, hae=hae,
        ce="9999999", le="9999999",
        stale_seconds=stale_seconds,
    )
    detail = event.find("detail")
    assert detail is not None

    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", f"{callsign} Video")

    video = ET.SubElement(detail, "__video")
    video.set("url", rtsp_url)

    ET.SubElement(detail, "remarks").text = "Hydra Detect RTSP feed"

    return _to_bytes(event)
