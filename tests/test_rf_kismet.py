"""Tests for Kismet REST API client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hydra_detect.rf.kismet_client import KismetClient


class TestKismetDependencyPolicy:
    @patch("hydra_detect.rf.kismet_client.requests.Session")
    def test_client_uses_requests_session_dependency(self, mock_session_cls):
        client = KismetClient()

        mock_session_cls.assert_called_once_with()
        assert client._session is mock_session_cls.return_value


class TestKismetConnection:
    @patch("hydra_detect.rf.kismet_client.requests.Session")
    def test_connection_success(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        response = MagicMock()
        response.status_code = 200
        session.get.return_value = response

        client = KismetClient(host="http://localhost:2501")
        client._session = session
        assert client.check_connection() is True

    @patch("hydra_detect.rf.kismet_client.requests.Session")
    def test_connection_failure(self, mock_session_cls):
        import requests
        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.side_effect = requests.ConnectionError("refused")

        client = KismetClient()
        client._session = session
        assert client.check_connection() is False


    @patch("hydra_detect.rf.kismet_client.requests.Session")
    def test_connection_timeout_failure(self, mock_session_cls):
        import requests
        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.side_effect = requests.Timeout("timed out")

        client = KismetClient()
        client._session = session
        assert client.check_connection() is False

    @patch("hydra_detect.rf.kismet_client.requests.Session")
    def test_connection_bad_status(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        response = MagicMock()
        response.status_code = 401
        session.get.return_value = response

        client = KismetClient()
        client._session = session
        assert client.check_connection() is False


class TestWifiRSSI:
    def test_returns_rssi(self):
        client = KismetClient()
        client._session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [{
            "kismet.device.base.signal": {
                "kismet.common.signal.last_signal": -55,
            },
        }]
        client._session.get.return_value = response

        rssi = client.get_wifi_rssi("AA:BB:CC:DD:EE:FF")
        assert rssi == -55.0

    def test_returns_none_when_not_found(self):
        client = KismetClient()
        client._session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = []
        client._session.get.return_value = response

        assert client.get_wifi_rssi("AA:BB:CC:DD:EE:FF") is None

    def test_returns_none_on_error(self):
        import requests
        client = KismetClient()
        client._session = MagicMock()
        client._session.get.side_effect = requests.ConnectionError("nope")

        assert client.get_wifi_rssi("AA:BB:CC:DD:EE:FF") is None

    def test_returns_none_on_zero_rssi(self):
        """Kismet reports 0 when no signal data is available."""
        client = KismetClient()
        client._session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [{
            "kismet.device.base.signal": {
                "kismet.common.signal.last_signal": 0,
            },
        }]
        client._session.get.return_value = response

        assert client.get_wifi_rssi("AA:BB:CC:DD:EE:FF") is None


class TestSDRRSSI:
    def test_returns_best_rssi_near_freq(self):
        client = KismetClient()
        client._session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [
            {
                "kismet.device.base.frequency": 433920000,  # 433.92 MHz in Hz
                "kismet.device.base.signal": {
                    "kismet.common.signal.last_signal": -65,
                },
            },
            {
                "kismet.device.base.frequency": 915000000,  # 915 MHz
                "kismet.device.base.signal": {
                    "kismet.common.signal.last_signal": -70,
                },
            },
        ]
        client._session.get.return_value = response

        # Hunt 433.92 MHz — should get -65
        rssi = client.get_sdr_rssi(433.92, tolerance_mhz=0.5)
        assert rssi == -65.0

    def test_returns_none_when_no_match(self):
        client = KismetClient()
        client._session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [
            {
                "kismet.device.base.frequency": 433920000,
                "kismet.device.base.signal": {
                    "kismet.common.signal.last_signal": -65,
                },
            },
        ]
        client._session.get.return_value = response

        # Hunt 915 MHz — no match
        assert client.get_sdr_rssi(915.0) is None


class TestUnifiedGetter:
    def test_wifi_mode(self):
        client = KismetClient()
        client._session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [{
            "kismet.device.base.signal": {
                "kismet.common.signal.last_signal": -50,
            },
        }]
        client._session.get.return_value = response

        rssi = client.get_rssi(mode="wifi", bssid="AA:BB:CC:DD:EE:FF")
        assert rssi == -50.0

    def test_invalid_mode(self):
        client = KismetClient()
        assert client.get_rssi(mode="wifi", bssid=None) is None
        assert client.get_rssi(mode="sdr", freq_mhz=None) is None

    def test_close(self):
        client = KismetClient()
        client._session = MagicMock()
        client.close()
        client._session.close.assert_called_once()

    def test_reset_auth_clears_cached_session_state(self):
        client = KismetClient()
        client._session = MagicMock()
        client._authenticated = True
        client._session.auth = ("user", "pass")

        client.reset_auth()

        assert client._authenticated is False
        assert client._session.auth is None
        client._session.cookies.clear.assert_called_once()

    def test_context_manager(self):
        with KismetClient() as client:
            assert client._session is not None

    def test_invalid_host_url(self):
        import pytest
        with pytest.raises(ValueError, match="HTTP"):
            KismetClient(host="not-a-url")

    def test_freq_normalisation_hz(self):
        """Kismet reports Hz — should normalise to MHz correctly."""
        client = KismetClient()
        client._session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [
            {
                "kismet.device.base.frequency": 915000000,  # 915 MHz in Hz
                "kismet.device.base.signal": {
                    "kismet.common.signal.last_signal": -60,
                },
            },
        ]
        client._session.get.return_value = response

        rssi = client.get_sdr_rssi(915.0)
        assert rssi == -60.0
