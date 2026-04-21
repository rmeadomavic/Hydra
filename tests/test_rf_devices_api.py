"""Tests for the RF device-feed and events API endpoints."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from hydra_detect.web.server import app, stream_state


@pytest.fixture
def client():
    stream_state._callbacks = {}
    return TestClient(app)


def _device(**overrides):
    base = {
        "bssid": "AA:BB:CC:00:00:01",
        "ssid": "CAFE-GUEST",
        "rssi": -65.0,
        "channel": 6,
        "freq_mhz": 2437.0,
        "manuf": "TP-Link",
        "first_seen": 100.0,
        "last_seen": 120.0,
        "lat": None,
        "lon": None,
        "is_target": False,
    }
    base.update(overrides)
    return base


class TestDevicesEndpoint:
    def test_devices_without_callback(self, client):
        resp = client.get("/api/rf/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"mode": "unavailable", "devices": []}

    def test_devices_returns_list_with_mode(self, client):
        stream_state.set_callbacks(
            get_rf_devices=lambda: {
                "mode": "replay",
                "devices": [
                    _device(rssi=-40.0, is_target=True,
                            bssid="AA:BB:CC:DE:AD:01", ssid="TARGET-NODE"),
                    _device(rssi=-65.0),
                    _device(rssi=-80.0, ssid=None,
                            bssid="AA:BB:CC:00:00:02"),
                ],
            },
        )
        resp = client.get("/api/rf/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "replay"
        assert len(data["devices"]) == 3
        # Is_target flag survives serialization.
        targets = [d for d in data["devices"] if d["is_target"]]
        assert len(targets) == 1
        assert targets[0]["bssid"] == "AA:BB:CC:DE:AD:01"

    def test_devices_live_mode_shape(self, client):
        stream_state.set_callbacks(
            get_rf_devices=lambda: {
                "mode": "live",
                "devices": [_device()],
            },
        )
        resp = client.get("/api/rf/devices")
        data = resp.json()
        assert data["mode"] == "live"
        expected = {
            "bssid", "ssid", "rssi", "channel", "freq_mhz", "manuf",
            "first_seen", "last_seen", "lat", "lon", "is_target",
        }
        assert expected.issubset(data["devices"][0].keys())

    def test_devices_callback_exception_returns_unavailable(self, client):
        def boom():
            raise RuntimeError("kismet down")
        stream_state.set_callbacks(get_rf_devices=boom)
        resp = client.get("/api/rf/devices")
        assert resp.status_code == 200
        assert resp.json() == {"mode": "unavailable", "devices": []}

    def test_devices_no_auth_required(self, client):
        # Ensure GET /api/rf/devices does not require bearer token —
        # dashboard polls it over plain fetch.
        stream_state.set_callbacks(
            get_rf_devices=lambda: {"mode": "live", "devices": []},
        )
        resp = client.get("/api/rf/devices")
        assert resp.status_code == 200


class TestEventsEndpoint:
    def test_events_without_callback(self, client):
        resp = client.get("/api/rf/events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_events_returns_transitions(self, client):
        stream_state.set_callbacks(
            get_rf_events=lambda: [
                {"t": 1.0, "from": "idle", "to": "searching",
                 "samples": 0, "elapsed_prev_sec": 0.0},
                {"t": 10.5, "from": "searching", "to": "homing",
                 "samples": 12, "elapsed_prev_sec": 9.5},
                {"t": 18.2, "from": "homing", "to": "converged",
                 "samples": 34, "elapsed_prev_sec": 7.7},
            ],
        )
        resp = client.get("/api/rf/events")
        data = resp.json()
        assert len(data) == 3
        transitions = [(e["from"], e["to"]) for e in data]
        assert transitions == [
            ("idle", "searching"),
            ("searching", "homing"),
            ("homing", "converged"),
        ]

    def test_events_callback_exception_returns_empty(self, client):
        def boom():
            raise RuntimeError("hunt dead")
        stream_state.set_callbacks(get_rf_events=boom)
        resp = client.get("/api/rf/events")
        assert resp.status_code == 200
        assert resp.json() == []
