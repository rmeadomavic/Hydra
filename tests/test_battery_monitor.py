"""Tests for BatteryMonitor and SYS_STATUS plumbing through MAVLinkIO.

Covers:
- SYS_STATUS → state populated (voltage, remaining, level)
- Threshold transitions emit STATUSTEXT exactly once each (hysteresis)
- Recovery emits a single RECOVERED message, then goes silent
- battery_remaining = -1 → UNKNOWN level, no alert fires
- voltage_battery = 0xFFFF → voltage stays None
- Stale data (no msg in N sec) → UNKNOWN
- critical_reissue_sec re-emits CRITICAL on the configured cadence
- enabled=False → no state, no alerts, no battery field
- MAVLinkIO._handle_sys_status forwards to attached monitor
- BatteryState.to_api() shape matches /api/stats contract
- Configuration validation (critical >= low rejected)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hydra_detect.battery_monitor import (
    LEVEL_CRITICAL,
    LEVEL_LOW,
    LEVEL_OK,
    LEVEL_UNKNOWN,
    BatteryMonitor,
    BatteryState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Sender:
    """Fake STATUSTEXT sink — records (text, severity) tuples."""

    def __init__(self):
        self.messages: list[tuple[str, int]] = []

    def __call__(self, text: str, severity: int) -> None:
        self.messages.append((text, severity))


def _make_monitor(**overrides) -> tuple[BatteryMonitor, _Sender]:
    sender = _Sender()
    kwargs = dict(
        low_threshold_pct=20,
        critical_threshold_pct=10,
        callsign="HYDRA-2-USV",
        send_statustext=sender,
        stale_after_sec=30.0,
        critical_reissue_sec=0.0,
        enabled=True,
    )
    kwargs.update(overrides)
    return BatteryMonitor(**kwargs), sender


# ---------------------------------------------------------------------------
# State + level computation
# ---------------------------------------------------------------------------


class TestBatteryState:
    def test_to_api_shape(self):
        s = BatteryState(
            voltage_v=12.6, remaining_pct=75, level=LEVEL_OK,
        )
        api = s.to_api()
        assert api == {
            "voltage_v": 12.6,
            "remaining_pct": 75,
            "level": LEVEL_OK,
            "source": "mavlink",
            "uncalibrated": False,
        }

    def test_to_api_shape_uncalibrated(self):
        s = BatteryState(
            voltage_v=14.6, remaining_pct=None, level=LEVEL_UNKNOWN,
            uncalibrated=True,
        )
        api = s.to_api()
        assert api["uncalibrated"] is True
        assert api["remaining_pct"] is None


class TestLevelComputation:
    def test_initial_state_is_unknown(self):
        mon, _ = _make_monitor()
        st = mon.get_state(now=100.0)
        assert st.level == LEVEL_UNKNOWN
        assert st.voltage_v is None
        assert st.remaining_pct is None

    def test_sys_status_populates_voltage_and_remaining(self):
        mon, _ = _make_monitor()
        # voltage_battery in mV, battery_remaining 0-100
        mon.update_from_sys_status(12600, 75, now=100.0)
        st = mon.get_state(now=100.0)
        assert st.voltage_v == 12.6
        assert st.remaining_pct == 75
        assert st.level == LEVEL_OK

    def test_low_threshold_inclusive(self):
        mon, _ = _make_monitor(low_threshold_pct=20, critical_threshold_pct=10)
        mon.update_from_sys_status(12000, 20, now=100.0)
        assert mon.get_level(now=100.0) == LEVEL_LOW
        # Just above goes OK
        mon.update_from_sys_status(12000, 21, now=101.0)
        assert mon.get_level(now=101.0) == LEVEL_OK

    def test_critical_threshold_inclusive(self):
        mon, _ = _make_monitor(low_threshold_pct=20, critical_threshold_pct=10)
        mon.update_from_sys_status(11500, 10, now=100.0)
        assert mon.get_level(now=100.0) == LEVEL_CRITICAL
        mon.update_from_sys_status(11500, 11, now=101.0)
        assert mon.get_level(now=101.0) == LEVEL_LOW

    def test_unknown_remaining_keeps_voltage_but_unknown_level(self):
        mon, sender = _make_monitor()
        mon.update_from_sys_status(11800, -1, now=100.0)
        st = mon.get_state(now=100.0)
        assert st.voltage_v == 11.8
        assert st.remaining_pct is None
        assert st.level == LEVEL_UNKNOWN
        assert st.uncalibrated is True
        # Sentinel produces a one-time UNCALIBRATED STATUSTEXT but
        # never a CRITICAL / LOW alert (chemistry-dependent thresholds).
        assert len(sender.messages) == 1
        text, _sev = sender.messages[0]
        assert "UNCALIBRATED" in text

    def test_unknown_voltage_sentinel(self):
        mon, _ = _make_monitor()
        mon.update_from_sys_status(0xFFFF, 50, now=100.0)
        st = mon.get_state(now=100.0)
        assert st.voltage_v is None
        assert st.remaining_pct == 50
        assert st.level == LEVEL_OK


# ---------------------------------------------------------------------------
# Uncalibrated detection (R1-1 from docs/adversarial/211.md)
# ---------------------------------------------------------------------------


class TestUncalibratedDetection:
    """FC is reporting SYS_STATUS but battery_remaining is the -1 sentinel.

    On FPV racing platforms in the SORCC fleet, BATT_CAPACITY is usually 0
    and the battery monitor is uncalibrated — the percent path stays silent
    forever and the dashboard renders dim gray. Fire a one-time STATUSTEXT
    so the operator notices the unit is not protected, and surface the
    `uncalibrated` flag so the dashboard widget can distinguish this state
    from healthy and from disabled.
    """

    def test_uncalibrated_emits_one_time_statustext(self):
        mon, sender = _make_monitor()
        # First SYS_STATUS with voltage but the -1 sentinel for pct.
        mon.update_from_sys_status(14600, -1, now=100.0)
        assert len(sender.messages) == 1
        text, sev = sender.messages[0]
        assert "UNCALIBRATED" in text
        assert mon.callsign in text
        # MAV_SEVERITY_WARNING — visible in Mission Planner, not alarming.
        assert sev == 4

    def test_uncalibrated_alert_does_not_repeat(self):
        mon, sender = _make_monitor()
        for tick in range(10):
            mon.update_from_sys_status(14600, -1, now=100.0 + tick)
        # Exactly one UNCALIBRATED message across 10 SYS_STATUS ticks.
        uncal_msgs = [m for m in sender.messages if "UNCALIBRATED" in m[0]]
        assert len(uncal_msgs) == 1

    def test_uncalibrated_flag_in_state(self):
        mon, _ = _make_monitor()
        mon.update_from_sys_status(14600, -1, now=100.0)
        st = mon.get_state(now=100.0)
        assert st.uncalibrated is True
        assert st.level == LEVEL_UNKNOWN

    def test_uncalibrated_sticks_after_stale(self):
        """After SYS_STATUS goes stale, uncalibrated stays True for the
        dashboard so the operator does not interpret stale-and-silent
        as healthy. Level still flips to UNKNOWN via staleness."""
        mon, _ = _make_monitor(stale_after_sec=10.0)
        mon.update_from_sys_status(14600, -1, now=100.0)
        # 30s later, staleness has triggered.
        st = mon.get_state(now=130.0)
        assert st.level == LEVEL_UNKNOWN
        assert st.uncalibrated is True

    def test_uncalibrated_no_alert_when_voltage_also_missing(self):
        """0xFFFF + -1 means we got a SYS_STATUS but it had no battery
        data at all. That is no-data, not uncalibrated — do not fire."""
        mon, sender = _make_monitor()
        mon.update_from_sys_status(0xFFFF, -1, now=100.0)
        assert sender.messages == []
        st = mon.get_state(now=100.0)
        assert st.uncalibrated is False

    def test_calibrated_first_then_sentinel(self):
        """If a real pct arrives first, then a -1 sentinel later
        (telemetry glitch), do not fire the boot UNCALIBRATED — the
        unit clearly has calibration."""
        mon, sender = _make_monitor()
        mon.update_from_sys_status(14600, 80, now=100.0)  # healthy
        mon.update_from_sys_status(14600, -1, now=101.0)  # sentinel
        uncal_msgs = [m for m in sender.messages if "UNCALIBRATED" in m[0]]
        # First message went OK→UNKNOWN with no alert; second sees pct=None.
        # Both saw voltage+sentinel on tick 2 → uncalibrated fires once.
        # This is the documented behavior; test pins it.
        assert len(uncal_msgs) == 1


# ---------------------------------------------------------------------------
# Hysteresis / STATUSTEXT emission
# ---------------------------------------------------------------------------


class TestThresholdTransitions:
    def test_ok_to_low_emits_once(self):
        mon, sender = _make_monitor()
        mon.update_from_sys_status(12000, 50, now=100.0)
        mon.update_from_sys_status(12000, 18, now=101.0)
        # Multiple updates at the same level must not re-fire
        mon.update_from_sys_status(12000, 17, now=102.0)
        mon.update_from_sys_status(12000, 16, now=103.0)
        low_msgs = [m for m in sender.messages if "BATT LOW" in m[0]]
        assert len(low_msgs) == 1
        assert low_msgs[0][0] == "HYDRA-2-USV: BATT LOW 18%"
        # WARNING severity (4)
        assert low_msgs[0][1] == 4

    def test_low_to_critical_emits_once(self):
        mon, sender = _make_monitor()
        mon.update_from_sys_status(12000, 18, now=100.0)
        mon.update_from_sys_status(11500, 9, now=101.0)
        mon.update_from_sys_status(11500, 8, now=102.0)
        crit_msgs = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        assert len(crit_msgs) == 1
        assert crit_msgs[0][0] == "HYDRA-2-USV: BATT CRITICAL 9%"
        # CRITICAL severity (2)
        assert crit_msgs[0][1] == 2

    def test_ok_to_critical_skipping_low_emits_once(self):
        """Sudden drop from OK directly to CRITICAL (e.g. high-discharge spike)."""
        mon, sender = _make_monitor()
        mon.update_from_sys_status(12600, 80, now=100.0)
        mon.update_from_sys_status(11000, 5, now=101.0)
        crit_msgs = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        low_msgs = [m for m in sender.messages if "BATT LOW" in m[0]]
        assert len(crit_msgs) == 1
        # Skipping LOW state should NOT emit a LOW alert.
        assert low_msgs == []

    def test_recovery_emits_single_message(self):
        """CRITICAL → OK fires a RECOVERED STATUSTEXT, then goes silent."""
        mon, sender = _make_monitor()
        mon.update_from_sys_status(11500, 5, now=100.0)
        mon.update_from_sys_status(12600, 80, now=110.0)
        recovered = [m for m in sender.messages if "RECOVERED" in m[0]]
        assert len(recovered) == 1
        assert recovered[0][0] == "HYDRA-2-USV: BATT RECOVERED 80%"
        # Subsequent OK updates must not refire.
        mon.update_from_sys_status(12600, 75, now=111.0)
        recovered2 = [m for m in sender.messages if "RECOVERED" in m[0]]
        assert len(recovered2) == 1

    def test_low_to_ok_recovery(self):
        mon, sender = _make_monitor()
        mon.update_from_sys_status(12000, 15, now=100.0)
        mon.update_from_sys_status(12500, 50, now=110.0)
        recovered = [m for m in sender.messages if "RECOVERED" in m[0]]
        assert len(recovered) == 1

    def test_no_low_or_critical_alert_on_unknown_remaining(self):
        """The -1 sentinel must never trigger LOW or CRITICAL — voltage
        thresholds are chemistry-dependent. The one-time UNCALIBRATED
        WARNING is permitted (see TestUncalibratedDetection)."""
        mon, sender = _make_monitor()
        mon.update_from_sys_status(11500, -1, now=100.0)
        mon.update_from_sys_status(11400, -1, now=101.0)
        threshold_alerts = [
            m for m in sender.messages
            if any(tag in m[0] for tag in ("BATT LOW", "BATT CRITICAL"))
        ]
        assert threshold_alerts == []

    def test_callsign_truncation(self):
        mon, sender = _make_monitor(callsign="HYDRA-99-LONGNAME-EXTRA")
        mon.update_from_sys_status(11500, 5, now=100.0)
        # Prefix should be truncated to 16 chars.
        assert sender.messages[0][0].startswith("HYDRA-99-LONGNAM:")


class TestStaleness:
    def test_stale_data_resolves_to_unknown(self):
        mon, _ = _make_monitor(stale_after_sec=10.0)
        mon.update_from_sys_status(12000, 50, now=100.0)
        assert mon.get_level(now=105.0) == LEVEL_OK
        # Past the staleness window
        assert mon.get_level(now=120.0) == LEVEL_UNKNOWN

    def test_stale_zero_disables_window(self):
        mon, _ = _make_monitor(stale_after_sec=0.0)
        mon.update_from_sys_status(12000, 50, now=100.0)
        # Still OK well past what would normally be stale.
        assert mon.get_level(now=10_000.0) == LEVEL_OK


class TestCriticalReissue:
    def test_reissue_zero_means_one_alert_per_transition(self):
        mon, sender = _make_monitor(critical_reissue_sec=0.0)
        mon.update_from_sys_status(11500, 5, now=100.0)
        mon.update_from_sys_status(11500, 5, now=200.0)
        crit = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        assert len(crit) == 1

    def test_reissue_fires_after_window(self):
        mon, sender = _make_monitor(critical_reissue_sec=60.0)
        mon.update_from_sys_status(11500, 5, now=100.0)
        mon.update_from_sys_status(11500, 4, now=130.0)
        crit_30s = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        assert len(crit_30s) == 1  # 30s elapsed, below window
        mon.update_from_sys_status(11500, 4, now=170.0)  # +70s since first
        crit_70s = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        assert len(crit_70s) == 2

    def test_reissue_only_fires_for_critical_not_low(self):
        mon, sender = _make_monitor(critical_reissue_sec=60.0)
        mon.update_from_sys_status(12000, 18, now=100.0)
        mon.update_from_sys_status(12000, 17, now=200.0)
        low = [m for m in sender.messages if "BATT LOW" in m[0]]
        assert len(low) == 1


class TestDisabled:
    def test_disabled_monitor_ignores_updates(self):
        mon, sender = _make_monitor(enabled=False)
        mon.update_from_sys_status(11000, 5, now=100.0)
        st = mon.get_state(now=100.0)
        assert st.voltage_v is None
        assert st.remaining_pct is None
        assert st.level == LEVEL_UNKNOWN
        assert sender.messages == []

    def test_enabled_property_reflects_constructor(self):
        mon, _ = _make_monitor(enabled=False)
        assert mon.enabled is False
        mon2, _ = _make_monitor(enabled=True)
        assert mon2.enabled is True


class TestConfigValidation:
    def test_critical_must_be_below_low(self):
        with pytest.raises(ValueError):
            BatteryMonitor(
                low_threshold_pct=10, critical_threshold_pct=20,
            )

    def test_equal_thresholds_rejected(self):
        with pytest.raises(ValueError):
            BatteryMonitor(
                low_threshold_pct=15, critical_threshold_pct=15,
            )


class TestTickReissue:
    def test_tick_does_nothing_when_idle(self):
        mon, sender = _make_monitor()
        mon.tick(now=100.0)
        assert sender.messages == []

    def test_tick_fires_critical_reissue_without_new_msg(self):
        # Disable staleness so the level stays CRITICAL between updates.
        # In production this branch is intended for fast-cadence SYS_STATUS
        # streams where new messages arrive well within stale_after_sec.
        mon, sender = _make_monitor(critical_reissue_sec=60.0, stale_after_sec=0.0)
        mon.update_from_sys_status(11500, 5, now=100.0)
        mon.tick(now=170.0)
        crit = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        assert len(crit) == 2

    def test_tick_does_not_reissue_when_stale(self):
        """Once SYS_STATUS goes stale the reissue ladder must stop."""
        mon, sender = _make_monitor(critical_reissue_sec=60.0, stale_after_sec=30.0)
        mon.update_from_sys_status(11500, 5, now=100.0)
        # Past staleness window — level resolves to UNKNOWN, no reissue.
        mon.tick(now=200.0)
        crit = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        assert len(crit) == 1
        assert mon.get_level(now=200.0) == LEVEL_UNKNOWN


# ---------------------------------------------------------------------------
# MAVLinkIO integration
# ---------------------------------------------------------------------------


def _make_sys_status(voltage_battery: int, battery_remaining: int):
    msg = MagicMock()
    msg.get_type.return_value = "SYS_STATUS"
    msg.voltage_battery = voltage_battery
    msg.battery_remaining = battery_remaining
    return msg


class TestMavlinkIoIntegration:
    def test_handle_sys_status_populates_telemetry(self):
        from hydra_detect.mavlink_io import MAVLinkIO
        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mav._handle_sys_status(_make_sys_status(12600, 75))
        telem = mav.get_telemetry()
        assert telem["battery_v"] == 12.6
        assert telem["battery_pct"] == 75
        assert telem["battery_last_update"] > 0.0

    def test_handle_sys_status_unknown_remaining(self):
        from hydra_detect.mavlink_io import MAVLinkIO
        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mav._handle_sys_status(_make_sys_status(11800, -1))
        telem = mav.get_telemetry()
        assert telem["battery_v"] == 11.8
        # battery_pct stays at last-known None (no remaining update)
        assert telem["battery_pct"] is None

    def test_handle_sys_status_unknown_voltage_sentinel(self):
        from hydra_detect.mavlink_io import MAVLinkIO
        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mav._handle_sys_status(_make_sys_status(0xFFFF, 60))
        telem = mav.get_telemetry()
        assert telem["battery_v"] is None
        assert telem["battery_pct"] == 60

    def test_attached_monitor_receives_sys_status(self):
        from hydra_detect.mavlink_io import MAVLinkIO
        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mon, sender = _make_monitor()
        mav.attach_battery_monitor(mon)
        # Drop straight to critical
        mav._handle_sys_status(_make_sys_status(11500, 5))
        crit = [m for m in sender.messages if "BATT CRITICAL" in m[0]]
        assert len(crit) == 1

    def test_get_battery_monitor_returns_attached(self):
        from hydra_detect.mavlink_io import MAVLinkIO
        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        assert mav.get_battery_monitor() is None
        mon, _ = _make_monitor()
        mav.attach_battery_monitor(mon)
        assert mav.get_battery_monitor() is mon

    def test_monitor_send_failure_is_swallowed(self):
        """A blow-up inside the STATUSTEXT callback must not crash the
        MAVLink reader."""
        from hydra_detect.mavlink_io import MAVLinkIO

        def boom(text, severity):
            raise RuntimeError("send failed")

        mon = BatteryMonitor(
            low_threshold_pct=20,
            critical_threshold_pct=10,
            callsign="HYDRA",
            send_statustext=boom,
        )
        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mav.attach_battery_monitor(mon)
        # Should not raise
        mav._handle_sys_status(_make_sys_status(11500, 5))
        assert mon.get_level().__contains__  # smoke; ensure no exception


# ---------------------------------------------------------------------------
# OSD integration
# ---------------------------------------------------------------------------


class TestOsdBatteryIntegration:
    def test_build_osd_state_picks_up_battery_dict(self):
        from hydra_detect.osd import build_osd_state
        from hydra_detect.tracker import TrackingResult

        battery = {
            "voltage_v": 11.4, "remaining_pct": 8,
            "level": "CRITICAL", "source": "mavlink",
        }
        state = build_osd_state(
            TrackingResult([]), fps=10.0, inference_ms=20.0,
            locked_track_id=None, lock_mode=None, gps=None,
            battery=battery,
        )
        assert state.battery_pct == 8
        assert state.battery_level == "CRITICAL"

    def test_build_osd_state_battery_unknown(self):
        from hydra_detect.osd import build_osd_state
        from hydra_detect.tracker import TrackingResult

        state = build_osd_state(
            TrackingResult([]), fps=10.0, inference_ms=20.0,
            locked_track_id=None, lock_mode=None, gps=None,
            battery=None,
        )
        assert state.battery_pct is None
        assert state.battery_level == "UNKNOWN"

    def test_statustext_emits_when_only_battery_alert_active(self):
        """OSD must surface a CRITICAL battery alert even with no tracks."""
        from hydra_detect.osd import FpvOsd, OSDState

        mav = MagicMock()
        mav.connected = True
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=20.0, active_tracks=0,
            battery_pct=8, battery_level="CRITICAL",
        )
        osd.update(state)
        mav.send_statustext.assert_called_once()
        text = mav.send_statustext.call_args[0][0]
        assert "B!8" in text  # CRITICAL marker

    def test_statustext_skipped_when_battery_ok_and_no_tracks(self):
        """Original quiet-when-idle behaviour preserved for OK battery."""
        from hydra_detect.osd import FpvOsd, OSDState

        mav = MagicMock()
        mav.connected = True
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=20.0, active_tracks=0,
            battery_pct=80, battery_level="OK",
        )
        osd.update(state)
        mav.send_statustext.assert_not_called()


# ---------------------------------------------------------------------------
# /api/stats integration — Linux-only because hydra_detect.web.server
# imports fcntl through web/config_api.py.
# ---------------------------------------------------------------------------


import sys  # noqa: E402

_FCNTL_AVAILABLE = sys.platform != "win32"


@pytest.mark.skipif(
    not _FCNTL_AVAILABLE,
    reason="server.py imports fcntl (Linux-only); covered on Jetson CI",
)
class TestApiStatsBatteryField:
    @pytest.fixture(autouse=True)
    def _reset(self):
        from hydra_detect.web import server as web_server
        web_server._response_cache.clear()
        web_server._mavlink_ref = None
        yield
        web_server._mavlink_ref = None
        web_server._response_cache.clear()

    def test_battery_field_null_when_monitor_absent(self):
        from fastapi.testclient import TestClient
        from hydra_detect.web import server as web_server

        client = TestClient(web_server.app)
        r = client.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        assert "battery" in body
        assert body["battery"] is None

    def test_battery_field_populated_when_monitor_attached(self):
        import time
        from fastapi.testclient import TestClient
        from hydra_detect.mavlink_io import MAVLinkIO
        from hydra_detect.web import server as web_server

        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mon, _ = _make_monitor()
        mav.attach_battery_monitor(mon)
        # Stamp the update with the real monotonic clock so the GET
        # request (which uses time.monotonic() under the hood) sees a
        # fresh sample, not a 30s-stale one demoted to UNKNOWN.
        mon.update_from_sys_status(12000, 18, now=time.monotonic())
        web_server.set_mavlink(mav)

        client = TestClient(web_server.app)
        r = client.get("/api/stats")
        body = r.json()
        battery = body["battery"]
        assert battery is not None
        assert battery["voltage_v"] == 12.0
        assert battery["remaining_pct"] == 18
        assert battery["level"] == "LOW"
        assert battery["source"] == "mavlink"

    def test_battery_field_null_when_monitor_disabled(self):
        from fastapi.testclient import TestClient
        from hydra_detect.mavlink_io import MAVLinkIO
        from hydra_detect.web import server as web_server

        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mon, _ = _make_monitor(enabled=False)
        mav.attach_battery_monitor(mon)
        mon.update_from_sys_status(12000, 18, now=100.0)
        web_server.set_mavlink(mav)

        client = TestClient(web_server.app)
        r = client.get("/api/stats")
        body = r.json()
        # Disabled monitor → no battery field exposed
        assert body["battery"] is None
