"""Tests for RF hunt web API endpoints."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state


@pytest.fixture(autouse=True)
def _reset_state():
    configure_auth(None)
    stream_state._callbacks = {}
    yield


@pytest.fixture
def client():
    """Create a test client and reset stream_state callbacks."""
    return TestClient(app)


class TestRFStatusEndpoint:
    def test_status_returns_unavailable_without_callback(self, client):
        resp = client.get("/api/rf/status")
        assert resp.status_code == 200
        assert resp.json()["state"] == "unavailable"

    def test_status_returns_hunt_data(self, client):
        stream_state.set_callbacks(
            get_rf_status=lambda: {
                "state": "searching",
                "mode": "sdr",
                "target": "915.0 MHz",
                "best_rssi": -75.0,
                "best_lat": 34.05,
                "best_lon": -118.25,
                "samples": 12,
                "wp_progress": "3/10",
            }
        )
        resp = client.get("/api/rf/status")
        data = resp.json()
        assert data["state"] == "searching"
        assert data["best_rssi"] == -75.0
        assert data["samples"] == 12


class TestRFStartEndpoint:
    def test_start_requires_auth_by_default_when_token_empty(self, client):
        resp = client.post("/api/rf/start", json={"mode": "wifi", "target_bssid": "AA:BB:CC:DD:EE:FF"})
        assert resp.status_code == 401

    def test_start_missing_bearer_rejected_when_token_configured(self, client):
        configure_auth("secret-token")
        resp = client.post("/api/rf/start", json={"mode": "wifi", "target_bssid": "AA:BB:CC:DD:EE:FF"})
        assert resp.status_code == 401

    def test_start_accepts_valid_bearer_token(self, client):
        configure_auth("secret-token")
        stream_state.set_callbacks(on_rf_start=lambda params: True)
        resp = client.post(
            "/api/rf/start",
            json={"mode": "sdr", "target_freq_mhz": 915.0},
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200

    def test_start_without_callback(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/start", json={"mode": "wifi", "target_bssid": "AA:BB:CC:DD:EE:FF"})
        assert resp.status_code == 503

    def test_start_invalid_mode(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/start", json={"mode": "bluetooth"})
        assert resp.status_code == 400
        assert "mode" in resp.json()["error"]

    def test_start_wifi_no_bssid(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/start", json={"mode": "wifi"})
        assert resp.status_code == 400
        assert "bssid" in resp.json()["error"].lower()

    def test_start_invalid_bssid_format(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/start", json={"mode": "wifi", "target_bssid": "invalid"})
        assert resp.status_code == 400

    def test_start_invalid_freq(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/start", json={"mode": "sdr", "target_freq_mhz": 99999})
        assert resp.status_code == 400

    def test_start_invalid_search_pattern(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/start", json={
            "mode": "sdr", "target_freq_mhz": 915.0,
            "search_pattern": "zigzag"
        })
        assert resp.status_code == 400

    def test_start_invalid_numeric_field(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/start", json={
            "mode": "sdr", "target_freq_mhz": 915.0,
            "search_area_m": 5000.0
        })
        assert resp.status_code == 400

    def test_start_success(self, client):
        configure_auth(None, allow_insecure_control=True)
        stream_state.set_callbacks(on_rf_start=lambda params: True)
        resp = client.post("/api/rf/start", json={
            "mode": "sdr",
            "target_freq_mhz": 915.0,
            "search_pattern": "spiral",
            "search_area_m": 200.0,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_start_callback_returns_false(self, client):
        configure_auth(None, allow_insecure_control=True)
        stream_state.set_callbacks(on_rf_start=lambda params: False)
        resp = client.post("/api/rf/start", json={
            "mode": "sdr",
            "target_freq_mhz": 915.0,
        })
        assert resp.status_code == 503

    def test_start_sdr_valid(self, client):
        configure_auth(None, allow_insecure_control=True)
        received = {}

        def on_start(params):
            received.update(params)
            return True

        stream_state.set_callbacks(on_rf_start=on_start)
        resp = client.post("/api/rf/start", json={
            "mode": "sdr",
            "target_freq_mhz": 433.92,
            "search_area_m": 100.0,
            "search_alt_m": 15.0,
            "rssi_threshold_dbm": -80.0,
            "rssi_converge_dbm": -40.0,
            "gradient_step_m": 5.0,
        })
        assert resp.status_code == 200
        assert received["mode"] == "sdr"
        assert received["target_freq_mhz"] == 433.92


class TestRFStopEndpoint:
    def test_stop_requires_auth_by_default_when_token_empty(self, client):
        resp = client.post("/api/rf/stop")
        assert resp.status_code == 401

    def test_stop_without_callback(self, client):
        configure_auth(None, allow_insecure_control=True)
        resp = client.post("/api/rf/stop")
        assert resp.status_code == 503

    def test_stop_success(self, client):
        configure_auth(None, allow_insecure_control=True)
        stopped = []
        stream_state.set_callbacks(on_rf_stop=lambda: stopped.append(True))
        resp = client.post("/api/rf/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert stopped == [True]
