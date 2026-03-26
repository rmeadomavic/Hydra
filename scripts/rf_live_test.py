#!/usr/bin/env python3
"""Live RF signal monitor — verifies Kismet + RTL-SDR see real signals.

Usage:
    # Monitor 433 MHz (default) — plug in a SiK radio or other transmitter
    python scripts/rf_live_test.py

    # Monitor a specific frequency
    python scripts/rf_live_test.py --freq 915.0

    # Wider tolerance for FHSS radios that hop around the band
    python scripts/rf_live_test.py --freq 433.0 --tolerance 5.0

    # Skip Kismet startup (if already running)
    python scripts/rf_live_test.py --no-start
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time

# Add project root to path
sys.path.insert(0, ".")

from hydra_detect.rf.kismet_client import KismetClient
from hydra_detect.rf.kismet_manager import KismetManager
from hydra_detect.rf.signal import RSSIFilter


def main():
    parser = argparse.ArgumentParser(description="Live RF signal monitor")
    parser.add_argument("--freq", type=float, default=433.0,
                        help="Target frequency in MHz (default: 433.0)")
    parser.add_argument("--tolerance", type=float, default=2.0,
                        help="Frequency tolerance in MHz (default: 2.0)")
    parser.add_argument("--no-start", action="store_true",
                        help="Don't start Kismet (assume already running)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Poll interval in seconds (default: 1.0)")
    args = parser.parse_args()

    print(f"=== Hydra RF Live Monitor ===")
    print(f"Target: {args.freq:.1f} MHz (+/- {args.tolerance:.1f} MHz)")
    print()

    mgr = None
    if not args.no_start:
        print("[1/3] Starting Kismet with RTL-SDR...")
        mgr = KismetManager(
            source="rtl433-0",
            capture_dir="./output_data/kismet",
            host="http://localhost:2501",
            user="kismet",
            password="kismet",
            log_dir="./output_data/logs",
        )
        if not mgr.start(timeout_sec=20.0):
            print("FAILED — Kismet could not start. Check:")
            print("  - Is the RTL-SDR plugged in? (lsusb | grep 0bda:2838)")
            print("  - Is another Kismet running? (pgrep kismet)")
            print("  - Check output_data/logs/kismet.log")
            sys.exit(1)
        if mgr.we_own_process:
            print(f"  Kismet started (PID {mgr.pid})")
        else:
            print(f"  Adopted existing Kismet instance")
    else:
        print("[1/3] Skipping Kismet start (--no-start)")

    print("[2/3] Connecting to Kismet API...")
    client = KismetClient(
        host="http://localhost:2501",
        user="kismet",
        password="kismet",
        timeout=5.0,
    )
    if not client.check_connection():
        print("FAILED — Cannot connect to Kismet API")
        if mgr:
            mgr.stop()
        sys.exit(1)
    print("  Connected and authenticated")

    print("[3/3] Monitoring... (Ctrl+C to stop)")
    print()
    print("  Power on your 433 MHz SiK radio now.")
    print("  You should see devices and RSSI readings appear below.")
    print()
    print(f"{'Time':>8}  {'Devices':>7}  {'Target RSSI':>12}  {'Avg RSSI':>9}  {'Trend':>6}  Details")
    print("-" * 80)

    filt = RSSIFilter(window_size=10)
    stop = False

    def on_signal(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)

    start_time = time.monotonic()

    while not stop:
        elapsed = time.monotonic() - start_time

        # Poll target frequency
        rssi = client.get_sdr_rssi(args.freq, tolerance_mhz=args.tolerance)

        # Also get full device list for context
        detail = ""
        try:
            r = client._session.get(
                f"http://localhost:2501/devices/views/all/devices.json",
                timeout=3,
            )
            if r.status_code == 200:
                devs = r.json()
                dev_count = len(devs)
                # Show nearby devices
                nearby = []
                for d in devs:
                    freq = d.get("kismet.device.base.frequency", 0)
                    if freq > 10_000:
                        freq_mhz = freq / 1e6
                    else:
                        freq_mhz = float(freq)
                    sig = d.get("kismet.device.base.signal", {})
                    dev_rssi = sig.get("kismet.common.signal.last_signal")
                    name = d.get("kismet.device.base.commonname", "?")
                    dev_type = d.get("kismet.device.base.type", "?")
                    if dev_rssi and dev_rssi != 0:
                        nearby.append(f"{freq_mhz:.1f}MHz/{dev_rssi}dBm({name})")
                if nearby:
                    detail = " | ".join(nearby[:3])
            else:
                dev_count = "?"
        except Exception:
            dev_count = "err"

        if rssi is not None:
            smoothed = filt.add(rssi)
            trend = filt.trend
            trend_sym = "+" if trend > 1 else ("-" if trend < -1 else "=")
            rssi_str = f"{rssi:.1f} dBm"
            avg_str = f"{smoothed:.1f}"
            trend_str = f"{trend_sym}{abs(trend):.1f}"
        else:
            rssi_str = "---"
            avg_str = f"{filt.average:.1f}" if filt.average > -100 else "---"
            trend_str = "---"

        print(f"{elapsed:7.0f}s  {dev_count:>7}  {rssi_str:>12}  {avg_str:>9}  {trend_str:>6}  {detail}")

        time.sleep(args.interval)

    print()
    print("Stopped.")
    client.close()
    if mgr:
        print("Stopping Kismet...")
        mgr.stop()
        print("Done.")


if __name__ == "__main__":
    main()
