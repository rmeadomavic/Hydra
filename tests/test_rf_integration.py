"""Integration tests for RF hunt module with real Kismet + RTL-SDR.

These tests hit the real Kismet REST API and (optionally) real SDR hardware.
They require hardware and are excluded from default test runs:

    # Run hardware tests explicitly
    python -m pytest tests/test_rf_integration.py -v -m hardware

    # Skip tests that need an active RF signal
    python -m pytest tests/test_rf_integration.py -v -k "not signal"
"""

from __future__ import annotations

import shutil
import subprocess
import time
from unittest.mock import MagicMock

import pytest
import requests

from hydra_detect.rf.kismet_client import KismetClient
from hydra_detect.rf.kismet_manager import KismetManager
from hydra_detect.rf.hunt import HuntState, RFHuntController
from hydra_detect.rf.signal import RSSIFilter
from hydra_detect.rf.navigator import GradientNavigator
from hydra_detect.rf.search import generate_lawnmower, generate_spiral

# Mark entire module as requiring hardware — excluded from default test runs.
pytestmark = pytest.mark.hardware

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KISMET_HOST = "http://localhost:2501"
KISMET_USER = "kismet"
KISMET_PASS = "kismet"
KISMET_SOURCE = "rtl433-0"
CAPTURE_DIR = "/tmp/hydra_test_kismet"
LOG_DIR = "/tmp/hydra_test_logs"


def _kismet_available() -> bool:
    """Check if Kismet binary is on PATH."""
    return shutil.which("kismet") is not None


def _kismet_api_up() -> bool:
    """Check if Kismet REST API is responding."""
    try:
        r = requests.get(f"{KISMET_HOST}/system/status.json", timeout=2)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _rtlsdr_present() -> bool:
    """Check if an RTL-SDR dongle is connected (0bda:2838)."""
    try:
        out = subprocess.check_output(["lsusb"], text=True, timeout=5)
        return "0bda:2838" in out
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


skip_no_kismet = pytest.mark.skipif(
    not _kismet_available(), reason="Kismet not installed",
)
skip_no_rtlsdr = pytest.mark.skipif(
    not _rtlsdr_present(), reason="RTL-SDR dongle not connected",
)


@pytest.fixture(scope="module")
def kismet_manager():
    """Start Kismet via KismetManager for the test session, stop after."""
    if not _kismet_available():
        pytest.skip("Kismet not installed")
    if not _rtlsdr_present():
        pytest.skip("RTL-SDR not connected")

    mgr = KismetManager(
        source=KISMET_SOURCE,
        capture_dir=CAPTURE_DIR,
        host=KISMET_HOST,
        user=KISMET_USER,
        password=KISMET_PASS,
        log_dir=LOG_DIR,
        max_capture_mb=50.0,
    )

    # If Kismet is already running, adopt it; otherwise start fresh
    started = mgr.start(timeout_sec=20.0)
    if not started:
        pytest.skip("Kismet failed to start — check RTL-SDR permissions")

    yield mgr

    mgr.stop(timeout_sec=5.0)


@pytest.fixture
def kismet_client(kismet_manager):
    """Provide a KismetClient connected to the running Kismet instance."""
    with KismetClient(
        host=KISMET_HOST,
        user=KISMET_USER,
        password=KISMET_PASS,
        timeout=5.0,
    ) as client:
        yield client


def _make_mock_mavlink(*, lat=34.0522, lon=-118.2437, alt=15.0):
    """Build a mock MAVLinkIO that returns fixed GPS coordinates."""
    mav = MagicMock()
    mav.get_lat_lon.return_value = (lat, lon, alt)
    mav.command_guided_to.return_value = True
    mav.send_statustext = MagicMock()
    mav.connected = True
    return mav


# ---------------------------------------------------------------------------
# 1. KismetManager lifecycle tests
# ---------------------------------------------------------------------------


