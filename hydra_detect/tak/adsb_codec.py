"""Encode and decode Hydra detections as ADSB_VEHICLE MAVLink messages.

This is the bridge format for the offline-Jetson / online-ground-station case.
The Jetson packs each throttled detection into a single ADSB_VEHICLE frame
(~38 bytes), ArduPilot auto-forwards it across the telemetry radio link to the
GCS, and the ground-station relay tool decodes it back into a CoT marker.

Why ADSB_VEHICLE?
    * It is a standard common-dialect MAVLink message, so pymavlink can build
      it without dialect patches.
    * ArduPilot forwards ADSB_VEHICLE to every MAVLink serial port on its own
      (see ADS-B receiver docs); no custom firmware required. The GCS-facing
      port must have ``SR*_ADSB`` set > 0 to publish.
    * One message carries lat/lon/alt/callsign/ID — STATUSTEXT (50 bytes) is
      too small and ArduPilot does not support ENCAPSULATED_DATA.

Encoding conventions (also used by the GCS tool in ``tools/hydra_relay.py``):
    * ``ICAO_address``  = ``track_id & 0xFFFFFF`` (24-bit key for the receiver)
    * ``lat``/``lon``   = degrees × 1e7 (MAVLink standard)
    * ``altitude``      = Jetson GPS altitude in millimetres (target is ground-
      projected; this is the best single-value hint we have)
    * ``callsign``      = 9 bytes packed as ``H`` + 3 chars of the YOLO label +
      4 digits of the track id (zero-padded).
    * ``emitter_type``  = coarse ADSB_EMITTER_TYPE mapped from the label.
    * ``squawk``        = confidence × 10000 (0..10000 bp).
    * ``tslc``          = age in seconds since the track was first seen.
    * ``flags``         = HydraFlags bitfield (detected / locked / sim-GPS).
"""

from __future__ import annotations

from dataclasses import dataclass

# ADSB_EMITTER_TYPE enum values (MAVLink common dialect).
EMITTER_NO_INFO = 0
EMITTER_LIGHT = 1
EMITTER_SMALL = 2
EMITTER_LARGE = 3
EMITTER_ROTOCRAFT = 7
EMITTER_PEDESTRIAN = 11
EMITTER_CYCLIST = 12
EMITTER_VEHICLE_EMERGENCY = 14
EMITTER_VEHICLE_SERVICE = 15
EMITTER_POINT_OBSTACLE = 16
EMITTER_UAV = 8

# HydraFlags — packed into ADSB_VEHICLE.flags (16 bits).
# The low bits overlap harmlessly with ADSB_FLAGS valid-data bits, which most
# decoders treat as advisory. ADTS receivers that strictly validate those bits
# will ignore these frames, which is fine — we only care about Hydra-aware
# receivers.
FLAG_DETECTED_THIS_FRAME = 1 << 8
FLAG_LOCKED = 1 << 9
FLAG_SIM_GPS = 1 << 10

# Minimum ADSB_VEHICLE.flags value we set so the receiver treats lat/lon/alt
# fields as valid. Bits 0 (coords), 1 (alt), 2 (heading), 3 (velocity) per
# ADSB_FLAGS enum. We set coords + alt.
ADSB_VALID_COORDS_ALT = 0b0011

# Label → ADSB emitter type. Kept coarse; the GCS tool re-maps to a precise
# CoT type via ``type_mapping.get_cot_type``.
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


def _abbr(label: str, n: int = 3) -> str:
    """Return an n-char uppercase abbreviation of a label, padded with 'X'."""
    clean = "".join(ch for ch in label.upper() if ch.isalpha())
    if not clean:
        clean = "UNK"
    return (clean + "XXX")[:n]


def pack_callsign(label: str, track_id: int) -> str:
    """Build the 9-char ADSB callsign.

    Format: ``H`` + 3-char label abbr + 4 zero-padded digits of ``track_id``.
    Total 8 chars; pad with a trailing space to 9 so the GCS decoder can rely
    on a fixed width. Example: ``person`` #42 → ``HPER0042 ``.
    """
    abbr = _abbr(label, 3)
    return f"H{abbr}{(track_id & 0xFFFF) % 10000:04d} "


