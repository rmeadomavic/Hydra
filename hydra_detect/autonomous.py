"""Autonomous strike controller — geofenced auto-engage with qualification criteria."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)
audit_log = logging.getLogger("hydra.audit")


# ---------------------------------------------------------------------------
# Geofence primitives
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two lat/lon points (WGS-84)."""
    R = 6_371_000.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_in_polygon(lat: float, lon: float, vertices: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test. Vertices are (lat, lon) tuples."""
    n = len(vertices)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lon_i = vertices[i]
        lat_j, lon_j = vertices[j]
        if ((lon_i > lon) != (lon_j > lon)) and \
           (lat < (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i) + lat_i):
            inside = not inside
        j = i
    return inside


def parse_polygon(raw: str) -> list[tuple[float, float]]:
    """Parse 'lat,lon;lat,lon;...' into a list of (lat, lon) tuples."""
    vertices: list[tuple[float, float]] = []
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split(",")
        if len(parts) != 2:
            raise ValueError(f"Invalid polygon vertex: {pair!r}")
        vertices.append((float(parts[0].strip()), float(parts[1].strip())))
    return vertices


# ---------------------------------------------------------------------------
# Track persistence counter
# ---------------------------------------------------------------------------

@dataclass
class _TrackPersistence:
    """Counts consecutive frames a track_id has been seen."""

    counts: dict[int, int] = field(default_factory=dict)
    _seen_this_frame: set[int] = field(default_factory=set)

    def begin_frame(self) -> None:
        self._seen_this_frame = set()

    def mark(self, track_id: int) -> int:
        """Mark a track as seen this frame. Returns the consecutive count."""
        self._seen_this_frame.add(track_id)
        self.counts[track_id] = self.counts.get(track_id, 0) + 1
        return self.counts[track_id]

    def end_frame(self) -> None:
        """Reset counts for tracks not seen this frame."""
        lost = [tid for tid in self.counts if tid not in self._seen_this_frame]
        for tid in lost:
            del self.counts[tid]


# ---------------------------------------------------------------------------
# Autonomous controller
# ---------------------------------------------------------------------------

class AutonomousController:
    """Evaluates qualification criteria and initiates autonomous strikes.

    All criteria must be met simultaneously:
    1. Controller is enabled
    2. Vehicle is in an allowed mode (e.g. AUTO)
    3. Vehicle GPS is inside the geofence
    4. No strike in cooldown
    5. A track matches: class in whitelist, confidence >= threshold,
       tracked for >= min_track_frames consecutive frames
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        # Circle geofence
        geofence_lat: float = 0.0,
        geofence_lon: float = 0.0,
        geofence_radius_m: float = 100.0,
        # Polygon geofence (overrides circle if non-empty)
        geofence_polygon: list[tuple[float, float]] | None = None,
        # Qualification
        min_confidence: float = 0.85,
        min_track_frames: int = 5,
        allowed_classes: list[str] | None = None,
        # Cooldown
        strike_cooldown_sec: float = 30.0,
        # Vehicle mode check
        allowed_vehicle_modes: list[str] | None = None,
    ):
        self.enabled = enabled
        self._geofence_lat = geofence_lat
        self._geofence_lon = geofence_lon
        self._geofence_radius_m = geofence_radius_m
        self._geofence_polygon = geofence_polygon or []
        self._min_confidence = min_confidence
        self._min_track_frames = min_track_frames
        self._allowed_classes = [c.lower().strip() for c in (allowed_classes or [])]
        self._strike_cooldown = strike_cooldown_sec
        self._allowed_modes = [m.upper().strip() for m in (allowed_vehicle_modes or ["AUTO"])]

        self._persistence = _TrackPersistence()
        self._last_strike_time: float = 0.0
        self._strike_in_progress = False

    # -- Geofence checks ---------------------------------------------------

    def _has_valid_geofence(self) -> bool:
        """Return True if a meaningful geofence is configured."""
        if self._geofence_polygon and len(self._geofence_polygon) >= 3:
            return True
        if self._geofence_lat != 0.0 or self._geofence_lon != 0.0:
            return True
        return False

    def check_geofence(self, lat: float, lon: float) -> bool:
        """Return True if the position is inside the geofence."""
        if self._geofence_polygon and len(self._geofence_polygon) >= 3:
            return point_in_polygon(lat, lon, self._geofence_polygon)
        return haversine_m(lat, lon, self._geofence_lat, self._geofence_lon) <= self._geofence_radius_m

    # -- Main evaluation ----------------------------------------------------

    def evaluate(
        self,
        tracks: object,  # TrackingResult
        mavlink: object | None,  # MAVLinkIO
        lock_cb: Callable[[int, str], bool],
        strike_cb: Callable[[int], bool],
    ) -> None:
        """Evaluate all criteria and initiate autonomous strike if qualified.

        Called once per frame from the pipeline loop.
        """
        if not self.enabled or mavlink is None:
            return

        # Check geofence is configured
        if not self._has_valid_geofence():
            return

        # Check cooldown
        now = time.monotonic()
        if (now - self._last_strike_time) < self._strike_cooldown:
            return

        # Check vehicle mode
        vehicle_mode = getattr(mavlink, "get_vehicle_mode", lambda: None)()
        if vehicle_mode is None:
            return  # Can't determine mode — don't act
        if vehicle_mode.upper() not in self._allowed_modes:
            return

        # Check vehicle inside geofence
        get_lat_lon = getattr(mavlink, "get_lat_lon", None)
        if get_lat_lon is None:
            return
        lat, lon, _ = get_lat_lon()
        if lat is None or lon is None:
            return
        if not self.check_geofence(lat, lon):
            return

        # Evaluate tracks
        self._persistence.begin_frame()

        best_track = None
        best_frames = 0

        for track in tracks:
            # Class whitelist
            if not self._allowed_classes:
                continue  # no whitelist = no valid targets (fail-closed)
            if track.label.lower().strip() not in self._allowed_classes:
                continue
            # Confidence threshold
            if track.confidence < self._min_confidence:
                continue
            # Track persistence
            frames = self._persistence.mark(track.track_id)
            if frames >= self._min_track_frames and frames > best_frames:
                best_track = track
                best_frames = frames

        self._persistence.end_frame()

        if best_track is None:
            return

        # All criteria met — initiate autonomous strike
        pos_str = getattr(mavlink, "get_position_string", lambda: None)()
        audit_log.info(
            "AUTONOMOUS STRIKE INITIATED: track_id=%d label=%s confidence=%.3f "
            "frames=%d vehicle_mode=%s position=%s",
            best_track.track_id, best_track.label, best_track.confidence,
            best_frames, vehicle_mode, pos_str,
        )
        logger.warning(
            "AUTO-STRIKE: %s #%d (%.0f%% conf, %d frames) @ %s",
            best_track.label, best_track.track_id,
            best_track.confidence * 100, best_frames, pos_str,
        )

        # Send alert to GCS
        send_statustext = getattr(mavlink, "send_statustext", None)
        if send_statustext:
            alert = f"AUTO-STRIKE: {best_track.label} #{best_track.track_id}"
            if pos_str:
                alert += f" @ {pos_str}"
            send_statustext(alert[:50], severity=1)  # ALERT severity

        # Lock and strike
        lock_cb(best_track.track_id, "strike")
        result = strike_cb(best_track.track_id)

        if result:
            self._last_strike_time = now
            audit_log.info(
                "AUTONOMOUS STRIKE CONFIRMED: track_id=%d", best_track.track_id
            )
        else:
            audit_log.warning(
                "AUTONOMOUS STRIKE FAILED: track_id=%d (strike_cb returned False)",
                best_track.track_id,
            )

    def notify_strike_complete(self) -> None:
        """Called when a strike finishes (target lost or vehicle arrives)."""
        self._strike_in_progress = False
