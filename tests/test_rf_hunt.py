"""Tests for RF hunt controller state machine."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.rf.hunt import HuntState, RFHuntController


def _make_mavlink(*, lat=34.05, lon=-118.25, alt=15.0):
    """Build a mock MAVLinkIO."""
    mav = MagicMock()
    mav.get_lat_lon.return_value = (lat, lon, alt)
    mav.get_position_string.return_value = f"{lat:.5f},{lon:.5f}"
    mav.command_guided_to.return_value = True
    mav.send_statustext = MagicMock()
    mav.connected = True
    return mav


def _make_controller(mav=None, **overrides):
    """Build an RFHuntController with test defaults."""
    if mav is None:
        mav = _make_mavlink()
    defaults = dict(
        mode="wifi",
        target_bssid="AA:BB:CC:DD:EE:FF",
        kismet_host="http://localhost:2501",
        search_area_m=50.0,
        search_spacing_m=10.0,
        search_alt_m=15.0,
        rssi_threshold_dbm=-80.0,
        rssi_converge_dbm=-40.0,
        poll_interval_sec=0.01,
        arrival_tolerance_m=3.0,
    )
    defaults.update(overrides)
    return RFHuntController(mav, **defaults)


class TestHuntInit:
    def test_initial_state_is_idle(self):
        ctrl = _make_controller()
        assert ctrl.state == HuntState.IDLE

    def test_no_mavlink(self):
        ctrl = RFHuntController(
            None, mode="wifi", target_bssid="AA:BB:CC:DD:EE:FF",
        )
        assert ctrl._mavlink is None
        assert ctrl.start() is False

    def test_get_status(self):
        ctrl = _make_controller()
        status = ctrl.get_status()
        assert status["state"] == "idle"
        assert status["mode"] == "wifi"
        assert "AA:BB:CC:DD:EE:FF" in status["target"]

    def test_sdr_target_in_status(self):
        ctrl = _make_controller(mode="sdr", target_bssid=None, target_freq_mhz=915.0)
        status = ctrl.get_status()
        assert "915" in status["target"]


class TestHuntStart:
    @patch("hydra_detect.rf.hunt.KismetClient")
    def test_start_fails_without_mavlink(self, mock_kismet_cls):
        ctrl = RFHuntController(
            None,
            mode="wifi",
            target_bssid="AA:BB:CC:DD:EE:FF",
        )
        assert ctrl.start() is False

    @patch("hydra_detect.rf.hunt.KismetClient")
    def test_start_fails_without_kismet(self, mock_kismet_cls):
        mock_kismet_cls.return_value.check_connection.return_value = False
        mav = _make_mavlink()
        ctrl = RFHuntController(
            mav, mode="wifi", target_bssid="AA:BB:CC:DD:EE:FF",
        )
        # Replace the internally-created client with our mock
        ctrl._kismet = mock_kismet_cls.return_value
        assert ctrl.start() is False

    @patch("hydra_detect.rf.hunt.KismetClient")
    def test_start_fails_without_gps(self, mock_kismet_cls):
        mock_kismet_cls.return_value.check_connection.return_value = True
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        ctrl = RFHuntController(
            mav, mode="wifi", target_bssid="AA:BB:CC:DD:EE:FF",
        )
        ctrl._kismet = mock_kismet_cls.return_value
        assert ctrl.start() is False

    @patch("hydra_detect.rf.hunt.KismetClient")
    def test_start_succeeds(self, mock_kismet_cls):
        mock_kismet_cls.return_value.check_connection.return_value = True
        mav = _make_mavlink()
        ctrl = RFHuntController(
            mav, mode="wifi", target_bssid="AA:BB:CC:DD:EE:FF",
            poll_interval_sec=0.01,
        )
        ctrl._kismet = mock_kismet_cls.return_value
        ctrl._kismet.get_rssi.return_value = None  # no signal yet

        result = ctrl.start()
        assert result is True
        assert ctrl.state == HuntState.SEARCHING

        # Clean up
        ctrl.stop()
        assert ctrl.state == HuntState.ABORTED


class TestHuntStateTransitions:
    def test_search_to_homing_on_signal(self):
        """When RSSI exceeds threshold during search, switch to HOMING."""
        ctrl = _make_controller()
        ctrl._filter.reset()
        ctrl._set_state(HuntState.SEARCHING)

        # Simulate Kismet returning strong signal
        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = -60.0  # above threshold

        ctrl._do_search()
        assert ctrl.state == HuntState.HOMING

    def test_search_no_signal_continues(self):
        ctrl = _make_controller()
        ctrl._set_state(HuntState.SEARCHING)
        ctrl._waypoints = [(34.05, -118.25, 15.0), (34.06, -118.25, 15.0)]
        ctrl._wp_index = 0

        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = None

        ctrl._do_search()
        assert ctrl.state == HuntState.SEARCHING

    def test_search_aborts_when_pattern_complete(self):
        ctrl = _make_controller()
        ctrl._set_state(HuntState.SEARCHING)
        ctrl._waypoints = []
        ctrl._wp_index = 0

        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = None

        ctrl._do_search()
        assert ctrl.state == HuntState.ABORTED

    def test_homing_converges_on_strong_signal(self):
        ctrl = _make_controller(rssi_converge_dbm=-40.0)
        ctrl._set_state(HuntState.HOMING)

        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = -35.0  # above converge threshold

        ctrl._do_homing()
        assert ctrl.state == HuntState.CONVERGED

    def test_homing_lost_on_signal_drop(self):
        ctrl = _make_controller(rssi_threshold_dbm=-80.0)
        ctrl._set_state(HuntState.HOMING)

        ctrl._kismet = MagicMock()
        # Return None (no signal) repeatedly to drop the filter average
        ctrl._filter.reset()
        for _ in range(15):
            ctrl._kismet.get_rssi.return_value = None
            ctrl._do_homing()
            if ctrl.state == HuntState.LOST:
                break

        assert ctrl.state == HuntState.LOST

    def test_stop_sets_aborted(self):
        ctrl = _make_controller()
        ctrl._set_state(HuntState.SEARCHING)
        ctrl.stop()
        assert ctrl.state == HuntState.ABORTED


class TestHuntCallbacks:
    def test_state_change_callback(self):
        states = []
        ctrl = _make_controller(on_state_change=lambda s: states.append(s))
        ctrl._set_state(HuntState.SEARCHING)
        ctrl._set_state(HuntState.HOMING)
        assert states == [HuntState.SEARCHING, HuntState.HOMING]

    def test_callback_exception_swallowed(self):
        def bad_cb(s):
            raise RuntimeError("boom")

        ctrl = _make_controller(on_state_change=bad_cb)
        # Should not raise
        ctrl._set_state(HuntState.SEARCHING)
        assert ctrl.state == HuntState.SEARCHING


class TestHuntWaypointNavigation:
    def test_sends_first_waypoint(self):
        mav = _make_mavlink()
        ctrl = _make_controller(mav=mav)
        ctrl._set_state(HuntState.SEARCHING)
        ctrl._waypoints = [(34.06, -118.24, 15.0), (34.07, -118.23, 15.0)]
        ctrl._wp_index = 0

        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = None

        ctrl._do_search()
        mav.command_guided_to.assert_called()

    def test_advances_waypoint_on_arrival(self):
        mav = _make_mavlink(lat=34.06, lon=-118.24)
        ctrl = _make_controller(mav=mav, arrival_tolerance_m=100.0)
        ctrl._set_state(HuntState.SEARCHING)
        ctrl._waypoints = [(34.06, -118.24, 15.0), (34.07, -118.23, 15.0)]
        ctrl._wp_index = 0

        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.return_value = None

        ctrl._do_search()
        assert ctrl._wp_index == 1


class TestHuntKismetClient:
    def test_kismet_client_created(self):
        ctrl = _make_controller(
            kismet_host="http://192.168.1.100:2501",
            kismet_user="admin",
            kismet_pass="secret",
        )
        assert ctrl._kismet._host == "http://192.168.1.100:2501"
        assert ctrl._kismet._session.auth == ("admin", "secret")
