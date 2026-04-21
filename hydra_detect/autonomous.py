"""Autonomous strike controller — geofenced auto-engage with qualification criteria."""

from __future__ import annotations

import collections
import copy
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)
audit_log = logging.getLogger("hydra.audit")


# Dashboard constants (match frontend mock + impl_autonomy.md spec)
AUTONOMY_MODES = ("dryrun", "shadow", "live")
GATE_IDS = ("geofence", "vehicle_mode", "operator_lock", "gps_fresh", "cooldown")
GATE_STATES = ("PASS", "FAIL", "N/A")
DECISION_ACTIONS = ("engage", "reject", "defer", "passthrough")
AUTONOMY_LOG_MAXLEN = 200


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
        # GPS freshness
        gps_max_stale_sec: float = 2.0,
        # Operator lock requirement
        require_operator_lock: bool = True,
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

        self._gps_max_stale_sec = gps_max_stale_sec
        self._require_operator_lock = require_operator_lock
        self._operator_locked_track: int | None = None  # set by pipeline on lock

        self._persistence = _TrackPersistence()
        self._last_strike_time: float = 0.0
        self._last_evaluate_time: float = 0.0  # monotonic time of last full eval
        self._strike_in_progress = False
        self._suppressed = False  # External suppression (e.g. camera loss)
        self._empty_classes_warn_time: float = 0.0  # throttle warning for empty allowed_classes

        # Dashboard snapshot state — thread-safe, drives GET /api/autonomy/status.
        # Mode is a display-level state machine for the operator UI; it does
        # not gate evaluate() behaviour in this wave (reader-only dashboard).
        self._dashboard_lock = threading.Lock()
        self._mode: str = "dryrun"
        self._gate_cache: dict[str, dict[str, str]] = {
            gid: {"state": "N/A", "detail": ""} for gid in GATE_IDS
        }
        self._decision_log: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=AUTONOMY_LOG_MAXLEN
        )
        self._self_position: dict[str, float] | None = None

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
        dist = haversine_m(
            lat, lon, self._geofence_lat, self._geofence_lon,
        )
        return dist <= self._geofence_radius_m

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
        if not self.enabled or self._suppressed or mavlink is None:
            # System off / suppressed — mark every gate N/A so the dashboard
            # does not display stale PASS/FAIL from a prior active session.
            off_reason = (
                "disabled" if not self.enabled
                else "suppressed" if self._suppressed
                else "no mavlink"
            )
            for gid in GATE_IDS:
                self._record_gate_evaluation(gid, "N/A", off_reason)
            return

        # Check geofence is configured
        if not self._has_valid_geofence():
            self._record_gate_evaluation("geofence", "N/A", "no geofence configured")
            self._mark_gates_na(
                "cooldown", "vehicle_mode", "gps_fresh", "operator_lock",
                detail="gate not reached",
            )
            self._record_decision(None, "", "reject", "no geofence configured")
            return

        # Check cooldown
        now = time.monotonic()
        if self._last_strike_time > 0.0 and (now - self._last_strike_time) < self._strike_cooldown:
            remaining = self._strike_cooldown - (now - self._last_strike_time)
            self._record_gate_evaluation("cooldown", "FAIL", f"{remaining:.1f}s remaining")
            self._mark_gates_na(
                "vehicle_mode", "gps_fresh", "geofence", "operator_lock",
                detail="cooldown active",
            )
            self._record_decision(
                None, "", "reject", f"cooldown {remaining:.1f}s remaining",
            )
            return
        cooldown_detail = "ready" if self._last_strike_time > 0.0 else "no prior strike"
        cooldown_state = "PASS" if self._last_strike_time > 0.0 else "N/A"
        self._record_gate_evaluation("cooldown", cooldown_state, cooldown_detail)

        # Check vehicle mode
        vehicle_mode = getattr(mavlink, "get_vehicle_mode", lambda: None)()
        if vehicle_mode is None:
            self._record_gate_evaluation("vehicle_mode", "N/A", "mode unknown")
            self._mark_gates_na(
                "gps_fresh", "geofence", "operator_lock",
                detail="vehicle_mode unknown",
            )
            self._record_decision(None, "", "reject", "vehicle mode unknown")
            return  # Can't determine mode — don't act
        if vehicle_mode.upper() not in self._allowed_modes:
            need = "/".join(self._allowed_modes)
            self._record_gate_evaluation(
                "vehicle_mode", "FAIL",
                f"{vehicle_mode.upper()} (need {need})",
            )
            self._mark_gates_na(
                "gps_fresh", "geofence", "operator_lock",
                detail="vehicle_mode failed",
            )
            self._record_decision(
                None, "", "reject",
                f"vehicle mode {vehicle_mode.upper()} not in {need}",
            )
            return
        self._record_gate_evaluation("vehicle_mode", "PASS", vehicle_mode.upper())

        # Check GPS freshness BEFORE geofence (Fix 1: stale GPS must be rejected first)
        # Skip freshness check when last_update is 0.0 — this means GPS data
        # is operator-provided/static (sim mode, bench config), not stale.
        get_gps = getattr(mavlink, "get_gps", None)
        if get_gps is not None:
            gps_data = get_gps()
            last_update = gps_data.get("last_update", 0.0)
            if last_update > 0.0:
                gps_age = now - last_update
                if gps_age > self._gps_max_stale_sec:
                    self._record_gate_evaluation(
                        "gps_fresh", "FAIL", f"fix age {gps_age:.1f}s",
                    )
                    self._mark_gates_na(
                        "geofence", "operator_lock",
                        detail="gps_fresh failed",
                    )
                    self._record_decision(
                        None, "", "reject", f"GPS stale {gps_age:.1f}s",
                    )
                    logger.debug("GPS stale (%.1fs) — skipping autonomous eval", gps_age)
                    return
                self._record_gate_evaluation(
                    "gps_fresh", "PASS", f"fix age {gps_age:.1f}s",
                )
            else:
                self._record_gate_evaluation(
                    "gps_fresh", "N/A", "operator-provided position",
                )
        else:
            self._record_gate_evaluation("gps_fresh", "N/A", "no GPS source")

        # Check vehicle inside geofence
        get_lat_lon = getattr(mavlink, "get_lat_lon", None)
        if get_lat_lon is None:
            self._mark_gates_na(
                "geofence", "operator_lock",
                detail="no GPS position source",
            )
            self._record_decision(None, "", "reject", "no GPS position source")
            return
        try:
            lat, lon, _ = get_lat_lon()
        except Exception as exc:
            logger.warning(
                "Autonomous: GPS unavailable for geofence check: %s", exc,
            )
            self._mark_gates_na(
                "geofence", "operator_lock",
                detail="GPS read failed",
            )
            self._record_decision(None, "", "reject", "GPS read failed")
            return
        if lat is None or lon is None:
            self._mark_gates_na(
                "geofence", "operator_lock",
                detail="no GPS fix",
            )
            self._record_decision(None, "", "reject", "no GPS fix")
            return
        fence_dist = self._distance_to_fence_center(lat, lon)
        self._update_self_position(lat, lon, fence_dist)
        if not self.check_geofence(lat, lon):
            self._record_gate_evaluation(
                "geofence", "FAIL",
                f"{fence_dist:.0f}m of {self._geofence_radius_m:.0f}m",
            )
            self._mark_gates_na("operator_lock", detail="geofence failed")
            self._record_decision(
                None, "", "reject", f"outside geofence ({fence_dist:.0f}m)",
            )
            return
        self._record_gate_evaluation(
            "geofence", "PASS",
            f"{fence_dist:.0f}m of {self._geofence_radius_m:.0f}m",
        )

        # Operator lock requirement
        if self._require_operator_lock and self._operator_locked_track is None:
            self._record_gate_evaluation("operator_lock", "FAIL", "no soft-lock")
            self._record_decision(None, "", "reject", "no operator soft-lock")
            return
        if self._require_operator_lock:
            self._record_gate_evaluation(
                "operator_lock", "PASS",
                f"locked on track {self._operator_locked_track}",
            )
        else:
            self._record_gate_evaluation(
                "operator_lock", "N/A", "lock not required",
            )

        # Mark that we reached the full evaluation (past all early returns)
        self._last_evaluate_time = now

        # Evaluate tracks
        self._persistence.begin_frame()

        best_track = None
        best_frames = 0

        for track in tracks:
            # If operator lock required, only consider the locked track
            if self._require_operator_lock and track.track_id != self._operator_locked_track:
                continue
            # Class whitelist — fail-closed; warn operator if misconfigured (Fix 3)
            if not self._allowed_classes:
                if now - self._empty_classes_warn_time >= 30.0:
                    logger.warning(
                        "Autonomous controller enabled but allowed_classes is empty — "
                        "no targets will ever qualify. "
                        "Set [autonomous] allowed_classes in config.ini."
                    )
                    self._empty_classes_warn_time = now
                continue
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
            # All gates passed but no track qualifies yet — temporary wait.
            self._record_decision(None, "", "defer", "no qualifying track")
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
        self._strike_in_progress = True
        result = strike_cb(best_track.track_id)

        if result:
            self._last_strike_time = now
            self._record_decision(
                best_track.track_id, best_track.label, "engage",
                f"conf={best_track.confidence:.2f} frames={best_frames}",
            )
            audit_log.info(
                "AUTONOMOUS STRIKE CONFIRMED: track_id=%d", best_track.track_id
            )
        else:
            self._record_decision(
                best_track.track_id, best_track.label, "reject",
                "strike callback failed",
            )
            audit_log.warning(
                "AUTONOMOUS STRIKE FAILED: track_id=%d (strike_cb returned False)",
                best_track.track_id,
            )

    @property
    def suppressed(self) -> bool:
        """True when externally suppressed (e.g. camera loss)."""
        return self._suppressed

    @suppressed.setter
    def suppressed(self, value: bool) -> None:
        self._suppressed = value

    def has_active_evaluation(self) -> bool:
        """Return True if any track is being evaluated (frame counter > 0).

        Also checks staleness: if evaluate() hasn't run past its early
        returns recently (>3 s), counters are stale and we return False.
        """
        if not any(count > 0 for count in self._persistence.counts.values()):
            return False
        # Guard against stale counters from evaluate() returning early
        if self._last_evaluate_time == 0.0:
            return False
        if (time.monotonic() - self._last_evaluate_time) > 3.0:
            return False
        return True

    def clip_to_geofence(self, lat: float, lon: float) -> tuple[float, float]:
        """Clip a point to the nearest geofence boundary. Returns (lat, lon).

        For polygon geofences, binary-searches along the line from the point
        toward the polygon centroid.  For circular geofences, projects toward
        the circle centre along the radius.
        """
        if self.check_geofence(lat, lon):
            return (lat, lon)  # Already inside

        if self._geofence_polygon and len(self._geofence_polygon) >= 3:
            # Project toward centroid until inside
            cx = sum(p[0] for p in self._geofence_polygon) / len(self._geofence_polygon)
            cy = sum(p[1] for p in self._geofence_polygon) / len(self._geofence_polygon)
            # Binary search along line from point toward centroid
            for _ in range(20):  # 20 iterations gives ~1 m precision
                mid_lat = (lat + cx) / 2
                mid_lon = (lon + cy) / 2
                if self.check_geofence(mid_lat, mid_lon):
                    cx, cy = mid_lat, mid_lon
                else:
                    lat, lon = mid_lat, mid_lon
            # Verify result is actually inside (centroid of concave polygon
            # can be outside, which makes the binary search miss).
            if not self.check_geofence(cx, cy):
                for vx, vy in self._geofence_polygon:
                    if self.check_geofence(vx, vy):
                        return (vx, vy)
                # All vertices outside? Return original (shouldn't happen)
                return (lat, lon)
            return (cx, cy)

        # Circular geofence: project toward centre
        dist = haversine_m(lat, lon, self._geofence_lat, self._geofence_lon)
        if dist == 0:
            return (lat, lon)
        ratio = self._geofence_radius_m / dist
        clipped_lat = self._geofence_lat + (lat - self._geofence_lat) * ratio
        clipped_lon = self._geofence_lon + (lon - self._geofence_lon) * ratio
        return (clipped_lat, clipped_lon)

    def notify_strike_complete(self) -> None:
        """Called when a strike finishes (target lost or vehicle arrives)."""
        self._strike_in_progress = False

    # -- Dashboard / explainability API ------------------------------------

    def _distance_to_fence_center(self, lat: float, lon: float) -> float:
        """Haversine distance in metres from point to geofence reference center.

        For polygon geofences, the centroid of the vertices is used.
        """
        if self._geofence_polygon and len(self._geofence_polygon) >= 3:
            cx = sum(p[0] for p in self._geofence_polygon) / len(self._geofence_polygon)
            cy = sum(p[1] for p in self._geofence_polygon) / len(self._geofence_polygon)
            return haversine_m(lat, lon, cx, cy)
        return haversine_m(lat, lon, self._geofence_lat, self._geofence_lon)

    def _update_self_position(self, lat: float, lon: float, distance_m: float) -> None:
        with self._dashboard_lock:
            self._self_position = {
                "lat": float(lat),
                "lon": float(lon),
                "distance_m": float(distance_m),
            }

    def _record_gate_evaluation(self, gate_id: str, state: str, detail: str) -> None:
        """Record the outcome of a single gate check. Keyed by gate_id (5 gates)."""
        if gate_id not in GATE_IDS:
            raise ValueError(f"unknown gate_id: {gate_id!r}")
        if state not in GATE_STATES:
            raise ValueError(f"invalid gate state: {state!r}")
        with self._dashboard_lock:
            self._gate_cache[gate_id] = {"state": state, "detail": str(detail)}

    def _mark_gates_na(self, *gate_ids: str, detail: str = "") -> None:
        """Mark every given gate as N/A with a shared detail string.

        Used after an early-return from a FAILed gate so later gates on the
        same evaluate() path do not display stale PASS/FAIL from prior cycles.
        """
        for gid in gate_ids:
            self._record_gate_evaluation(gid, "N/A", detail)

    def _record_decision(
        self,
        track_id: int | None,
        label: str,
        action: str,
        reason: str,
        sha256: str | None = None,
    ) -> None:
        """Append a decision entry to the bounded explainability log (maxlen 200)."""
        if action not in DECISION_ACTIONS:
            raise ValueError(f"invalid action: {action!r}")
        entry: dict[str, Any] = {
            "ts": time.strftime("%H:%M:%S", time.localtime()),
            "track_id": track_id,
            "label": str(label),
            "action": action,
            "reason": str(reason),
        }
        if sha256:
            entry["sha256"] = str(sha256)
        with self._dashboard_lock:
            self._decision_log.append(entry)

    def set_mode(self, mode: str) -> None:
        """Set the display-level autonomy mode.

        Raises ValueError if mode is not one of ``dryrun``/``shadow``/``live``.
        """
        if mode not in AUTONOMY_MODES:
            raise ValueError(f"invalid mode: {mode!r}; must be one of {AUTONOMY_MODES}")
        with self._dashboard_lock:
            self._mode = mode
        audit_log.info("autonomy_mode_set mode=%s", mode)

    def get_mode(self) -> str:
        with self._dashboard_lock:
            return self._mode

    def get_dashboard_snapshot(
        self,
        *,
        callsign: str = "HYDRA-1",
        self_position: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Return a deep-copied JSON-safe snapshot for GET /api/autonomy/status.

        ``callsign`` is passed in by the web layer (pulled from runtime config).
        ``self_position`` may be supplied by the caller; if omitted, the most
        recently observed position from ``evaluate()`` is used (or None).
        """
        polygon_str = ";".join(
            f"{lat},{lon}" for lat, lon in self._geofence_polygon
        ) if self._geofence_polygon else ""
        is_polygon = bool(self._geofence_polygon) and len(self._geofence_polygon) >= 3
        shape = "POLYGON" if is_polygon else "CIRCLE"

        geofence = {
            "shape": shape,
            "radius_m": float(self._geofence_radius_m),
            "center_lat": float(self._geofence_lat),
            "center_lon": float(self._geofence_lon),
            "polygon": polygon_str,
        }

        criteria = {
            "min_confidence": float(self._min_confidence),
            "min_track_frames": int(self._min_track_frames),
            "strike_cooldown_sec": float(self._strike_cooldown),
            "gps_max_stale_sec": float(self._gps_max_stale_sec),
            "require_operator_lock": bool(self._require_operator_lock),
            "allowed_vehicle_modes": ",".join(self._allowed_modes),
            "allowed_classes": list(self._allowed_classes),
        }

        with self._dashboard_lock:
            mode = self._mode
            gates = [
                {"id": gid, "state": self._gate_cache[gid]["state"],
                 "detail": self._gate_cache[gid]["detail"]}
                for gid in GATE_IDS
            ]
            log = list(reversed(self._decision_log))  # newest-first
            pos = self._self_position

        if self_position is not None:
            pos = self_position

        return copy.deepcopy({
            "mode": mode,
            "enabled": bool(self.enabled),
            "callsign": str(callsign),
            "geofence": geofence,
            "self_position": pos,
            "criteria": criteria,
            "gates": gates,
            "log": log,
        })
