"""Regression tests for RF-hunt geofence waypoint skip-and-continue.

Previously, if `_geofence_waypoint` rejected a waypoint (outside the
fence with no clip callback wired), `_do_search` returned silently and
the vehicle was stranded at its current position. The hunt then aborted
with "no signal found" — a silent failure indistinguishable from a
legitimate negative result.

These tests verify that `_advance_to_next_sendable_wp` walks past
suppressed waypoints until it finds a sendable one, and exits cleanly
when every remaining waypoint is suppressed or a state transition
interrupts the loop.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hydra_detect.rf.hunt import HuntState, RFHuntController


def _make_mavlink():
    mav = MagicMock()
    mav.get_lat_lon.return_value = (34.05, -118.25, 50.0)
    mav.connected = True
    return mav


def _make_controller(mav=None, **kwargs):
    defaults = dict(
        mode="wifi",
        target_bssid="AA:BB:CC:DD:EE:FF",
        kismet_host="http://localhost:2501",
        poll_interval_sec=0.01,
    )
    defaults.update(kwargs)
    return RFHuntController(mav or _make_mavlink(), **defaults)


def test_advance_skips_suppressed_waypoint_and_sends_next():
    """If wp[0] is rejected by geofence (no clip), helper must advance to wp[1]."""
    mav = _make_mavlink()
    # First waypoint outside fence, rest inside.
    check_fn = MagicMock(side_effect=[False, True, True])
    ctrl = _make_controller(mav=mav, geofence_check=check_fn, geofence_clip=None)
    ctrl._waypoints = [
        (34.10, -118.30, 15.0),  # outside
        (34.05, -118.25, 15.0),  # inside
        (34.06, -118.26, 15.0),  # inside
    ]
    ctrl._wp_index = 0
    ctrl._set_state(HuntState.SEARCHING)

    ctrl._advance_to_next_sendable_wp()

    # wp_index should now point at the sent waypoint (index 1)
    assert ctrl._wp_index == 1
    # command_guided_to called exactly once with the second waypoint
    mav.command_guided_to.assert_called_once_with(34.05, -118.25, 15.0)


def test_advance_exhausts_without_sending_when_all_suppressed():
    """All remaining waypoints outside fence, no clip → no send, wp_index past end."""
    mav = _make_mavlink()
    check_fn = MagicMock(return_value=False)  # all suppressed
    ctrl = _make_controller(mav=mav, geofence_check=check_fn, geofence_clip=None)
    ctrl._waypoints = [
        (34.10, -118.30, 15.0),
        (34.11, -118.31, 15.0),
    ]
    ctrl._wp_index = 0
    ctrl._set_state(HuntState.SEARCHING)

    ctrl._advance_to_next_sendable_wp()

    assert ctrl._wp_index >= len(ctrl._waypoints)
    mav.command_guided_to.assert_not_called()


def test_advance_bails_on_state_transition_to_converged():
    """When MAX_CONSECUTIVE_CLIPS triggers CONVERGED mid-loop, stop advancing."""
    mav = _make_mavlink()
    # Always outside; clip returns same coords (no real clipping)
    check_fn = MagicMock(return_value=False)
    clip_fn = MagicMock(side_effect=lambda la, lo: (la, lo))
    ctrl = _make_controller(mav=mav, geofence_check=check_fn, geofence_clip=clip_fn)
    ctrl._waypoints = [
        (34.10, -118.30, 15.0),
        (34.11, -118.31, 15.0),
        (34.12, -118.32, 15.0),
        (34.13, -118.33, 15.0),
        (34.14, -118.34, 15.0),
    ]
    ctrl._wp_index = 0
    ctrl._set_state(HuntState.SEARCHING)

    ctrl._advance_to_next_sendable_wp()

    # After _MAX_CONSECUTIVE_CLIPS (3) clips, state should flip to CONVERGED
    # and the loop must bail rather than walk the whole pattern.
    assert ctrl.state == HuntState.CONVERGED
    assert ctrl._wp_index < len(ctrl._waypoints), (
        "Loop did not bail on CONVERGED — advanced past the state transition"
    )


def test_advance_sends_first_waypoint_when_all_inside():
    """Normal case: wp[0] inside fence, sends immediately."""
    mav = _make_mavlink()
    check_fn = MagicMock(return_value=True)
    ctrl = _make_controller(mav=mav, geofence_check=check_fn, geofence_clip=None)
    ctrl._waypoints = [(34.05, -118.25, 15.0), (34.06, -118.26, 15.0)]
    ctrl._wp_index = 0
    ctrl._set_state(HuntState.SEARCHING)

    ctrl._advance_to_next_sendable_wp()

    assert ctrl._wp_index == 0
    mav.command_guided_to.assert_called_once_with(34.05, -118.25, 15.0)


def test_advance_no_geofence_configured_sends_first():
    """Without any geofence callback, helper sends the current waypoint directly."""
    mav = _make_mavlink()
    ctrl = _make_controller(mav=mav)  # no geofence_check
    ctrl._waypoints = [(34.05, -118.25, 15.0)]
    ctrl._wp_index = 0
    ctrl._set_state(HuntState.SEARCHING)

    ctrl._advance_to_next_sendable_wp()

    mav.command_guided_to.assert_called_once_with(34.05, -118.25, 15.0)


def test_misconfig_warn_on_check_without_clip(caplog):
    """geofence_check without geofence_clip should log a WARNING at construction."""
    import logging
    caplog.set_level(logging.WARNING, logger="hydra_detect.rf.hunt")
    _make_controller(geofence_check=MagicMock(return_value=True), geofence_clip=None)

    assert any(
        "geofence_clip" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), f"Expected geofence misconfig WARNING; got {caplog.records}"


def test_no_warn_when_both_wired(caplog):
    """Both callbacks wired → no misconfig warning."""
    import logging
    caplog.set_level(logging.WARNING, logger="hydra_detect.rf.hunt")
    _make_controller(
        geofence_check=MagicMock(return_value=True),
        geofence_clip=MagicMock(side_effect=lambda la, lo: (la, lo)),
    )

    geofence_warns = [
        r for r in caplog.records
        if "geofence_clip" in r.message and r.levelno == logging.WARNING
    ]
    assert geofence_warns == [], f"Unexpected warnings: {geofence_warns}"
