#!/usr/bin/env python3
"""Ground-station side of the Hydra MAVLink → CoT relay.

When the Jetson is out of WiFi/LAN range from the TAK server (e.g. on a boat
running over telemetry radio), it packs detections into ADSB_VEHICLE MAVLink
frames. ArduPilot auto-forwards those to every MAVLink serial port, so they
reach the ground-station laptop over the same telemetry radio carrying
vehicle telemetry.

This tool:
    * Connects to a MAVLink stream (typically a tee'd Mission Planner port,
      or ``mavlink-routerd`` output).
    * Filters ADSB_VEHICLE frames with Hydra-shaped callsigns (``H`` prefix).
    * Rebuilds a CoT detection marker — UID byte-compatible with the Jetson's
      direct path, so ATAK de-duplicates naturally when both paths are active.
    * Publishes to a TAK multicast group and any unicast targets.

Dependencies: pymavlink (``pip install pymavlink``). Standard library only
otherwise.

Example:
    python tools/hydra_relay.py \\
        --mavlink udp:127.0.0.1:14551 \\
        --tak-multicast 239.2.3.1:6969 \\
        --callsign HYDRA-1

Before running:
    * On the Pixhawk, set ``SR*_ADSB`` > 0 on the telemetry port feeding the
      GCS (usually ``SR1_ADSB`` or ``SR2_ADSB``). Default is often 0.
    * In Mission Planner, tee the MAVLink stream to a local UDP port so this
      tool can connect alongside MP.
"""

from __future__ import annotations

import argparse
import logging
import socket
import struct
import sys
import time
from pathlib import Path

# Allow the tool to be launched directly from the repo root *or* as a module.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hydra_relay.codec import HydraTrackEvent, decode_adsb_vehicle  # noqa: E402
from hydra_relay.cot import build_detection_marker, get_cot_type  # noqa: E402

logger = logging.getLogger("hydra_relay")


def _parse_host_port_list(raw: str) -> list[tuple[str, int]]:
    targets: list[tuple[str, int]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, _, port_str = entry.rpartition(":")
        if not host or not port_str.isdigit():
            logger.warning("Ignoring invalid target: %r", entry)
            continue
        targets.append((host, int(port_str)))
    return targets


class CoTSender:
    """UDP multicast + unicast emitter."""

    def __init__(
        self,
        multicast: tuple[str, int] | None,
        unicast: list[tuple[str, int]],
    ) -> None:
        self._multicast = multicast
        self._unicast = unicast
        self._sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
        )
        self._sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 32),
        )
        self.sent = 0

    def send(self, data: bytes) -> None:
        if self._multicast is not None:
            try:
                self._sock.sendto(data, self._multicast)
                self.sent += 1
            except OSError as exc:
                logger.debug("Multicast send failed: %s", exc)
        for target in self._unicast:
            try:
                self._sock.sendto(data, target)
                self.sent += 1
            except OSError as exc:
                logger.debug("Unicast send to %s failed: %s", target, exc)


def event_to_cot(
    evt: HydraTrackEvent, callsign: str, stale_seconds: float,
) -> bytes:
    """Build a CoT marker for a decoded Hydra track event.

    UID scheme matches ``hydra_detect/tak/tak_output.py`` so ATAK de-duplicates
    when the Jetson is simultaneously publishing directly.
    """
    label = evt.label
    return build_detection_marker(
        uid=f"{callsign}-DET-{evt.track_id}",
        callsign=f"{callsign}-{label}-{evt.track_id}",
        cot_type=get_cot_type(label),
        lat=evt.lat,
        lon=evt.lon,
        hae=evt.hae_m,
        confidence=evt.confidence,
        label=label,
        track_id=evt.track_id,
        stale_seconds=stale_seconds,
    )


def run(args: argparse.Namespace) -> int:
    try:
        from pymavlink import mavutil
    except ImportError:
        logger.error("pymavlink not installed. Run: pip install pymavlink")
        return 2

    multicast = None
    if args.tak_multicast:
        host, _, port = args.tak_multicast.partition(":")
        if not port.isdigit():
            logger.error("Invalid --tak-multicast: %r", args.tak_multicast)
            return 2
        multicast = (host, int(port))

    unicast = _parse_host_port_list(args.tak_unicast) if args.tak_unicast else []

    sender = CoTSender(multicast, unicast)
    logger.info(
        "Hydra relay listening on %s → multicast=%s unicast=%s callsign=%s",
        args.mavlink, multicast, unicast, args.callsign,
    )

    conn = mavutil.mavlink_connection(
        args.mavlink, source_system=args.source_system, dialect="common",
    )

    last_status = time.monotonic()
    received = 0
    relayed = 0

    while True:
        msg = conn.recv_match(type="ADSB_VEHICLE", blocking=True, timeout=1.0)
        now = time.monotonic()
        if msg is not None:
            received += 1
            evt = decode_adsb_vehicle(msg)
            if evt is None:
                # Genuine ADS-B traffic (no Hydra callsign) — ignore quietly.
                continue
            cot_bytes = event_to_cot(evt, args.callsign, args.stale)
            sender.send(cot_bytes)
            relayed += 1
            if args.verbose:
                logger.info(
                    "relay %s #%d @ %.6f,%.6f (conf=%.0f%%, age=%ds, locked=%s)",
                    evt.label, evt.track_id, evt.lat, evt.lon,
                    evt.confidence * 100, evt.age_sec, evt.locked,
                )

        if now - last_status >= args.status_interval:
            logger.info(
                "status: adsb_rx=%d hydra_relayed=%d cot_sent=%d",
                received, relayed, sender.sent,
            )
            last_status = now


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--mavlink", required=True,
        help="MAVLink connection string, e.g. udp:127.0.0.1:14551",
    )
    p.add_argument(
        "--tak-multicast", default="239.2.3.1:6969",
        help="TAK multicast group host:port (default: 239.2.3.1:6969)",
    )
    p.add_argument(
        "--tak-unicast", default="",
        help="Comma-separated list of unicast targets host:port,host:port",
    )
    p.add_argument(
        "--callsign", default="HYDRA-1",
        help="Jetson callsign (must match [tak] callsign on the Jetson)",
    )
    p.add_argument(
        "--stale", type=float, default=60.0,
        help="CoT stale time in seconds (default: 60)",
    )
    p.add_argument(
        "--source-system", type=int, default=254,
        help="MAVLink source system id (default: 254)",
    )
    p.add_argument(
        "--status-interval", type=float, default=10.0,
        help="Seconds between status lines (default: 10)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return run(args) or 0
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