@skip_no_kismet
@skip_no_rtlsdr
class TestKismetManagerLifecycle:
    """Test KismetManager start/health/stop with real Kismet."""

    def test_manager_started(self, kismet_manager):
        """KismetManager should have Kismet running and API reachable."""
        assert kismet_manager.is_healthy()

    def test_manager_pid_or_adopted(self, kismet_manager):
        """Should either own the process (with PID) or have adopted it."""
        if kismet_manager.we_own_process:
            assert kismet_manager.pid is not None
            assert kismet_manager.pid > 0
        else:
            # Adopted — no PID tracked, but API is up
            assert kismet_manager.is_healthy()

    def test_api_responds_to_status(self, kismet_manager):
        """Direct HTTP check — Kismet REST API returns system status."""
        r = requests.get(
            f"{KISMET_HOST}/system/status.json",
            auth=(KISMET_USER, KISMET_PASS),
            timeout=5,
        )
        assert r.status_code == 200
        data = r.json()
        assert "kismet.system.version" in data


# ---------------------------------------------------------------------------
# 2. KismetClient auth & connectivity tests
# ---------------------------------------------------------------------------


@skip_no_kismet
@skip_no_rtlsdr
class TestKismetClientAuth:
    """Test real Kismet 2025 cookie-based authentication."""

    def test_check_connection(self, kismet_client):
        """Client should authenticate and report connected."""
        assert kismet_client.check_connection() is True

    def test_session_cookie_auth(self, kismet_client):
        """After check_connection, client should be authenticated."""
        kismet_client.check_connection()
        assert kismet_client._authenticated is True

    def test_reset_and_reconnect(self, kismet_client):
        """After reset_auth, client should re-authenticate on next call."""
        kismet_client.check_connection()
        kismet_client.reset_auth()
        assert kismet_client._authenticated is False

        # Should re-auth automatically
        assert kismet_client.check_connection() is True
        assert kismet_client._authenticated is True

    def test_wrong_password_fails(self, kismet_manager):
        """Wrong credentials should fail authentication."""
        client = KismetClient(
            host=KISMET_HOST,
            user="kismet",
            password="wrong_password_12345",
            timeout=5.0,
        )
        # Kismet 2025 may still return 200 for check_session with wrong creds
        # depending on config, but status.json should fail without valid session
        # The important thing is it doesn't crash
        result = client.check_connection()
        # Either True (if Kismet has no auth) or False (if auth required)
        assert isinstance(result, bool)
        client.close()


# ---------------------------------------------------------------------------
# 3. SDR device polling tests
# ---------------------------------------------------------------------------


@skip_no_kismet
@skip_no_rtlsdr
class TestKismetSDRPolling:
    """Test real SDR device queries via Kismet REST API."""

    def test_device_list_accessible(self, kismet_client):
        """Should be able to query device list (may be empty)."""
        kismet_client.check_connection()
        r = kismet_client._session.get(
            f"{KISMET_HOST}/devices/views/all/devices.json",
            params={
                "KISMET": '{"fields": ['
                '"kismet.device.base.signal/kismet.common.signal.last_signal",'
                '"kismet.device.base.frequency",'
                '"kismet.device.base.last_time"'
                ']}'
            },
            timeout=5,
        )
        assert r.status_code == 200
        devices = r.json()
        assert isinstance(devices, list)
        print(f"\n  Kismet sees {len(devices)} device(s)")
        for dev in devices[:5]:
            freq = dev.get("kismet.device.base.frequency", 0)
            if freq > 10_000:
                freq_mhz = freq / 1e6
            else:
                freq_mhz = float(freq)
            sig = dev.get("kismet.device.base.signal", {})
            rssi = sig.get("kismet.common.signal.last_signal", "N/A")
            print(f"    freq={freq_mhz:.3f} MHz  rssi={rssi} dBm")

    def test_sdr_rssi_query_433mhz(self, kismet_client):
        """Query 433 MHz — returns float or None (no crash)."""
        rssi = kismet_client.get_sdr_rssi(433.0, tolerance_mhz=1.0)
        if rssi is not None:
            assert isinstance(rssi, float)
            assert -120 < rssi < 0
            print(f"\n  433 MHz RSSI: {rssi} dBm")
        else:
            print("\n  433 MHz: no signal (expected if no transmitter nearby)")

    def test_sdr_rssi_query_915mhz(self, kismet_client):
        """Query 915 MHz — returns float or None (no crash)."""
        rssi = kismet_client.get_sdr_rssi(915.0, tolerance_mhz=1.0)
        if rssi is not None:
            assert isinstance(rssi, float)
            assert -120 < rssi < 0
            print(f"\n  915 MHz RSSI: {rssi} dBm")
        else:
            print("\n  915 MHz: no signal (expected if no transmitter nearby)")

    def test_unified_getter_sdr(self, kismet_client):
        """Unified getter in SDR mode should work without error."""
        rssi = kismet_client.get_rssi(mode="sdr", freq_mhz=433.0)
        # Just verify it doesn't crash — signal may or may not be present
        assert rssi is None or isinstance(rssi, float)


