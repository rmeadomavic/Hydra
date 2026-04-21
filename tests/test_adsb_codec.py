"""Tests for the Hydra ADSB_VEHICLE encoding used by the MAVLink relay."""
from __future__ import annotations

from types import SimpleNamespace

from hydra_detect.tak import adsb_codec


def test_pack_unpack_callsign_roundtrip():
    cs = adsb_codec.pack_callsign("person", 42)
    assert cs.startswith("H")
    assert len(cs) == 9
    parsed = adsb_codec.unpack_callsign(cs)
    assert parsed is not None
    abbr, short_id = parsed
    assert abbr == "PER"
    assert short_id == 42


def test_callsign_truncates_long_track_ids():
    # 24-bit ICAO space is wider than the 4-digit short id; the codec only
    # guarantees the short tail and the ICAO carries the authoritative id.
    cs = adsb_codec.pack_callsign("car", 123456)
    parsed = adsb_codec.unpack_callsign(cs)
    assert parsed is not None
    abbr, short_id = parsed
    assert abbr == "CAR"
    assert 0 <= short_id < 10000


def test_unpack_rejects_non_hydra_callsigns():
    assert adsb_codec.unpack_callsign("N12345 ") is None
    assert adsb_codec.unpack_callsign("UNITED1 ") is None
    assert adsb_codec.unpack_callsign("") is None


def test_build_adsb_kwargs_basic_fields():
    kwargs = adsb_codec.build_adsb_kwargs(
        track_id=7,
        lat=47.1234567,
        lon=8.5432109,
        hae_m=25.0,
        label="person",
        confidence=0.85,
        age_sec=3,
        detected=True,
        locked=True,
        sim_gps=False,
    )
    assert kwargs["ICAO_address"] == 7
    assert kwargs["lat"] == int(47.1234567 * 1e7)
    assert kwargs["lon"] == int(8.5432109 * 1e7)
    assert kwargs["altitude"] == 25000
    assert kwargs["emitter_type"] == adsb_codec.EMITTER_PEDESTRIAN
    assert kwargs["tslc"] == 3
    # Confidence 0.85 → squawk clamped to 0..4095, ~3481
    assert 3000 <= kwargs["squawk"] <= 4095
    # Flags must include valid-data bits + detected + locked
    assert kwargs["flags"] & adsb_codec.ADSB_VALID_COORDS_ALT
    assert kwargs["flags"] & adsb_codec.FLAG_DETECTED_THIS_FRAME
    assert kwargs["flags"] & adsb_codec.FLAG_LOCKED
    assert not (kwargs["flags"] & adsb_codec.FLAG_SIM_GPS)


def test_decode_roundtrip_preserves_key_fields():
    lat, lon = 47.1234567, 8.5432109
    kwargs = adsb_codec.build_adsb_kwargs(
        track_id=99,
        lat=lat,
        lon=lon,
        hae_m=15.5,
        label="car",
        confidence=0.5,
        age_sec=10,
        detected=True,
        locked=False,
        sim_gps=True,
    )
    # Fake a MAVLink message object exposing the same attributes.
    msg = SimpleNamespace(
        ICAO_address=kwargs["ICAO_address"],
        lat=kwargs["lat"],
        lon=kwargs["lon"],
        altitude=kwargs["altitude"],
        callsign=kwargs["callsign"],  # bytes
        emitter_type=kwargs["emitter_type"],
        tslc=kwargs["tslc"],
        flags=kwargs["flags"],
        squawk=kwargs["squawk"],
    )
    evt = adsb_codec.decode_adsb_vehicle(msg)
    assert evt is not None
    assert evt.track_id == 99
    assert abs(evt.lat - lat) < 1e-6
    assert abs(evt.lon - lon) < 1e-6
    assert abs(evt.hae_m - 15.5) < 1e-3
    assert evt.label_abbr == "CAR"
    assert evt.age_sec == 10
    assert evt.detected_this_frame is True
    assert evt.locked is False
    assert evt.sim_gps is True
    # Confidence survives squawk round-trip to within quantisation (~0.0003).
    assert abs(evt.confidence - 0.5) < 0.01


def test_decode_ignores_non_hydra_adsb_traffic():
    # Real ADS-B traffic from a commercial aircraft shouldn't be decoded.
    msg = SimpleNamespace(
        ICAO_address=0xABCDEF,
        lat=int(47.1 * 1e7),
        lon=int(8.5 * 1e7),
        altitude=10000000,
        callsign=b"UAL123  ",
        emitter_type=3,
        tslc=0,
        flags=3,
        squawk=1200,
    )
    assert adsb_codec.decode_adsb_vehicle(msg) is None


def test_label_from_abbr_reverse_lookup():
    assert adsb_codec.label_from_abbr("PER") == "person"
    assert adsb_codec.label_from_abbr("CAR") == "car"
    # Unknown abbr falls back to lowercase input.
    assert adsb_codec.label_from_abbr("XYZ") == "xyz"


def test_emitter_for_label_defaults():
    assert adsb_codec.emitter_for_label("person") == adsb_codec.EMITTER_PEDESTRIAN
    assert adsb_codec.emitter_for_label("banana") == adsb_codec.EMITTER_NO_INFO
