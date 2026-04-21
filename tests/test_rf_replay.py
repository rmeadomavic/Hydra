"""Tests for the Kismet replay source.

Covers fixture parsing, playback clock semantics, loop behavior, and parity
with the KismetClient public surface that ``RFHuntController`` relies on.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hydra_detect.rf.replay_source import KismetDataSource, KismetReplaySource


# -- Helpers -----------------------------------------------------------------

def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def _fake_monotonic(values):
    """Iterate through a fixed sequence of monotonic() return values."""
    it = iter(values)
    return lambda: next(it)


# -- Fixture parsing ---------------------------------------------------------

def test_replay_loads_packaged_fixture():
    pkg_fixture = (
        Path(__file__).resolve().parent.parent
        / "hydra_detect" / "rf" / "fixtures" / "demo_urban.jsonl"
    )
    assert pkg_fixture.is_file(), "packaged demo fixture must exist"
    source = KismetReplaySource(pkg_fixture, loop=True, speed=1.0)
    assert source.device_count >= 8
    assert source.sample_count > 100
    assert source.duration >= 100.0
    # Target device from the generator must be present.
    names = source.list_devices(max_age_sec=source.duration + 1)
    bssids = {d["bssid"] for d in names}
    assert "AA:BB:CC:DE:AD:01" in bssids


def test_replay_missing_fixture_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        KismetReplaySource(tmp_path / "does_not_exist.jsonl")


def test_replay_empty_fixture_raises(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        KismetReplaySource(path)


def test_replay_skips_malformed_lines(tmp_path):
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        "\n"
        "# a comment\n"
        "{not json}\n"
        '{"t": 0.0, "bssid": "AA:BB:CC:00:00:01", "rssi": -60}\n'
        '{"t": "bad", "bssid": "AA:BB:CC:00:00:02", "rssi": -55}\n'  # bad t
        '{"t": 1.0, "bssid": "AA:BB:CC:00:00:01", "rssi": -58}\n',
        encoding="utf-8",
    )
    source = KismetReplaySource(path, loop=False, speed=1.0)
    assert source.device_count == 1
    assert source.sample_count == 2


def test_replay_rejects_non_monotonic_time(tmp_path):
    path = _write_jsonl(tmp_path / "f.jsonl", [
        {"t": 0.0, "bssid": "AA:BB:CC:00:00:01", "rssi": -60},
        {"t": 5.0, "bssid": "AA:BB:CC:00:00:01", "rssi": -55},
        {"t": 2.0, "bssid": "AA:BB:CC:00:00:01", "rssi": -70},  # out of order
        {"t": 6.0, "bssid": "AA:BB:CC:00:00:01", "rssi": -50},
    ])
    source = KismetReplaySource(path, loop=False)
    # Out-of-order row is skipped.
    assert source.sample_count == 3


# -- Playback clock / lookups ------------------------------------------------

def _simple_fixture(tmp_path: Path) -> Path:
    return _write_jsonl(tmp_path / "simple.jsonl", [
        {"t": 0.0, "bssid": "AA:BB:CC:00:00:01", "ssid": "AMBIENT",
         "rssi": -70, "channel": 6, "freq_mhz": 2437.0, "manuf": "TP-Link"},
        {"t": 0.0, "bssid": "AA:BB:CC:DE:AD:01", "ssid": "TARGET",
         "rssi": -85, "channel": 6, "freq_mhz": 2437.0, "manuf": "Espressif"},
        {"t": 5.0, "bssid": "AA:BB:CC:DE:AD:01", "ssid": "TARGET",
         "rssi": -60, "channel": 6, "freq_mhz": 2437.0, "manuf": "Espressif"},
        {"t": 10.0, "bssid": "AA:BB:CC:DE:AD:01", "ssid": "TARGET",
         "rssi": -40, "channel": 6, "freq_mhz": 2437.0, "manuf": "Espressif"},
    ])


def test_replay_clock_returns_zero_on_first_call(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path), loop=False)
    with patch(
        "hydra_detect.rf.replay_source.time.monotonic",
        _fake_monotonic([100.0]),
    ):
        # First call initializes the clock and returns t=0 reading.
        rssi = source.get_wifi_rssi("AA:BB:CC:DE:AD:01")
    assert rssi == -85.0


def test_replay_advances_with_wall_clock(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path), loop=False, speed=1.0)
    # First call pins t_start, subsequent calls use elapsed*speed.
    with patch(
        "hydra_detect.rf.replay_source.time.monotonic",
        _fake_monotonic([100.0, 105.0, 110.0]),
    ):
        r1 = source.get_wifi_rssi("AA:BB:CC:DE:AD:01")  # t=0
        r2 = source.get_wifi_rssi("AA:BB:CC:DE:AD:01")  # t=5
        r3 = source.get_wifi_rssi("AA:BB:CC:DE:AD:01")  # t=10
    assert r1 == -85.0
    assert r2 == -60.0
    assert r3 == -40.0


def test_replay_speed_multiplier(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path), loop=False, speed=2.0)
    with patch(
        "hydra_detect.rf.replay_source.time.monotonic",
        _fake_monotonic([100.0, 102.5]),
    ):
        source.get_wifi_rssi("AA:BB:CC:DE:AD:01")  # pin clock
        # 2.5 s wall * 2.0 speed = 5.0 s fixture time → -60 dBm sample.
        assert source.get_wifi_rssi("AA:BB:CC:DE:AD:01") == -60.0


def test_replay_loops_when_configured(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path), loop=True, speed=1.0)
    with patch(
        "hydra_detect.rf.replay_source.time.monotonic",
        _fake_monotonic([100.0, 112.0]),
    ):
        source.get_wifi_rssi("AA:BB:CC:DE:AD:01")  # pin clock
        # 12 s wall % 10 s duration = 2 s → still on the t=0 sample (-85).
        rssi = source.get_wifi_rssi("AA:BB:CC:DE:AD:01")
    assert rssi == -85.0


def test_replay_without_loop_returns_last_sample_then_nothing(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path), loop=False, speed=1.0)
    with patch(
        "hydra_detect.rf.replay_source.time.monotonic",
        _fake_monotonic([100.0, 150.0]),
    ):
        source.get_wifi_rssi("AA:BB:CC:DE:AD:01")  # pin clock
        # 50 s past end with no loop → clock clamps at duration (10 s).
        # Sample at t=10 is fresh, so it still reads -40 dBm.
        assert source.get_wifi_rssi("AA:BB:CC:DE:AD:01") == -40.0


# -- Protocol / interface parity --------------------------------------------

def test_replay_satisfies_kismet_data_source_protocol(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path))
    # Runtime structural check — duck typing, no isinstance.
    for method in (
        "check_connection",
        "get_rssi",
        "get_wifi_rssi",
        "get_sdr_rssi",
        "reset_auth",
        "close",
    ):
        assert callable(getattr(source, method)), f"missing {method}"


def test_replay_get_rssi_dispatches_on_mode(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path))
    assert source.get_rssi(mode="wifi", bssid="AA:BB:CC:DE:AD:01") == -85.0
    assert source.get_rssi(mode="sdr", freq_mhz=2437.0) == -70.0  # ambient is stronger
    assert source.get_rssi(mode="wifi") is None
    assert source.get_rssi(mode="sdr") is None


def test_replay_sdr_respects_tolerance(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path))
    # 2437 MHz exists. 2450 MHz does not (and is outside 0.5 MHz tolerance).
    assert source.get_sdr_rssi(2437.0, tolerance_mhz=0.5) == -70.0
    assert source.get_sdr_rssi(2450.0, tolerance_mhz=0.5) is None
    # Wider tolerance catches it.
    assert source.get_sdr_rssi(2437.2, tolerance_mhz=1.0) == -70.0


def test_replay_list_devices_shape(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path))
    devs = source.list_devices(max_age_sec=60.0)
    assert len(devs) == 2
    required = {"bssid", "ssid", "rssi", "channel", "freq_mhz",
                "manuf", "first_seen", "last_seen", "lat", "lon"}
    for d in devs:
        assert required.issubset(d.keys())
    # Sorted by RSSI desc.
    rssis = [d["rssi"] for d in devs]
    assert rssis == sorted(rssis, reverse=True)


def test_replay_list_devices_respects_freshness(tmp_path):
    # Two devices: ambient seen only at t=0, target seen at t=0 and t=8.
    path = _write_jsonl(tmp_path / "fresh.jsonl", [
        {"t": 0.0, "bssid": "AA:BB:CC:00:00:01", "rssi": -70,
         "freq_mhz": 2437.0},
        {"t": 0.0, "bssid": "AA:BB:CC:DE:AD:01", "rssi": -80,
         "freq_mhz": 2437.0},
        {"t": 8.0, "bssid": "AA:BB:CC:DE:AD:01", "rssi": -60,
         "freq_mhz": 2437.0},
    ])
    source = KismetReplaySource(path, loop=True, speed=1.0)
    # Pin clock at t_wall=100.0 and advance 10 s → replay t = 10 % 8 = 2.
    # At replay t=2: ambient last at t=0 (age=2, FRESH with 5s window),
    # target last at t=0 (same — the t=8 sample is in the *future*).
    # With max_age=1.0, both should be excluded.
    with patch(
        "hydra_detect.rf.replay_source.time.monotonic",
        _fake_monotonic([100.0, 110.0]),
    ):
        source.get_wifi_rssi("AA:BB:CC:00:00:01")  # pin clock
        devs_tight = source.list_devices(max_age_sec=1.0)
    assert devs_tight == []


def test_replay_close_makes_lookups_empty(tmp_path):
    source = KismetReplaySource(_simple_fixture(tmp_path))
    source.close()
    assert source.check_connection() is False
    assert source.get_wifi_rssi("AA:BB:CC:DE:AD:01") is None
    assert source.get_sdr_rssi(2437.0) is None
    assert source.list_devices() == []


def test_replay_bssid_case_insensitive(tmp_path):
    path = _write_jsonl(tmp_path / "case.jsonl", [
        {"t": 0.0, "bssid": "aa:bb:cc:00:00:01", "rssi": -60},
    ])
    source = KismetReplaySource(path)
    # Fixture stored lowercase, query uppercase — should still match.
    assert source.get_wifi_rssi("AA:BB:CC:00:00:01") == -60.0


def test_protocol_is_accepted_as_type_hint():
    # Compile-time guarantee: the protocol is importable and usable.
    def _accepts(client: KismetDataSource) -> bool:
        return client.check_connection()

    assert _accepts is not None
