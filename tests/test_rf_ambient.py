"""Tests for the ambient RF sample buffer and /api/rf/ambient_scan."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from hydra_detect.rf.ambient_scan import AmbientScanBuffer, _MAXLEN
from hydra_detect.web import server as server_module


@pytest.fixture
def client():
    return TestClient(server_module.app)


@pytest.fixture(autouse=True)
def _reset_scanner_ref():
    server_module.set_rf_ambient_scan(None)
    yield
    server_module.set_rf_ambient_scan(None)


# ---------------------------------------------------------------------------
# AmbientScanBuffer unit tests
# ---------------------------------------------------------------------------

class TestAmbientScanBuffer:
    def test_empty_shape(self):
        buf = AmbientScanBuffer()
        snap = buf.get_samples()
        assert snap["samples"] == []
        assert snap["window_seconds"] >= 1
        assert snap["max_rssi"] is None

    def test_push_then_read(self):
        buf = AmbientScanBuffer()
        buf.push_sample(
            freq_mhz=2412.0, rssi_dbm=-55.0,
            modulation="wifi_2g", duration_ms=12.5,
        )
        snap = buf.get_samples()
        assert len(snap["samples"]) == 1
        s = snap["samples"][0]
        assert s["freq_mhz"] == 2412.0
        assert s["rssi_dbm"] == -55.0
        assert s["modulation"] == "wifi_2g"
        assert s["duration_ms"] == 12.5
        assert snap["max_rssi"] == -55.0

    def test_bounded_push(self):
        buf = AmbientScanBuffer()
        for i in range(_MAXLEN + 50):
            buf.push_sample(freq_mhz=2400.0 + i, rssi_dbm=-70.0)
        snap = buf.get_samples()
        assert len(snap["samples"]) == _MAXLEN

    def test_window_eviction(self):
        buf = AmbientScanBuffer(window_seconds=5)
        # Stale sample — should be evicted
        old = time.time() - 1000
        buf.push_sample(freq_mhz=915.0, rssi_dbm=-80.0, ts=old)
        # Fresh sample — should survive
        buf.push_sample(freq_mhz=916.0, rssi_dbm=-60.0)
        snap = buf.get_samples()
        assert len(snap["samples"]) == 1
        assert snap["samples"][0]["freq_mhz"] == 916.0


# ---------------------------------------------------------------------------
# /api/rf/ambient_scan endpoint
# ---------------------------------------------------------------------------

class TestAmbientScanEndpoint:
    def test_no_scanner_returns_idle(self, client):
        r = client.get("/api/rf/ambient_scan")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert body["samples"] == []
        assert body["max_rssi"] is None
        assert body["window_seconds"] >= 1

    def test_registered_scanner_shape(self, client):
        buf = AmbientScanBuffer()
        buf.push_sample(
            freq_mhz=5805.0, rssi_dbm=-42.0,
            modulation="fpv_raceband", duration_ms=2.0,
        )
        server_module.set_rf_ambient_scan(buf)
        r = client.get("/api/rf/ambient_scan")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert len(body["samples"]) == 1
        s = body["samples"][0]
        assert s["freq_mhz"] == 5805.0
        assert s["rssi_dbm"] == -42.0
        assert s["modulation"] == "fpv_raceband"
        assert body["max_rssi"] == -42.0
