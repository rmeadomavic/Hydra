"""Pixhawk first-run wizard — detect, diff, apply backend (#158 PR-A).

Pure orchestration functions, no FastAPI surface. The caller (web/server.py)
opens the mavutil connection and threads it through these helpers; the wizard
never owns the connection lifecycle. This keeps the module trivially testable
with a MagicMock standing in for ``mavutil.mavlink_connection``.

PR-A scope: detect FC firmware/version/frame, load profile param pack, compute
a diff against live params, apply with per-name results, capture/restore
backup. PR-B adds the operator UI, the preflight complete-gate, and the
override-with-reason flow.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROFILES_DIR = _REPO_ROOT / "hydra_detect" / "profiles"


# ---------------------------------------------------------------------------
# Firmware mapping (per MAVLink MAV_TYPE — see issue #158)
# ---------------------------------------------------------------------------
#
# AUTOPILOT_VERSION carries no field that disambiguates ArduPilot variants:
# Copter, Plane, Rover and Sub all report MAV_AUTOPILOT_ARDUPILOTMEGA=3 in
# HEARTBEAT.autopilot, and AUTOPILOT_VERSION has no `autopilot_id` field at
# all. The variant lives in HEARTBEAT.type (MAV_TYPE), which encodes the
# vehicle frame (QUADROTOR, FIXED_WING, GROUND_ROVER, SURFACE_BOAT, ...).
#
# We derive `firmware` from HEARTBEAT.type and use HEARTBEAT.autopilot only as
# a sanity check that the device is actually ArduPilot (vs. PX4 / generic).
# ArduRover covers both UGV (GROUND_ROVER) and USV (SURFACE_BOAT) — same
# firmware binary, different vehicle type — by design.

_FIRMWARE_BY_MAV_TYPE: dict[int, str] = {
    # ArduCopter family
    2: "ArduCopter",   # MAV_TYPE_QUADROTOR
    3: "ArduCopter",   # MAV_TYPE_COAXIAL
    4: "ArduCopter",   # MAV_TYPE_HELICOPTER
    13: "ArduCopter",  # MAV_TYPE_HEXAROTOR
    14: "ArduCopter",  # MAV_TYPE_OCTOROTOR
    15: "ArduCopter",  # MAV_TYPE_TRICOPTER
    # ArduPlane family
    1: "ArduPlane",    # MAV_TYPE_FIXED_WING
    16: "ArduPlane",   # MAV_TYPE_VTOL_DUOROTOR
    17: "ArduPlane",   # MAV_TYPE_VTOL_QUADROTOR
    19: "ArduPlane",   # MAV_TYPE_VTOL_TILTROTOR
    20: "ArduPlane",   # MAV_TYPE_VTOL_RESERVED2
    21: "ArduPlane",   # MAV_TYPE_VTOL_RESERVED3
    22: "ArduPlane",   # MAV_TYPE_VTOL_RESERVED4
    23: "ArduPlane",   # MAV_TYPE_VTOL_RESERVED5
    # ArduRover family (UGV + USV — same firmware, different vehicle)
    10: "ArduRover",   # MAV_TYPE_GROUND_ROVER (UGV)
    11: "ArduRover",   # MAV_TYPE_SURFACE_BOAT (USV)
    # ArduSub
    12: "ArduSub",     # MAV_TYPE_SUBMARINE
}

_MAV_AUTOPILOT_ARDUPILOTMEGA = 3


def _firmware_from_mav_type(mav_type: int) -> str:
    """Return canonical firmware name for a MAV_TYPE int, or 'unknown'."""
    return _FIRMWARE_BY_MAV_TYPE.get(int(mav_type), "unknown")


# ---------------------------------------------------------------------------
# detect_fc
# ---------------------------------------------------------------------------

def _decode_flight_sw_version(packed: int) -> str:
    """Decode pymavlink ``flight_sw_version`` (uint32) to a ``MAJOR.MINOR.PATCH`` string.

    Packing: MSB = major, next = minor, next = patch, LSB = FW_TYPE (we drop).
    """
    try:
        v = int(packed)
    except (TypeError, ValueError):
        return "0.0.0"
    major = (v >> 24) & 0xFF
    minor = (v >> 16) & 0xFF
    patch = (v >> 8) & 0xFF
    return f"{major}.{minor}.{patch}"


# Per-message wait windows inside detect_fc. Short by design: HEARTBEAT
# streams at 1 Hz from ArduPilot by default, AUTOPILOT_VERSION comes back
# within a frame or two of REQUEST_MESSAGE. Total budget for detect_fc is
# bounded by the caller's ``timeout`` (used as a ceiling, not a per-msg).
_HEARTBEAT_WAIT_SEC = 1.0
_AUTOPILOT_VERSION_WAIT_SEC = 1.0


def detect_fc(connection: Any, timeout: float = 5.0) -> dict[str, Any]:
    """Detect the connected flight controller's firmware, version, and frame.

    Reads HEARTBEAT (for vehicle type + autopilot family) and AUTOPILOT_VERSION
    (for the version string) off ``connection`` and returns the canonical
    Hydra shape.

    The previous implementation derived ``firmware`` from a non-existent
    ``AUTOPILOT_VERSION.autopilot_id`` field; the correct disambiguator is
    HEARTBEAT.type (MAV_TYPE). HEARTBEAT.autopilot is used as a sanity check
    that the device is ArduPilot (MAV_AUTOPILOT_ARDUPILOTMEGA=3) — non-
    ArduPilot autopilots return ``firmware="unknown"``.

    Args:
        connection: An opened ``mavutil.mavlink_connection`` instance.
        timeout:    Soft total budget; this function uses short per-message
                    waits internally (1 s each for HEARTBEAT and
                    AUTOPILOT_VERSION). ``timeout`` is kept for back-compat
                    and as an upper bound on the per-msg waits when shorter.

    Returns:
        ``{"firmware": str, "version": str, "frame_type": int|None, "autopilot_id": int|None}``.
        On HEARTBEAT timeout all fields are unknown/None — no exception. On
        AUTOPILOT_VERSION timeout the HEARTBEAT-derived firmware/frame are
        preserved and ``version="unknown"``.
    """
    hb_wait = min(_HEARTBEAT_WAIT_SEC, float(timeout))
    av_wait = min(_AUTOPILOT_VERSION_WAIT_SEC, float(timeout))

    hb = connection.recv_match(
        type="HEARTBEAT",
        blocking=True,
        timeout=hb_wait,
    )
    if hb is None:
        return {
            "firmware": "unknown",
            "version": "unknown",
            "frame_type": None,
            "autopilot_id": None,
        }

    try:
        mav_type = int(getattr(hb, "type", 0) or 0)
    except (TypeError, ValueError):
        mav_type = 0
    try:
        autopilot = int(getattr(hb, "autopilot", 0) or 0)
    except (TypeError, ValueError):
        autopilot = 0

    if autopilot == _MAV_AUTOPILOT_ARDUPILOTMEGA:
        firmware = _firmware_from_mav_type(mav_type)
    else:
        firmware = "unknown"

    # Best-effort: nudge the FC to emit AUTOPILOT_VERSION via REQUEST_MESSAGE
    # (MAV_CMD 512). Many ArduPilot builds emit it unsolicited on connect;
    # the request is harmless either way and silent on failure (server.py
    # already issues MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES=520 in parallel).
    try:
        target_system = getattr(connection, "target_system", 1)
        target_component = getattr(connection, "target_component", 1)
        connection.mav.command_long_send(
            target_system, target_component,
            512,  # MAV_CMD_REQUEST_MESSAGE
            0,
            148,  # MAVLINK_MSG_ID_AUTOPILOT_VERSION
            0, 0, 0, 0, 0, 0,
        )
    except Exception:
        pass

    av = connection.recv_match(
        type="AUTOPILOT_VERSION",
        blocking=True,
        timeout=av_wait,
    )
    if av is None:
        version = "unknown"
    else:
        flight_sw = getattr(av, "flight_sw_version", 0)
        version = _decode_flight_sw_version(flight_sw)

    return {
        "firmware": firmware,
        "version": version,
        "frame_type": mav_type,
        "autopilot_id": autopilot,
    }


# ---------------------------------------------------------------------------
# load_param_pack
# ---------------------------------------------------------------------------

def load_param_pack(profile: str) -> list[tuple[str, float]]:
    """Load ``param_pack.param`` for ``profile`` as an ordered list.

    File format: standard ArduPilot ``.param`` — ``NAME,value`` per line. Lines
    starting with ``#`` and blank lines are skipped.

    Args:
        profile: One of ``drone_10in`` / ``ugv`` / ``usv`` (or any directory
                 under ``hydra_detect/profiles/`` carrying a ``param_pack.param``).

    Returns:
        List of ``(name, value)`` tuples in source order. Float values; integer
        params from the source file are returned as ``float`` so callers don't
        have to type-switch.

    Raises:
        FileNotFoundError: No ``param_pack.param`` exists for the profile.
        ValueError: Malformed line (no comma, non-numeric value).
    """
    path = _PROFILES_DIR / profile / "param_pack.param"
    if not path.exists():
        raise FileNotFoundError(
            f"No param_pack.param for profile '{profile}' at {path}"
        )

    entries: list[tuple[str, float]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "," not in line:
                raise ValueError(
                    f"{path}:{lineno}: expected 'NAME,value', got {line!r}"
                )
            name, _, value_str = line.partition(",")
            name = name.strip()
            value_str = value_str.strip()
            if not name:
                raise ValueError(f"{path}:{lineno}: empty param name")
            try:
                value = float(value_str)
            except ValueError as exc:
                raise ValueError(
                    f"{path}:{lineno}: non-numeric value {value_str!r}"
                ) from exc
            entries.append((name, value))
    return entries


# ---------------------------------------------------------------------------
# compute_diff
# ---------------------------------------------------------------------------

_DIFF_TOL = 1e-6


def compute_diff(
    live_params: dict[str, float],
    pack: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    """Compute the per-param diff between ``live_params`` and ``pack``.

    Result rows: ``{"name", "current", "target", "action", "delta"}``.
        action ``"skip"``   — name in live_params and matches target within ``1e-6``.
        action ``"add"``    — name not in live_params (current is ``None``).
        action ``"change"`` — name in live_params but mismatched value.

    ``delta`` is ``current - target`` for change rows (i.e. how far the live
    value is from where it needs to be — negative means it's below target),
    and ``None`` for add/skip.
    """
    rows: list[dict[str, Any]] = []
    for name, target in pack:
        if name not in live_params:
            rows.append({
                "name": name,
                "current": None,
                "target": float(target),
                "action": "add",
                "delta": None,
            })
            continue
        current = float(live_params[name])
        if abs(current - float(target)) <= _DIFF_TOL:
            rows.append({
                "name": name,
                "current": current,
                "target": float(target),
                "action": "skip",
                "delta": None,
            })
        else:
            rows.append({
                "name": name,
                "current": current,
                "target": float(target),
                "action": "change",
                "delta": current - float(target),
            })
    return rows


# ---------------------------------------------------------------------------
# apply_pack
# ---------------------------------------------------------------------------

# MAV_PARAM_TYPE_REAL32 = 9; importing the pymavlink enum at module top would
# force a pymavlink dependency at import time. Hardcoding 9 keeps the module
# import-clean for tests that don't need pymavlink.
_MAV_PARAM_TYPE_REAL32 = 9


def apply_pack(
    connection: Any,
    diff: list[dict[str, Any]],
    dry_run: bool = False,
    ack_timeout: float = 1.0,
) -> list[dict[str, Any]]:
    """Apply rows from ``diff`` with ``action`` in ``{"change", "add"}``.

    For each applied row:
      * Calls ``connection.mav.param_set_send(target_system, target_component,
        name.encode(), value, MAV_PARAM_TYPE_REAL32)``.
      * Waits up to ``ack_timeout`` for a ``PARAM_VALUE`` message whose
        ``param_id`` matches; the observed value becomes ``post_value``.

    Args:
        connection:   Opened mavutil connection. Must expose ``mav``,
                      ``target_system``, ``target_component``.
        diff:         Result of :func:`compute_diff`.
        dry_run:      If True, do not call ``param_set_send`` — return a row
                      per applied entry with ``applied=False, error="dry_run"``.
        ack_timeout:  Seconds to wait for a per-name PARAM_VALUE ack.

    Returns:
        One result dict per ``change``/``add`` row in source order:
        ``{"name", "applied": bool, "error": str|None, "post_value": float|None}``.
        ``"skip"`` rows are not included in the returned list.
    """
    results: list[dict[str, Any]] = []
    target_system = getattr(connection, "target_system", 1)
    target_component = getattr(connection, "target_component", 1)

    for row in diff:
        action = row.get("action")
        if action not in ("change", "add"):
            continue
        name = str(row.get("name", ""))
        value = float(row.get("target", 0.0))

        if dry_run:
            results.append({
                "name": name,
                "applied": False,
                "error": "dry_run",
                "post_value": None,
            })
            continue

        try:
            connection.mav.param_set_send(
                target_system,
                target_component,
                name.encode("utf-8"),
                value,
                _MAV_PARAM_TYPE_REAL32,
            )
        except Exception as exc:
            results.append({
                "name": name,
                "applied": False,
                "error": f"send failed: {exc}",
                "post_value": None,
            })
            continue

        post_value = _await_param_value(connection, name, ack_timeout)
        if post_value is None:
            results.append({
                "name": name,
                "applied": False,
                "error": "timeout waiting for PARAM_VALUE ack",
                "post_value": None,
            })
        else:
            results.append({
                "name": name,
                "applied": True,
                "error": None,
                "post_value": post_value,
            })

    return results


# ---------------------------------------------------------------------------
# capture_backup / restore_backup
# ---------------------------------------------------------------------------

def capture_backup(
    connection: Any,
    names: list[str],
    per_name_timeout: float = 1.0,
) -> dict[str, float | None]:
    """Snapshot the current value of each name in ``names``.

    For each name, sends ``PARAM_REQUEST_READ`` and waits up to
    ``per_name_timeout`` seconds for the matching ``PARAM_VALUE``. Names with
    no response within the window map to ``None``.

    Args:
        connection:       Opened mavutil connection.
        names:            Param names to snapshot.
        per_name_timeout: Seconds to wait per name.

    Returns:
        ``{name: float|None}``.
    """
    target_system = getattr(connection, "target_system", 1)
    target_component = getattr(connection, "target_component", 1)
    snapshot: dict[str, float | None] = {}

    for name in names:
        try:
            connection.mav.param_request_read_send(
                target_system,
                target_component,
                name.encode("utf-8"),
                -1,  # param_index = -1 means look up by name
            )
        except Exception as exc:
            logger.warning("param_request_read failed for %s: %s", name, exc)
            snapshot[name] = None
            continue
        snapshot[name] = _await_param_value(connection, name, per_name_timeout)

    return snapshot


def restore_backup(
    connection: Any,
    backup: dict[str, float | None],
    ack_timeout: float = 1.0,
) -> list[dict[str, Any]]:
    """Apply each ``(name, value)`` in ``backup`` back to the FC.

    Skips names whose value is ``None`` — those weren't captured cleanly and we
    do not want to write a synthetic value back.

    Args:
        connection:   Opened mavutil connection.
        backup:       Output of :func:`capture_backup` (or any compatible map).
        ack_timeout:  Seconds to wait per name for PARAM_VALUE ack.

    Returns:
        One result dict per restored name (same shape as :func:`apply_pack`).
        Skipped (None-valued) names appear with ``applied=False,
        error="no captured value"``.
    """
    target_system = getattr(connection, "target_system", 1)
    target_component = getattr(connection, "target_component", 1)
    results: list[dict[str, Any]] = []

    for name, value in backup.items():
        if value is None:
            results.append({
                "name": name,
                "applied": False,
                "error": "no captured value",
                "post_value": None,
            })
            continue

        try:
            connection.mav.param_set_send(
                target_system,
                target_component,
                name.encode("utf-8"),
                float(value),
                _MAV_PARAM_TYPE_REAL32,
            )
        except Exception as exc:
            results.append({
                "name": name,
                "applied": False,
                "error": f"send failed: {exc}",
                "post_value": None,
            })
            continue

        post_value = _await_param_value(connection, name, ack_timeout)
        if post_value is None:
            results.append({
                "name": name,
                "applied": False,
                "error": "timeout waiting for PARAM_VALUE ack",
                "post_value": None,
            })
        else:
            results.append({
                "name": name,
                "applied": True,
                "error": None,
                "post_value": post_value,
            })

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _await_param_value(connection: Any, name: str, timeout: float) -> float | None:
    """Wait up to ``timeout`` seconds for a PARAM_VALUE matching ``name``.

    Returns the observed ``param_value`` as ``float``, or ``None`` on timeout
    or shape mismatch. Discards PARAM_VALUE messages whose ``param_id`` does
    not match ``name`` (other params may be streaming in concurrently).
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        msg = connection.recv_match(
            type="PARAM_VALUE",
            blocking=True,
            timeout=min(remaining, 1.0),
        )
        if msg is None:
            continue
        param_id = getattr(msg, "param_id", "")
        if isinstance(param_id, (bytes, bytearray)):
            try:
                param_id = param_id.decode("utf-8", errors="replace")
            except Exception:
                param_id = ""
        param_id = str(param_id).rstrip("\x00").strip()
        if param_id != name:
            continue
        try:
            return float(getattr(msg, "param_value", 0.0))
        except (TypeError, ValueError):
            return None