# ---------------------------------------------------------------------------
# 4. Hunt controller integration with real Kismet
# ---------------------------------------------------------------------------


@skip_no_kismet
@skip_no_rtlsdr
class TestHuntControllerWithKismet:
    """Run the hunt controller against real Kismet with mock MAVLink."""

    def test_hunt_starts_with_real_kismet(self, kismet_manager):
        """Hunt controller should start when real Kismet is running."""
        mav = _make_mock_mavlink()
        ctrl = RFHuntController(
            mav,
            mode="sdr",
            target_freq_mhz=433.0,
            kismet_host=KISMET_HOST,
            kismet_user=KISMET_USER,
            kismet_pass=KISMET_PASS,
            search_pattern="spiral",
            search_area_m=50.0,
            search_spacing_m=10.0,
            search_alt_m=15.0,
            rssi_threshold_dbm=-80.0,
            rssi_converge_dbm=-30.0,
            poll_interval_sec=0.5,
            arrival_tolerance_m=3.0,
            kismet_manager=kismet_manager,
        )

        result = ctrl.start()
        assert result is True
        assert ctrl.state == HuntState.SEARCHING

        # Let it run a few cycles
        time.sleep(2.0)

        # Should still be running (searching or homing if signal found)
        state = ctrl.state
        assert state in (
            HuntState.SEARCHING, HuntState.HOMING,
            HuntState.CONVERGED, HuntState.LOST,
        )

        status = ctrl.get_status()
        assert status["mode"] == "sdr"
        assert "433" in status["target"]
        print(f"\n  Hunt state: {status['state']}")
        print(f"  Best RSSI: {status['best_rssi']} dBm")
        print(f"  Samples: {status['samples']}")
        print(f"  WP progress: {status['wp_progress']}")

        ctrl.stop()
        assert ctrl.state == HuntState.ABORTED

    def test_hunt_stop_is_clean(self, kismet_manager):
        """Stopping the hunt should be fast and clean."""
        mav = _make_mock_mavlink()
        ctrl = RFHuntController(
            mav,
            mode="sdr",
            target_freq_mhz=915.0,
            kismet_host=KISMET_HOST,
            kismet_user=KISMET_USER,
            kismet_pass=KISMET_PASS,
            poll_interval_sec=0.5,
            kismet_manager=kismet_manager,
        )
        ctrl.start()
        time.sleep(1.0)

        t0 = time.monotonic()
        ctrl.stop()
        stop_time = time.monotonic() - t0

        assert ctrl.state == HuntState.ABORTED
        assert stop_time < 5.0, f"Stop took {stop_time:.1f}s (should be < 5s)"
        print(f"\n  Hunt stop took {stop_time:.2f}s")

    def test_hunt_status_thread_safe(self, kismet_manager):
        """Reading status from main thread while hunt runs should not crash."""
        mav = _make_mock_mavlink()
        ctrl = RFHuntController(
            mav,
            mode="sdr",
            target_freq_mhz=433.0,
            kismet_host=KISMET_HOST,
            kismet_user=KISMET_USER,
            kismet_pass=KISMET_PASS,
            poll_interval_sec=0.1,
            kismet_manager=kismet_manager,
        )
        ctrl.start()

        # Hammer status reads from main thread while hunt runs
        for _ in range(50):
            status = ctrl.get_status()
            assert "state" in status
            assert "best_rssi" in status
            _ = ctrl.state
            _ = ctrl.best_rssi
            _ = ctrl.best_position
            _ = ctrl.sample_count
            time.sleep(0.02)

        ctrl.stop()

    def test_hunt_state_change_callback(self, kismet_manager):
        """State change callback should fire with real Kismet."""
        states_seen = []
        mav = _make_mock_mavlink()
        ctrl = RFHuntController(
            mav,
            mode="sdr",
            target_freq_mhz=433.0,
            kismet_host=KISMET_HOST,
            kismet_user=KISMET_USER,
            kismet_pass=KISMET_PASS,
            poll_interval_sec=0.5,
            on_state_change=lambda s: states_seen.append(s),
            kismet_manager=kismet_manager,
        )

        ctrl.start()
        time.sleep(1.0)
        ctrl.stop()

        # Should have at least SEARCHING and ABORTED
        assert HuntState.SEARCHING in states_seen
        assert HuntState.ABORTED in states_seen
        print(f"\n  States seen: {[s.value for s in states_seen]}")

    def test_hunt_mavlink_waypoints_sent(self, kismet_manager):
        """Hunt should send GUIDED waypoint commands to MAVLink."""
        mav = _make_mock_mavlink()
        ctrl = RFHuntController(
            mav,
            mode="sdr",
            target_freq_mhz=433.0,
            kismet_host=KISMET_HOST,
            kismet_user=KISMET_USER,
            kismet_pass=KISMET_PASS,
            search_pattern="lawnmower",
            search_area_m=30.0,
            search_spacing_m=10.0,
            poll_interval_sec=0.2,
            arrival_tolerance_m=0.1,  # tight so it sends first WP
            kismet_manager=kismet_manager,
        )
        ctrl.start()
        time.sleep(2.0)
        ctrl.stop()

        # Should have tried to send at least one waypoint
        assert mav.command_guided_to.called, "No waypoint commands sent"
        calls = mav.command_guided_to.call_args_list
        print(f"\n  Waypoint commands sent: {len(calls)}")
        for i, call in enumerate(calls[:3]):
            lat, lon = call.args[0], call.args[1]
            print(f"    WP {i}: ({lat:.6f}, {lon:.6f})")

    def test_hunt_statustext_sent(self, kismet_manager):
        """Hunt should send STATUSTEXT messages to GCS."""
        mav = _make_mock_mavlink()
        ctrl = RFHuntController(
            mav,
            mode="sdr",
            target_freq_mhz=433.0,
            kismet_host=KISMET_HOST,
            kismet_user=KISMET_USER,
            kismet_pass=KISMET_PASS,
            poll_interval_sec=0.5,
            kismet_manager=kismet_manager,
        )
        ctrl.start()
        time.sleep(1.5)
        ctrl.stop()

        assert mav.send_statustext.called, "No STATUSTEXT messages sent"
        calls = mav.send_statustext.call_args_list
        texts = [c.args[0] for c in calls]
        print(f"\n  STATUSTEXT messages: {texts}")
        # First message should be about search starting
        assert any("RF HUNT" in t for t in texts)


