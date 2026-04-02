"""Waypoint export — QGC WPL 110 format for detection-driven missions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class Waypoint:
    lat: float
    lon: float
    alt: float
    label: str
    confidence: float


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS points."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def deduplicate(waypoints: list[Waypoint], radius_m: float = 10.0) -> list[Waypoint]:
    """Merge waypoints within radius_m, keeping highest confidence."""
    result: list[Waypoint] = []
    for wp in sorted(waypoints, key=lambda w: -w.confidence):
        if not any(_haversine_m(wp.lat, wp.lon, r.lat, r.lon) < radius_m
                   for r in result):
            result.append(wp)
    return result


def format_wpl(waypoints: list[Waypoint], home_lat: float, home_lon: float,
               home_alt: float = 0.0, loiter_sec: float = 5.0) -> str:
    """Format waypoints as QGC WPL 110 text."""
    lines = ["QGC WPL 110"]
    # Home waypoint (seq 0, current=1, frame=0, cmd=16 NAV_WAYPOINT)
    lines.append(
        f"0\t1\t0\t16\t0\t0\t0\t0\t"
        f"{home_lat:.8f}\t{home_lon:.8f}\t{home_alt:.2f}\t1"
    )
    # Detection waypoints (frame=3 GLOBAL_RELATIVE_ALT, cmd=16 NAV_WAYPOINT)
    for i, wp in enumerate(waypoints[:99], start=1):  # Max 99 waypoints
        lines.append(
            f"{i}\t0\t3\t16\t{loiter_sec:.0f}\t0\t0\t0\t"
            f"{wp.lat:.8f}\t{wp.lon:.8f}\t{wp.alt:.2f}\t1"
        )
    return "\n".join(lines) + "\n"


def tracks_to_waypoints(tracks: list[dict[str, Any]], alt_m: float = 15.0,
                        classes: set[str] | None = None) -> list[Waypoint]:
    """Convert track dicts (from /api/detections or detection logs) to Waypoints.

    Filters out tracks without valid GPS coordinates.
    """
    waypoints: list[Waypoint] = []
    for t in tracks:
        lat = t.get("lat") or t.get("latitude", 0.0)
        lon = t.get("lon") or t.get("longitude", 0.0)
        if lat == 0.0 and lon == 0.0:
            continue
        if lat is None or lon is None:
            continue
        label = t.get("label", t.get("class", "unknown"))
        if classes and label not in classes:
            continue
        conf = t.get("confidence", t.get("conf", 0.0))
        waypoints.append(Waypoint(
            lat=float(lat), lon=float(lon), alt=alt_m,
            label=label, confidence=float(conf),
        ))
    return waypoints
