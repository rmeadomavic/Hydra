"""Tests for the Kismet → AmbientScanBuffer poller."""

from __future__ import annotations

import json
import logging
import threading
import time
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from hydra_detect.rf import AmbientScanBuffer, KismetPoller
from hydra_detect.rf.kismet_poller import (
    _modulation_from,
    _parse_devices,
)


def _fake_response(payload: object) -> MagicMock:
    """Build a mock urlopen context manager yielding JSON bytes."""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


# ---------------------------------------------------------------------------
# Pure-parse helpers
# ---------------------------------------------------------------------------

class TestModulation:
    def test_wifi_2g(self):
        assert _modulation_from("IEEE802.11", 2412.0) == "wifi_2g"

    def test_wifi_5g(self):
        assert _modulation_from("IEEE802.11", 5745.0) == "wifi_5g"

    def test_ism_915(self):
        assert _modulation_from("RTL433", 915.0) == "ism_915"

    def test_fpv_raceband(self):
        assert _modulation_from("", 5800.0) == "fpv_raceband"

    def test_unknown(self):
        assert _modulation_from("", 1234.0) == "unknown"


class TestParseDevices:
    def test_non_list_returns_empty(self):
        assert _parse_devices({"not": "a list"}, 50) == []

    def test_skips_stale(self):
        old = time.time() - 3600
        payload = [{
            "kismet.device.base.frequency": 2412000000,
            "kismet.device.base.last_time": old,
            "kismet.device.base.phyname": "IEEE802.11",
            "kismet.device.base.signal": {
                "kismet.common.signal.last_signal": -50,
            },
        }]
        assert _parse_devices(payload, 50) == []

    def test_skips_zero_rssi(self):
        now = time.time()
        payload = [{
            "kismet.device.base.frequency": 2412000000,
            "kismet.device.base.last_time": now,
            "kismet.device.base.phyname": "IEEE802.11",
            "kismet.device.base.signal": {
                "kismet.common.signal.last_signal": 0,
            },
        }]
        assert _parse_devices(payload, 50) == []


# ---------------------------------------------------------------------------
# (a) No config → graceful no-start
# ---------------------------------------------------------------------------

class TestPollerNoConfig:
    def test_empty_host_does_not_start(self, caplog):
        buf = AmbientScanBuffer()
        poller = KismetPoller(buf, host="")
        with caplog.at_level(logging.WARNING,
                             logger="hydra_detect.rf.kismet_poller"):
            assert poller.start() is False
        assert poller._thread is None or not poller._thread.is_alive()
        # Warning was logged, no exception raised
        assert any(
            "empty host" in r.getMessage().lower()
            for r in caplog.records
        )

    def test_empty_host_poll_once_is_noop(self):
        buf = AmbientScanBuffer()
        poller = KismetPoller(buf, host="")
        # No network call, no crash, no samples pushed
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
        ) as mock_open:
            assert poller.poll_once() == 0
            mock_open.assert_not_called()
        assert len(buf) == 0


# ---------------------------------------------------------------------------
# (b) Happy path — JSON parsed, buffer receives push_sample() calls
# ---------------------------------------------------------------------------

