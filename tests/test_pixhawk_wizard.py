"""Tests for hydra_detect.web.pixhawk_wizard (#158 PR-A).

Pure-function tests — no FastAPI surface, no real pymavlink. A MagicMock
stands in for ``mavutil.mavlink_connection``; PARAM_VALUE / AUTOPILOT_VERSION
responses are scripted via the ``recv_match`` side_effect.

Endpoint tests are intentionally deferred to PR-B.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hydra_detect.web import pixhawk_wizard
from hydra_detect.web.pixhawk_wizard import (
    apply_pack,
    capture_backup,
    compute_diff,
    detect_fc,
    load_param_pack,
    restore_backup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_param_value(name: str, value: float) -> SimpleNamespace:
    """Build a fake PARAM_VALUE-like message object."""
    return SimpleNamespace(
        param_id=name,
        param_value=float(value),
        get_type=lambda: "PARAM_VALUE",
    )


def _make_autopilot_version(
    flight_sw_version: int = 0,
) -> SimpleNamespace:
    """Build a fake AUTOPILOT_VERSION-like message object."""
    return SimpleNamespace(
        flight_sw_version=flight_sw_version,
        get_type=lambda: "AUTOPILOT_VERSION",
    )


def _make_heartbeat(
    mav_type: int = 2,
    autopilot: int = 3,
) -> SimpleNamespace:
    """Build a fake HEARTBEAT-like message object.

    ``mav_type`` defaults to QUADROTOR (2) and ``autopilot`` to
    MAV_AUTOPILOT_ARDUPILOTMEGA (3), the dominant SORCC drone case.
    """
    return SimpleNamespace(
        type=mav_type,
        autopilot=autopilot,
        get_type=lambda: "HEARTBEAT",
    )


def _scripted_conn(recv_responses: list) -> MagicMock:
    """Build a MagicMock connection that returns ``recv_responses`` in order from ``recv_match``."""
    conn = MagicMock()
    conn.target_system = 1
    conn.target_component = 1
    iterator = iter(recv_responses)

    def _recv(*_args, **_kwargs):
        try:
            return next(iterator)
        except StopIteration:
            return None

    conn.recv_match.side_effect = _recv
    return conn


# Encoding: major=4, minor=5, patch=7 → 0x04050700
_FLIGHT_SW_4_5_7 = (4 << 24) | (5 << 16) | (7 << 8)


# ---------------------------------------------------------------------------
# detect_fc
# ---------------------------------------------------------------------------

def test_detect_fc_returns_firmware_version_frame():
    """ArduCopter QUADROTOR: HEARTBEAT type=2, autopilot=3 → ArduCopter."""
    hb = _make_heartbeat(mav_type=2, autopilot=3)
    av = _make_autopilot_version(flight_sw_version=_FLIGHT_SW_4_5_7)
    conn = _scripted_conn([hb, av])
    info = detect_fc(conn)
    assert info["firmware"] == "ArduCopter"
    assert info["version"] == "4.5.7"
    assert info["frame_type"] == 2
    assert info["autopilot_id"] == 3


def test_detect_fc_unknown_autopilot():
    """HEARTBEAT.autopilot=12 (PX4) → firmware unknown, even if MAV_TYPE matches."""
    hb = _make_heartbeat(mav_type=2, autopilot=12)
    av = _make_autopilot_version(flight_sw_version=_FLIGHT_SW_4_5_7)
    conn = _scripted_conn([hb, av])
    info = detect_fc(conn)
    assert info["firmware"] == "unknown"
    assert info["autopilot_id"] == 12


def test_detect_fc_ardurover_ugv():
    """MAV_TYPE_GROUND_ROVER (10) → ArduRover."""
    hb = _make_heartbeat(mav_type=10, autopilot=3)
    av = _make_autopilot_version(flight_sw_version=_FLIGHT_SW_4_5_7)
    conn = _scripted_conn([hb, av])
    info = detect_fc(conn)
    assert info["firmware"] == "ArduRover"
    assert info["frame_type"] == 10


def test_detect_fc_ardurover_usv():
    """MAV_TYPE_SURFACE_BOAT (11) also maps to ArduRover — same firmware binary."""
    hb = _make_heartbeat(mav_type=11, autopilot=3)
    av = _make_autopilot_version(flight_sw_version=_FLIGHT_SW_4_5_7)
    conn = _scripted_conn([hb, av])
    info = detect_fc(conn)
    assert info["firmware"] == "ArduRover"
    assert info["frame_type"] == 11


def test_detect_fc_arduplane():
    """MAV_TYPE_FIXED_WING (1) → ArduPlane."""
    hb = _make_heartbeat(mav_type=1, autopilot=3)
    av = _make_autopilot_version(flight_sw_version=_FLIGHT_SW_4_5_7)
    conn = _scripted_conn([hb, av])
    info = detect_fc(conn)
    assert info["firmware"] == "ArduPlane"
    assert info["frame_type"] == 1


def test_detect_fc_arduplane_vtol():
    """MAV_TYPE_VTOL_TILTROTOR (19) → ArduPlane (VTOL variant)."""
    hb = _make_heartbeat(mav_type=19, autopilot=3)
    av = _make_autopilot_version(flight_sw_version=_FLIGHT_SW_4_5_7)
    conn = _scripted_conn([hb, av])
    info = detect_fc(conn)
    assert info["firmware"] == "ArduPlane"
    assert info["frame_type"] == 19


def test_detect_fc_unknown_mav_type():
    """Unknown MAV_TYPE (99) on ArduPilot autopilot → firmware unknown."""
    hb = _make_heartbeat(mav_type=99, autopilot=3)
    av = _make_autopilot_version(flight_sw_version=_FLIGHT_SW_4_5_7)
    conn = _scripted_conn([hb, av])
    info = detect_fc(conn)
    assert info["firmware"] == "unknown"
    assert info["frame_type"] == 99
    assert info["autopilot_id"] == 3


def test_detect_fc_heartbeat_timeout():
    """No HEARTBEAT → all-unknown / None, no exception."""
    conn = _scripted_conn([None])
    info = detect_fc(conn, timeout=0.01)
    assert info == {
        "firmware": "unknown",
        "version": "unknown",
        "frame_type": None,
        "autopilot_id": None,
    }


def test_detect_fc_version_timeout_keeps_firmware():
    """HEARTBEAT arrives, AUTOPILOT_VERSION times out → firmware preserved, version=unknown."""
    hb = _make_heartbeat(mav_type=2, autopilot=3)
    conn = _scripted_conn([hb, None])
    info = detect_fc(conn, timeout=0.01)
    assert info["firmware"] == "ArduCopter"
    assert info["version"] == "unknown"
    assert info["frame_type"] == 2
    assert info["autopilot_id"] == 3


# ---------------------------------------------------------------------------
# load_param_pack
# ---------------------------------------------------------------------------

def test_load_param_pack_drone_10in():
    pack = load_param_pack("drone_10in")
    assert pack, "drone_10in pack should be non-empty"
    by_name = dict(pack)
    assert "FENCE_ENABLE" in by_name
    assert by_name["FENCE_ENABLE"] == pytest.approx(1.0)
    # SERIAL2_BAUD is 921 (kBaud shorthand in ArduPilot), not 921600
    assert by_name["SERIAL2_BAUD"] == pytest.approx(921.0)


def test_load_param_pack_ugv_and_usv_differ_in_content():
    ugv = dict(load_param_pack("ugv"))
    usv = dict(load_param_pack("usv"))
    # USV is the only profile with FRAME_CLASS=2 (boat selector)
    assert usv.get("FRAME_CLASS") == pytest.approx(2.0)
    assert "FRAME_CLASS" not in ugv


def test_load_param_pack_missing_profile():
    with pytest.raises(FileNotFoundError):
        load_param_pack("does_not_exist_xyz")


# ---------------------------------------------------------------------------
# compute_diff
# ---------------------------------------------------------------------------

def test_compute_diff_change_action():
    live = {"SERIAL2_BAUD": 57.0}
    pack = [("SERIAL2_BAUD", 921.0)]
    rows = compute_diff(live, pack)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "SERIAL2_BAUD"
    assert row["current"] == pytest.approx(57.0)
    assert row["target"] == pytest.approx(921.0)
    assert row["action"] == "change"
    assert row["delta"] == pytest.approx(-864.0)


def test_compute_diff_skip_when_match():
    live = {"FENCE_ENABLE": 1.0}
    pack = [("FENCE_ENABLE", 1.0)]
    rows = compute_diff(live, pack)
    assert rows[0]["action"] == "skip"
    assert rows[0]["delta"] is None


def test_compute_diff_skip_within_tolerance():
    live = {"FENCE_ENABLE": 1.0000001}
    pack = [("FENCE_ENABLE", 1.0)]
    rows = compute_diff(live, pack)
    assert rows[0]["action"] == "skip"


def test_compute_diff_add_when_missing():
    live: dict[str, float] = {}
    pack = [("SR1_POSITION", 5.0)]
    rows = compute_diff(live, pack)
    assert rows[0]["action"] == "add"
    assert rows[0]["current"] is None
    assert rows[0]["target"] == pytest.approx(5.0)
    assert rows[0]["delta"] is None


# ---------------------------------------------------------------------------
# apply_pack
# ---------------------------------------------------------------------------

def test_apply_pack_records_per_name_result():
    diff = [
        {"name": "FENCE_ENABLE", "current": 0.0, "target": 1.0,
         "action": "change", "delta": 1.0},
        {"name": "SR1_POSITION", "current": None, "target": 5.0,
         "action": "add", "delta": None},
        {"name": "ARMING_CHECK", "current": 1.0, "target": 1.0,
         "action": "skip", "delta": None},
    ]
    # Two acks, one per applied row, in the same order
    conn = _scripted_conn([
        _make_param_value("FENCE_ENABLE", 1.0),
        _make_param_value("SR1_POSITION", 5.0),
    ])

    results = apply_pack(conn, diff)

    assert [r["name"] for r in results] == ["FENCE_ENABLE", "SR1_POSITION"]
    assert all(r["applied"] for r in results)
    assert results[0]["post_value"] == pytest.approx(1.0)
    assert results[1]["post_value"] == pytest.approx(5.0)
    # Skip rows do not get a param_set_send call
    assert conn.mav.param_set_send.call_count == 2


def test_apply_pack_timeout_marks_failed():
    diff = [
        {"name": "FENCE_ENABLE", "current": 0.0, "target": 1.0,
         "action": "change", "delta": 1.0},
    ]
    conn = _scripted_conn([None])
    results = apply_pack(conn, diff, ack_timeout=0.01)
    assert len(results) == 1
    row = results[0]
    assert row["applied"] is False
    assert "timeout" in row["error"].lower()
    assert row["post_value"] is None


def test_apply_pack_dry_run_skips_send():
    diff = [
        {"name": "FENCE_ENABLE", "current": 0.0, "target": 1.0,
         "action": "change", "delta": 1.0},
    ]
    conn = _scripted_conn([])
    results = apply_pack(conn, diff, dry_run=True)
    assert len(results) == 1
    assert results[0]["applied"] is False
    assert results[0]["error"] == "dry_run"
    conn.mav.param_set_send.assert_not_called()


# ---------------------------------------------------------------------------
# capture_backup
# ---------------------------------------------------------------------------

def test_capture_backup_reads_all_names():
    names = ["FENCE_ENABLE", "SERIAL2_BAUD", "MISSING_PARAM"]
    # First two respond; third times out
    conn = _scripted_conn([
        _make_param_value("FENCE_ENABLE", 1.0),
        _make_param_value("SERIAL2_BAUD", 921.0),
        None,
    ])
    snap = capture_backup(conn, names, per_name_timeout=0.01)
    assert set(snap.keys()) == set(names)
    assert snap["FENCE_ENABLE"] == pytest.approx(1.0)
    assert snap["SERIAL2_BAUD"] == pytest.approx(921.0)
    assert snap["MISSING_PARAM"] is None
    # Read sent for each name (3 names = 3 reads)
    assert conn.mav.param_request_read_send.call_count == 3


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------

def test_restore_backup_applies_all():
    backup = {"FENCE_ENABLE": 0.0, "SERIAL2_BAUD": 57.0}
    conn = _scripted_conn([
        _make_param_value("FENCE_ENABLE", 0.0),
        _make_param_value("SERIAL2_BAUD", 57.0),
    ])
    results = restore_backup(conn, backup)
    assert len(results) == 2
    assert all(r["applied"] for r in results)
    by_name = {r["name"]: r for r in results}
    assert by_name["FENCE_ENABLE"]["post_value"] == pytest.approx(0.0)
    assert by_name["SERIAL2_BAUD"]["post_value"] == pytest.approx(57.0)
    assert conn.mav.param_set_send.call_count == 2


def test_restore_backup_skips_none_values():
    backup = {"FENCE_ENABLE": 1.0, "MISSING_PARAM": None}
    conn = _scripted_conn([
        _make_param_value("FENCE_ENABLE", 1.0),
    ])
    results = restore_backup(conn, backup)
    assert len(results) == 2
    by_name = {r["name"]: r for r in results}
    assert by_name["FENCE_ENABLE"]["applied"] is True
    assert by_name["MISSING_PARAM"]["applied"] is False
    assert by_name["MISSING_PARAM"]["error"] == "no captured value"
    # MISSING_PARAM must NOT trigger a param_set_send
    assert conn.mav.param_set_send.call_count == 1


# ---------------------------------------------------------------------------
# Internal helpers (regression coverage)
# ---------------------------------------------------------------------------

def test_decode_flight_sw_version_zero():
    assert pixhawk_wizard._decode_flight_sw_version(0) == "0.0.0"


def test_decode_flight_sw_version_packed():
    packed = (4 << 24) | (6 << 16) | (1 << 8)
    assert pixhawk_wizard._decode_flight_sw_version(packed) == "4.6.1"