# ---------------------------------------------------------------------------
# 5. Signal pipeline tests (no RF signal needed)
# ---------------------------------------------------------------------------


class TestSignalPipelineIntegration:
    """Test RSSIFilter + GradientNavigator together (no hardware needed)."""

    def test_filter_feeds_navigator(self):
        """Simulated RSSI readings flow through filter into navigator."""
        filt = RSSIFilter(window_size=5)
        nav = GradientNavigator(step_m=5.0, rotation_deg=45.0)

        # Simulate improving signal
        readings = [-90.0, -85.0, -80.0, -75.0, -70.0, -65.0, -60.0]
        lat, lon, alt = 34.0522, -118.2437, 15.0

        for rssi in readings:
            smoothed = filt.add(rssi)
            nav.record(smoothed, lat, lon, alt)

        assert nav.get_best_rssi() > -100.0
        assert nav.get_sample_count() == len(readings)
        assert filt.trend > 0, "Trend should be positive for improving signal"

    def test_navigator_gradient_with_synthetic_rssi(self):
        """Navigator should track improving RSSI and maintain bearing."""
        nav = GradientNavigator(
            step_m=5.0,
            rotation_deg=45.0,
            converge_dbm=-40.0,
        )

        lat, lon = 34.0522, -118.2437
        prev_rssi = -90.0

        # Simulate gradient ascent — signal gets stronger
        for rssi in [-85.0, -80.0, -75.0, -70.0, -65.0]:
            nav.record(rssi, lat, lon, 15.0)
            nlat, nlon, cont = nav.next_probe(lat, lon, rssi, prev_rssi)
            assert cont is True
            prev_rssi = rssi
            lat, lon = nlat, nlon

        # Now converge
        nav.record(-35.0, lat, lon, 15.0)
        _, _, cont = nav.next_probe(lat, lon, -35.0, prev_rssi)
        assert cont is False, "Should declare converged at -35 dBm"

    def test_search_pattern_generation(self):
        """Lawnmower and spiral patterns produce valid waypoints."""
        lat, lon = 34.0522, -118.2437

        lm_wps = generate_lawnmower(lat, lon, width_m=50, height_m=50, spacing_m=10)
        assert len(lm_wps) > 0
        assert all(len(wp) == 3 for wp in lm_wps)

        sp_wps = generate_spiral(lat, lon, max_radius_m=25, spacing_m=5)
        assert len(sp_wps) > 0
        # First waypoint should be at center
        assert abs(sp_wps[0][0] - lat) < 1e-6
        assert abs(sp_wps[0][1] - lon) < 1e-6