class TestPollerHappyPath:
    def test_parses_and_pushes(self):
        buf = AmbientScanBuffer()
        now = time.time()
        payload = [
            {
                "kismet.device.base.frequency": 2412000000,
                "kismet.device.base.last_time": now,
                "kismet.device.base.first_time": now - 1.5,
                "kismet.device.base.phyname": "IEEE802.11",
                "kismet.device.base.signal": {
                    "kismet.common.signal.last_signal": -55,
                },
            },
            {
                "kismet.device.base.frequency": 915000000,
                "kismet.device.base.last_time": now,
                "kismet.device.base.first_time": now - 0.2,
                "kismet.device.base.phyname": "RTL433",
                "kismet.device.base.signal": {
                    "kismet.common.signal.last_signal": -42,
                },
            },
        ]
        poller = KismetPoller(buf, host="http://fake:2501")
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            return_value=_fake_response(payload),
        ):
            pushed = poller.poll_once()

        assert pushed == 2
        snap = buf.get_samples()
        assert len(snap["samples"]) == 2
        freqs = sorted(s["freq_mhz"] for s in snap["samples"])
        assert freqs == [915.0, 2412.0]
        mods = {s["modulation"] for s in snap["samples"]}
        assert mods == {"wifi_2g", "ism_915"}

        wifi = next(s for s in snap["samples"] if s["freq_mhz"] == 2412.0)
        assert wifi["rssi_dbm"] == -55.0
        # 1.5s visibility window → 1500 ms duration
        assert 1400.0 <= wifi["duration_ms"] <= 1600.0

    def test_push_caps_at_max_samples_per_cycle(self):
        buf = AmbientScanBuffer(maxlen=500)
        now = time.time()
        payload = [
            {
                "kismet.device.base.frequency": 2412000000 + i * 1_000_000,
                "kismet.device.base.last_time": now,
                "kismet.device.base.first_time": now - 0.1,
                "kismet.device.base.phyname": "IEEE802.11",
                "kismet.device.base.signal": {
                    "kismet.common.signal.last_signal": -50,
                },
            }
            for i in range(100)
        ]
        poller = KismetPoller(
            buf, host="http://fake:2501", max_samples_per_cycle=10,
        )
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            return_value=_fake_response(payload),
        ):
            pushed = poller.poll_once()
        assert pushed == 10
        assert len(buf) == 10

    def test_basic_auth_header_sent(self):
        buf = AmbientScanBuffer()
        poller = KismetPoller(
            buf, host="http://fake:2501",
            user="kismet", password="kismet",
        )
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            return_value=_fake_response([]),
        ) as mock_open:
            poller.poll_once()
        assert mock_open.call_count == 1
        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization", "").startswith("Basic ")


# ---------------------------------------------------------------------------
# (c) Degrade — connection / parse errors log a warning, never crash
# ---------------------------------------------------------------------------

class TestPollerDegrade:
    def test_connection_error_logs_and_retries(self, caplog):
        buf = AmbientScanBuffer()
        poller = KismetPoller(buf, host="http://fake:2501")
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            side_effect=URLError("refused"),
        ):
            with caplog.at_level(
                logging.WARNING, logger="hydra_detect.rf.kismet_poller",
            ):
                assert poller.poll_once() == 0
                # Second call must not raise either
                assert poller.poll_once() == 0
                assert poller.poll_once() == 0
        messages = [r.getMessage() for r in caplog.records]
        assert any("connection error" in m for m in messages)
        # Counter advanced — retries are happening, poller did not die
        assert poller._consecutive_errors == 3
        assert len(buf) == 0

    def test_bad_json_returns_zero(self, caplog):
        buf = AmbientScanBuffer()
        poller = KismetPoller(buf, host="http://fake:2501")
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"not-valid-json{"
        bad_resp.__enter__.return_value = bad_resp
        bad_resp.__exit__.return_value = False
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            return_value=bad_resp,
        ):
            with caplog.at_level(
                logging.WARNING, logger="hydra_detect.rf.kismet_poller",
            ):
                assert poller.poll_once() == 0
        assert len(buf) == 0
        assert any(
            "parse error" in r.getMessage()
            for r in caplog.records
        )

    def test_recovery_resets_counter(self):
        buf = AmbientScanBuffer()
        poller = KismetPoller(buf, host="http://fake:2501")
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            side_effect=URLError("refused"),
        ):
            poller.poll_once()
            poller.poll_once()
        assert poller._consecutive_errors == 2
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            return_value=_fake_response([]),
        ):
            poller.poll_once()
        assert poller._consecutive_errors == 0


# ---------------------------------------------------------------------------
# (d) Thread lifecycle — stop_event cleanly terminates the daemon thread
# ---------------------------------------------------------------------------

class TestPollerThreadLifecycle:
    def test_stop_exits_thread(self):
        buf = AmbientScanBuffer()
        poller = KismetPoller(
            buf, host="http://fake:2501", poll_interval_sec=0.1,
        )
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            side_effect=URLError("refused"),
        ):
            assert poller.start() is True
            time.sleep(0.15)
            poller.stop(timeout=1.0)
        assert poller._thread is not None
        assert not poller._thread.is_alive()

    def test_external_stop_event(self):
        buf = AmbientScanBuffer()
        stop = threading.Event()
        poller = KismetPoller(
            buf,
            host="http://fake:2501",
            poll_interval_sec=0.1,
            stop_event=stop,
        )
        with patch(
            "hydra_detect.rf.kismet_poller.urlrequest.urlopen",
            side_effect=URLError("refused"),
        ):
            poller.start()
            time.sleep(0.1)
            stop.set()
            assert poller._thread is not None
            poller._thread.join(timeout=1.0)
        assert not poller._thread.is_alive()
