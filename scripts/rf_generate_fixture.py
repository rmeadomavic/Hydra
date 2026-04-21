#!/usr/bin/env python3
"""Generate a deterministic JSONL fixture for the RF hunt demo.

Produces a 2-minute scene with ambient WiFi + BLE + one SDR emitter plus a
target device whose RSSI climbs over the window so the hunt converges at
t ~= 95 s. Seeded RNG makes the output reproducible across runs.

Usage:
    python scripts/rf_generate_fixture.py [--out PATH] [--seed N] \\
        [--duration SEC] [--rate HZ]

Default output path is ``hydra_detect/rf/fixtures/demo_urban.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / "hydra_detect" / "rf" / "fixtures" / "demo_urban.jsonl"
)


@dataclass
class Device:
    bssid: str
    ssid: str | None
    channel: int | None
    freq_mhz: float | None
    manuf: str | None
    # Callable: (t) -> base rssi before jitter
    center_rssi: float
    climb_to: float | None = None  # None = stationary
    climb_end_t: float = 95.0

    def rssi_at(self, t: float) -> float:
        if self.climb_to is None:
            return self.center_rssi
        # Smooth climb from center_rssi at t=0 to climb_to at climb_end_t,
        # then hold.
        if t >= self.climb_end_t:
            return self.climb_to
        progress = t / self.climb_end_t
        # Use a smoothstep so the climb accelerates in the middle.
        smooth = progress * progress * (3 - 2 * progress)
        return self.center_rssi + (self.climb_to - self.center_rssi) * smooth


AMBIENT_DEVICES = [
    Device("AA:BB:CC:00:00:01", "CAFE-GUEST", 6, 2437.0, "TP-Link", -72.0),
    Device("AA:BB:CC:00:00:02", "HomeNet_5G", 36, 5180.0, "Netgear", -65.0),
    Device("AA:BB:CC:00:00:03", "eduroam", 11, 2462.0, "Cisco", -78.0),
    Device("AA:BB:CC:00:00:04", None, 1, 2412.0, "Ubiquiti", -82.0),  # hidden
    Device("AA:BB:CC:00:00:05", "Printer_5A2F", 6, 2437.0, "HP", -68.0),
    Device("AA:BB:CC:00:00:06", "xfinitywifi", 11, 2462.0, "ARRIS", -75.0),
    Device("AA:BB:CC:00:00:07", "ATT-GUEST", 6, 2437.0, "Nokia", -80.0),
    Device("AA:BB:CC:00:BE:01", "Fitbit-A1B2", 37, 2402.0, "Fitbit", -77.0),
]

TARGET_WIFI = Device(
    bssid="AA:BB:CC:DE:AD:01",
    ssid="TARGET-NODE",
    channel=6,
    freq_mhz=2437.0,
    manuf="Espressif",
    center_rssi=-85.0,
    climb_to=-38.0,
    climb_end_t=95.0,
)

TARGET_SDR = Device(
    bssid="SDR:915.3",
    ssid=None,
    channel=None,
    freq_mhz=915.3,
    manuf="rtl_433",
    center_rssi=-82.0,
    climb_to=-36.0,
    climb_end_t=95.0,
)


def _jitter(rng: random.Random, base: float, spread: float) -> float:
    return base + rng.uniform(-spread, spread)


def generate(duration_sec: float, rate_hz: float, seed: int) -> list[dict]:
    """Generate the fixture rows. Deterministic for a given seed."""
    rng = random.Random(seed)
    step = 1.0 / rate_hz
    ticks = int(math.floor(duration_sec * rate_hz)) + 1
    rows: list[dict] = []

    for i in range(ticks):
        t = round(i * step, 3)
        for dev in AMBIENT_DEVICES:
            rssi = _jitter(rng, dev.center_rssi, 3.0)
            rows.append(_row(t, dev, rssi))
        # Target WiFi — every tick, climbing signal.
        rssi = _jitter(rng, TARGET_WIFI.rssi_at(t), 2.0)
        rows.append(_row(t, TARGET_WIFI, rssi))
        # SDR target — matching climb, lower update rate (every other tick).
        if i % 2 == 0:
            rssi = _jitter(rng, TARGET_SDR.rssi_at(t), 2.0)
            rows.append(_row(t, TARGET_SDR, rssi))

    return rows


def _row(t: float, dev: Device, rssi: float) -> dict:
    return {
        "t": t,
        "bssid": dev.bssid,
        "ssid": dev.ssid,
        "rssi": int(round(rssi)),
        "channel": dev.channel,
        "freq_mhz": dev.freq_mhz,
        "manuf": dev.manuf,
        "lat": None,
        "lon": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output path (default: {DEFAULT_OUT})")
    parser.add_argument("--seed", type=int, default=1337,
                        help="RNG seed for reproducible output")
    parser.add_argument("--duration", type=float, default=120.0,
                        help="fixture duration in seconds (default 120)")
    parser.add_argument("--rate", type=float, default=2.0,
                        help="sample rate in Hz (default 2.0)")
    args = parser.parse_args()

    rows = generate(args.duration, args.rate, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(
        f"Wrote {len(rows)} rows to {args.out} "
        f"(duration={args.duration:.0f}s, devices={len(AMBIENT_DEVICES) + 2})"
    )


if __name__ == "__main__":
    main()
