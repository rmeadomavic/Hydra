"""Vendored copy of hydra_detect/tak/adsb_codec.py — keep in sync.

This file must stay byte-compatible with the Jetson-side codec so callsigns,
ICAO-to-track-id mapping, and flag layout match. If you change the wire
format on one side, change it on both.
"""

from __future__ import annotations

from dataclasses import dataclass

EMITTER_NO_INFO = 0
EMITTER_PEDESTRIAN = 11
EMITTER_CYCLIST = 12
EMITTER_VEHICLE_SERVICE = 15
EMITTER_LARGE = 3
EMITTER_POINT_OBSTACLE = 16

FLAG_DETECTED_THIS_FRAME = 1 << 8
FLAG_LOCKED = 1 << 9
FLAG_SIM_GPS = 1 << 10


def _abbr(label: str, n: int = 3) -> str:
    clean = "".join(ch for ch in label.upper() if ch.isalpha())
    if not clean:
        clean = "UNK"
    return (clean + "XXX")[:n]


def unpack_callsign(callsign: str) -> tuple[str, int] | None:
    cs = callsign.strip("\x00 ")
    if len(cs) < 8 or not cs.startswith("H"):
        return None
    abbr = cs[1:4]
    tail = cs[4:8]
    if not tail.isdigit():
        return None
    return (abbr, int(tail))


LABEL_TO_EMITTER: dict[str, int] = {
    "person": EMITTER_PEDESTRIAN,
    "bicycle": EMITTER_CYCLIST,
    "motorcycle": EMITTER_VEHICLE_SERVICE,
    "car": EMITTER_VEHICLE_SERVICE,
    "truck": EMITTER_VEHICLE_SERVICE,
    "bus": EMITTER_LARGE,
    "boat": EMITTER_POINT_OBSTACLE,
    "airplane": EMITTER_LARGE,
}

ABBR_TO_LABEL: dict[str, str] = {
    _abbr(label, 3): label for label in LABEL_TO_EMITTER
}


def label_from_abbr(abbr: str) -> str:
    return ABBR_TO_LABEL.get(abbr.upper(), abbr.lower())


@dataclass
class HydraTrackEvent:
    track_id: int
    lat: float
    lon: float
    hae_m: float
    label_abbr: str
    confidence: float
    age_sec: int
    detected_this_frame: bool
    locked: bool
    sim_gps: bool

    @property
    def label(self) -> str:
        return label_from_abbr(self.label_abbr)


def decode_adsb_vehicle(msg) -> HydraTrackEvent | None:
    try:
        raw_callsign = msg.callsign
    except AttributeError:
        return None

    if isinstance(raw_callsign, bytes):
        callsign = raw_callsign.decode("ascii", errors="replace")
    else:
        callsign = str(raw_callsign)

    parsed = unpack_callsign(callsign)
    if parsed is None:
        return None

    flags = int(getattr(msg, "flags", 0))
    squawk = int(getattr(msg, "squawk", 0))

    return HydraTrackEvent(
        track_id=int(msg.ICAO_address) & 0xFFFFFF,
        lat=int(msg.lat) / 1e7,
        lon=int(msg.lon) / 1e7,
        hae_m=int(msg.altitude) / 1000.0,
        label_abbr=parsed[0],
        confidence=max(0.0, min(1.0, squawk / 4095.0)),
        age_sec=int(getattr(msg, "tslc", 0)),
        detected_this_frame=bool(flags & FLAG_DETECTED_THIS_FRAME),
        locked=bool(flags & FLAG_LOCKED),
        sim_gps=bool(flags & FLAG_SIM_GPS),
    )
