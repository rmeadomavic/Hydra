"""Tests for RF hunt controller state machine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
        gps_required=True,
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


class TestHuntInputValidation:
    def test_search_area_clamped(self):
        ctrl = _make_controller(search_area_m=99999.0)
        assert ctrl._search_area_m == 2000.0

    def test_search_area_min_clamped(self):
        ctrl = _make_controller(search_area_m=1.0)
        assert ctrl._search_area_m == 10.0

    def test_search_spacing_clamped(self):
        ctrl = _make_controller(search_spacing_m=0.5)
        assert ctrl._search_spacing_m == 2.0

    def test_search_alt_clamped(self):
        ctrl = _make_controller(search_alt_m=999.0)
        assert ctrl._search_alt_m == 120.0


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
            raise ValueError("boom")

        ctrl = _make_controller(on_state_change=bad_cb)
        # Should not raise — ValueError is caught
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
        assert ctrl._kismet._user == "admin"
        assert ctrl._kismet._password == "secret"


class TestHuntKismetManagerIntegration:
    def test_accepts_kismet_manager(self):
        from hydra_detect.rf.kismet_manager import KismetManager
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="/tmp/test",
            host="http://localhost:2501",
            log_dir="/tmp/test",
        )
        ctrl = _make_controller(kismet_manager=mgr)
        assert ctrl._kismet_manager is mgr

    def test_works_without_kismet_manager(self):
        ctrl = _make_controller()
        assert ctrl._kismet_manager is None

    def test_poll_rssi_restarts_kismet_on_failure(self):
        from hydra_detect.rf.kismet_manager import KismetManager

        mgr = MagicMock(spec=KismetManager)
        mgr.restart.return_value = True

        ctrl = _make_controller(kismet_manager=mgr)
        ctrl._set_state(HuntState.SEARCHING)

        # First call returns None (connection error), retry after restart returns -60
        ctrl._kismet = MagicMock()
        ctrl._kismet.get_rssi.side_effect = [None, -60.0]
        ctrl._kismet.check_connection.return_value = False

        rssi = ctrl._poll_rssi()
        # Should have attempted restart and returned the retry value
        mgr.restart.assert_called_once()
        ctrl._kismet.reset_auth.assert_called_once_with()
        assert rssi == -60.0


import time


class TestRssiHistory:
    def test_record_rssi_appends(self):
        ctrl = _make_controller()
        ctrl._record_rssi(-72.3)
        history = ctrl.get_rssi_history()
        assert len(history) == 1
        assert history[0]["rssi"] == -72.3
        assert history[0]["lat"] is not None
        assert "t" in history[0]

    def test_record_rssi_with_explicit_gps(self):
        ctrl = _make_controller()
        ctrl._record_rssi(-65.0, lat=35.123, lon=-80.987)
        history = ctrl.get_rssi_history()
        assert history[0]["lat"] == 35.123
        assert history[0]["lon"] == -80.987

    def test_ring_buffer_maxlen(self):
        ctrl = _make_controller()
        for i in range(301):
            ctrl._record_rssi(float(-100 + i))
        history = ctrl.get_rssi_history()
        assert len(history) == 300
        assert history[0]["rssi"] == -99.0

    def test_get_rssi_history_empty(self):
        ctrl = _make_controller()
        assert ctrl.get_rssi_history() == []

    def test_get_rssi_history_is_snapshot(self):
        ctrl = _make_controller()
        ctrl._record_rssi(-70.0)
        h1 = ctrl.get_rssi_history()
        ctrl._record_rssi(-60.0)
        h2 = ctrl.get_rssi_history()
        assert len(h1) == 1
        assert len(h2) == 2

    def test_record_rssi_uses_wall_clock(self):
        ctrl = _make_controller()
        before = time.time()
        ctrl._record_rssi(-70.0)
        after = time.time()
        t = ctrl.get_rssi_history()[0]["t"]
        assert before <= t <= after


class TestScanOnlyMode:
    """Scan-only mode: RSSI polling without GPS/navigation."""

    def test_scan_only_flag_stored(self):
        ctrl = _make_controller(gps_required=False)
        assert ctrl._gps_required is False

    def test_gps_required_defaults_true(self):
        ctrl = _make_controller()
        assert ctrl._gps_required is True

    @patch.object(RFHuntController, "_poll_rssi", return_value=-65.0)
    def test_scan_only_start_succeeds_without_gps(self, mock_poll):
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        ctrl = _make_controller(mav=mav, gps_required=False)
        with patch.object(ctrl._kismet, "check_connection", return_value=True):
            assert ctrl.start() is True
            assert ctrl.state == HuntState.SCANNING

    def test_scan_only_get_status_includes_flag(self):
        ctrl = _make_controller(gps_required=False)
        status = ctrl.get_status()
        assert status["gps_required"] is False

    @patch.object(RFHuntController, "_poll_rssi", return_value=-65.0)
    def test_do_scan_records_rssi(self, mock_poll):
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        ctrl = _make_controller(mav=mav, gps_required=False)
        ctrl._set_state(HuntState.SCANNING)
        ctrl._do_scan()
        history = ctrl.get_rssi_history()
        assert len(history) == 1
        assert history[0]["rssi"] == -65.0
        assert history[0]["lat"] is None

    @patch.object(RFHuntController, "_poll_rssi", return_value=None)
    def test_do_scan_tolerates_no_reading(self, mock_poll):
        mav = _make_mavlink()
        mav.get_lat_lon.return_value = (None, None, None)
        ctrl = _make_controller(mav=mav, gps_required=False)
        ctrl._set_state(HuntState.SCANNING)
        ctrl._do_scan()
        assert len(ctrl.get_rssi_history()) == 0
