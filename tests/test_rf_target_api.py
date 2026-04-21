"""Tests for POST /api/rf/target — one-click hunt targeting from device feed."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from hydra_detect.web.server import app, stream_state


@pytest.fixture
def client():
    stream_state._callbacks = {}
    return TestClient(app)


class TestTargetEndpointValidation:
    def test_missing_callback_returns_503(self, client):
        resp = client.post("/api/rf/target", json={
            "bssid": "AA:BB:CC:DD:EE:FF", "confirm": True,
        })
        assert resp.status_code == 503

    def test_missing_confirm_rejected(self, client):
        stream_state.set_callbacks(on_rf_target=lambda p: True)
        resp = client.post("/api/rf/target", json={
            "bssid": "AA:BB:CC:DD:EE:FF",
        })
        assert resp.status_code == 400
        assert "confirm" in resp.json()["error"].lower()

    def test_no_target_rejected(self, client):
        stream_state.set_callbacks(on_rf_target=lambda p: True)
        resp = client.post("/api/rf/target", json={"confirm": True})
        assert resp.status_code == 400
        assert "bssid" in resp.json()["error"].lower() \
            or "freq" in resp.json()["error"].lower()

    def test_bad_bssid_rejected(self, client):
        stream_state.set_callbacks(on_rf_target=lambda p: True)
        resp = client.post("/api/rf/target", json={
            "bssid": "not-a-mac", "confirm": True,
        })
        assert resp.status_code == 400

    def test_bad_mode_rejected(self, client):
        stream_state.set_callbacks(on_rf_target=lambda p: True)
        resp = client.post("/api/rf/target", json={
            "bssid": "AA:BB:CC:DD:EE:FF",
            "mode": "bluetooth",
            "confirm": True,
        })
        assert resp.status_code == 400

    def test_bad_freq_rejected(self, client):
        stream_state.set_callbacks(on_rf_target=lambda p: True)
        resp = client.post("/api/rf/target", json={
            "freq_mhz": 99999.0, "confirm": True,
        })
        assert resp.status_code == 400

    def test_freq_string_rejected(self, client):
        stream_state.set_callbacks(on_rf_target=lambda p: True)
        resp = client.post("/api/rf/target", json={
            "freq_mhz": "not a number", "confirm": True,
        })
        assert resp.status_code == 400

    def test_malformed_json_returns_400(self, client):
        resp = client.post(
            "/api/rf/target",
            content=b"not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


class TestTargetEndpointSuccess:
    def test_wifi_target_flows_through(self, client):
        received = {}

        def on_target(params):
            received.update(params)
            return True

        stream_state.set_callbacks(on_rf_target=on_target)
        resp = client.post("/api/rf/target", json={
            "bssid": "aa:bb:cc:dd:ee:ff",
            "mode": "wifi",
            "confirm": True,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # BSSID is canonicalized to uppercase before reaching the callback.
        assert received["bssid"] == "AA:BB:CC:DD:EE:FF"
        assert received["mode"] == "wifi"

    def test_sdr_target_flows_through(self, client):
        received = {}

        def on_target(params):
            received.update(params)
            return True

        stream_state.set_callbacks(on_rf_target=on_target)
        resp = client.post("/api/rf/target", json={
            "freq_mhz": 915.3, "mode": "sdr", "confirm": True,
        })
        assert resp.status_code == 200
        assert received["freq_mhz"] == 915.3
        assert received["mode"] == "sdr"

    def test_callback_returning_false_returns_503(self, client):
        stream_state.set_callbacks(on_rf_target=lambda p: False)
        resp = client.post("/api/rf/target", json={
            "bssid": "AA:BB:CC:DD:EE:FF", "confirm": True,
        })
        assert resp.status_code == 503