def unpack_callsign(callsign: str) -> tuple[str, int] | None:
    """Inverse of :func:`pack_callsign`.

    Returns ``(label_abbr, short_track_id)`` or ``None`` if the callsign is
    not in the expected Hydra format. The 24-bit ICAO is the authoritative
    track id — this short form is just a human-readable hint.
    """
    cs = callsign.strip("\x00 ")
    if len(cs) < 8 or not cs.startswith("H"):
        return None
    abbr = cs[1:4]
    tail = cs[4:8]
    if not tail.isdigit():
        return None
    return (abbr, int(tail))


def emitter_for_label(label: str) -> int:
    return LABEL_TO_EMITTER.get(label.lower(), EMITTER_NO_INFO)


def clamp_squawk(confidence: float) -> int:
    """Pack confidence (0.0–1.0) into the 4-digit squawk field (0–7777 octal).

    ADSB_VEHICLE.squawk is int16 but real ADS-B squawks are octal 0000–7777
    (max 4095). We clamp so strict receivers don't reject the frame.
    """
    return max(0, min(4095, int(confidence * 4095)))


@dataclass
class HydraTrackEvent:
    """Decoded payload recovered from an ADSB_VEHICLE frame on the GCS side."""

    track_id: int           # 24-bit ICAO from the frame
    lat: float              # degrees
    lon: float              # degrees
    hae_m: float            # height above ellipsoid, metres (drone altitude)
    label_abbr: str         # 3-char label abbreviation from callsign
    confidence: float       # 0..1, recovered from squawk
    age_sec: int            # seconds since first detection
    detected_this_frame: bool
    locked: bool
    sim_gps: bool


def build_adsb_kwargs(
    *,
    track_id: int,
    lat: float,
    lon: float,
    hae_m: float,
    label: str,
    confidence: float,
    age_sec: int,
    detected: bool = True,
    locked: bool = False,
    sim_gps: bool = False,
) -> dict:
    """Build the kwargs dict for ``mavlink.MAVLink_adsb_vehicle_message``.

    Keeps the pymavlink import out of this module so the codec is pure-Python
    and unit-testable without a MAVLink installation. The caller is expected
    to be ``MAVLinkRelayOutput`` on the Jetson (uses pymavlink) or a test.
    """
    icao = track_id & 0xFFFFFF
    callsign = pack_callsign(label, track_id)

    flags = ADSB_VALID_COORDS_ALT
    if detected:
        flags |= FLAG_DETECTED_THIS_FRAME
    if locked:
        flags |= FLAG_LOCKED
    if sim_gps:
        flags |= FLAG_SIM_GPS

    return {
        "ICAO_address": icao,
        "lat": int(lat * 1e7),
        "lon": int(lon * 1e7),
        "altitude_type": 1,  # 1 = geometric (GPS HAE)
        "altitude": int(hae_m * 1000.0),  # millimetres
        "heading": 0,
        "hor_velocity": 0,
        "ver_velocity": 0,
        "callsign": callsign.encode("ascii"),
        "emitter_type": emitter_for_label(label),
        "tslc": max(0, min(255, age_sec)),
        "flags": flags,
        "squawk": clamp_squawk(confidence),
    }


def decode_adsb_vehicle(msg) -> HydraTrackEvent | None:
    """Decode a received ``ADSB_VEHICLE`` message back into a Hydra event.

    Returns ``None`` if the callsign does not look like a Hydra frame, so the
    GCS tool can safely ignore real ADS-B aircraft traffic sharing the link.
    """
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


# Reverse map from 3-char abbr back to a best-guess full label for CoT type
# selection on the GCS side. Not a round-trip — the full label is lost in
# transit, but most dashboards only need the category.
ABBR_TO_LABEL: dict[str, str] = {
    _abbr(label, 3): label for label in LABEL_TO_EMITTER
}


def label_from_abbr(abbr: str) -> str:
    return ABBR_TO_LABEL.get(abbr.upper(), abbr.lower())