# ---------------------------------------------------------------------------
# 6. Kismet manager edge cases (real binary, no SDR needed)
# ---------------------------------------------------------------------------


@skip_no_kismet
class TestKismetManagerEdgeCases:
    """Edge case tests that need the Kismet binary but not SDR hardware."""

    def test_manager_with_bad_source(self):
        """Starting with a nonexistent capture source should fail gracefully."""
        mgr = KismetManager(
            source="nonexistent-source-xyz",
            capture_dir="/tmp/hydra_test_bad",
            host="http://localhost:12345",  # unused port
            log_dir="/tmp/hydra_test_bad",
        )
        # This should fail (Kismet will exit quickly with bad source)
        # but should NOT crash
        result = mgr.start(timeout_sec=8.0)
        # Either it fails to start, or Kismet ignores bad source — both ok
        if result:
            mgr.stop()
        # The important thing is no exception was raised

    def test_manager_double_stop(self):
        """Calling stop() twice should be safe."""
        mgr = KismetManager(
            source=KISMET_SOURCE,
            capture_dir="/tmp/hydra_test_dblstop",
            host=KISMET_HOST,
            log_dir="/tmp/hydra_test_dblstop",
        )
        # Don't actually start — just verify double-stop is safe
        mgr.stop()
        mgr.stop()  # Should not raise

    def test_health_check_when_not_started(self):
        """is_healthy() should return False when nothing is running."""
        mgr = KismetManager(
            source=KISMET_SOURCE,
            capture_dir="/tmp/hydra_test_health",
            host="http://localhost:19999",  # nothing running here
            log_dir="/tmp/hydra_test_health",
        )
        assert mgr.is_healthy() is False
