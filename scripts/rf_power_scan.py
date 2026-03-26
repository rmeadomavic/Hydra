#!/usr/bin/env python3
"""Real-time RF power scanner using RTL-SDR — works with FHSS radios.

Unlike rtl_433/Kismet which decode specific protocols, this measures
raw signal power across a frequency band.  Works with SiK, CRSF, ELRS,
and any other radio that emits energy on a frequency.

Usage:
    # Scan 915 MHz CRSF band (default)
    python scripts/rf_power_scan.py

    # Scan 433 MHz SiK band
    python scripts/rf_power_scan.py --freq 433

    # Custom range
    python scripts/rf_power_scan.py --start 900 --stop 930
"""

from __future__ import annotations

import argparse
import subprocess
import signal
import sys
import time

# ANSI colors
RED = "\033[91m"
YEL = "\033[93m"
GRN = "\033[92m"
CYN = "\033[96m"
RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Presets for common drone radios
PRESETS = {
    "915":  {"start": 900, "stop": 930, "label": "915 MHz ISM (CRSF/ELRS/SiK)"},
    "433":  {"start": 430, "stop": 440, "label": "433 MHz ISM (SiK/ELRS)"},
    "2400": {"start": 2400, "stop": 2500, "label": "2.4 GHz ISM (ELRS/WiFi)"},
    "868":  {"start": 863, "stop": 870, "label": "868 MHz EU ISM"},
}


def power_bar(db: float, floor: float = -20.0, ceiling: float = 20.0, width: int = 40) -> str:
    """Render a power level as a colored bar."""
    clamped = max(floor, min(db, ceiling))
    ratio = (clamped - floor) / (ceiling - floor)
    filled = int(ratio * width)
    if db > 5:
        color = RED
    elif db > -5:
        color = YEL
    else:
        color = GRN
    return f"{color}{'█' * filled}{DIM}{'░' * (width - filled)}{RST}"


def scan_once(start_mhz: float, stop_mhz: float, step_khz: float = 100) -> list[tuple[float, float]]:
    """Run one rtl_power sweep and return [(freq_mhz, power_db), ...]."""
    cmd = [
        "rtl_power",
        "-f", f"{start_mhz}M:{stop_mhz}M:{step_khz}k",
        "-1",  # single sweep
        "-",   # stdout
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
    except FileNotFoundError:
        print("rtl_power not found — install rtl-sdr package")
        sys.exit(1)

    samples = []
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            # Format: date, time, freq_start_hz, freq_stop_hz, step_hz, samples, db1, db2, ...
            if len(parts) < 7:
                continue
            try:
                freq_start = float(parts[2].strip())
                freq_step = float(parts[4].strip())
                db_values = [float(x.strip()) for x in parts[6:]]
                for i, db in enumerate(db_values):
                    freq_hz = freq_start + i * freq_step
                    samples.append((freq_hz / 1e6, db))
            except (ValueError, IndexError):
                continue
    finally:
        proc.terminate()
        proc.wait(timeout=3)

    return samples


def main():
    parser = argparse.ArgumentParser(description="Real-time RF power scanner")
    parser.add_argument("--freq", choices=list(PRESETS.keys()),
                        help="Preset frequency band")
    parser.add_argument("--start", type=float, help="Start frequency (MHz)")
    parser.add_argument("--stop", type=float, help="Stop frequency (MHz)")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="Seconds between sweeps (default: 0.5)")
    args = parser.parse_args()

    # Default to 915 MHz CRSF band
    if args.start and args.stop:
        start, stop = args.start, args.stop
        label = f"{start:.0f}-{stop:.0f} MHz"
    elif args.freq:
        p = PRESETS[args.freq]
        start, stop, label = p["start"], p["stop"], p["label"]
    else:
        p = PRESETS["915"]
        start, stop, label = p["start"], p["stop"], p["label"]

    # Make sure nothing else is using the dongle
    subprocess.run(["pkill", "-9", "kismet"], capture_output=True)
    subprocess.run(["pkill", "-9", "rtl_433"], capture_output=True)
    subprocess.run(["pkill", "-9", "rtl_power"], capture_output=True)
    time.sleep(0.5)

    print(f"{BOLD}=== Hydra RF Power Scanner ==={RST}")
    print(f"Band: {label}")
    print(f"Range: {start:.0f} - {stop:.0f} MHz")
    print(f"Press Ctrl+C to stop")
    print()

    running = True
    def on_signal(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, on_signal)

    peak_freq = 0.0
    peak_power = -100.0
    sweep_count = 0

    while running:
        samples = scan_once(start, stop)
        if not samples:
            print("  (no data — is RTL-SDR plugged in?)")
            time.sleep(1)
            continue

        sweep_count += 1

        # Find peak
        best_freq, best_db = max(samples, key=lambda x: x[1])
        avg_db = sum(db for _, db in samples) / len(samples)

        if best_db > peak_power:
            peak_power = best_db
            peak_freq = best_freq

        # Bin into ~10 display bands
        band_width = (stop - start) / 10
        bins: dict[int, list[float]] = {}
        for freq, db in samples:
            b = int((freq - start) / band_width)
            bins.setdefault(b, []).append(db)

        # Clear and redraw
        sys.stdout.write(f"\033[2J\033[H")  # clear screen
        print(f"{BOLD}=== Hydra RF Power Scanner — Sweep #{sweep_count} ==={RST}")
        print(f"Band: {label}  |  {len(samples)} bins  |  Avg: {avg_db:.1f} dB")
        print(f"Peak: {CYN}{best_db:+.1f} dB{RST} @ {best_freq:.2f} MHz")
        print(f"Session peak: {CYN}{peak_power:+.1f} dB{RST} @ {peak_freq:.2f} MHz")
        print()
        print(f"  {'Freq (MHz)':>12}  {'Power':>8}  {'':40}  Signal")
        print(f"  {'─' * 12}  {'─' * 8}  {'─' * 40}  {'─' * 6}")

        for b in sorted(bins.keys()):
            if b < 0 or b >= 10:
                continue
            band_start = start + b * band_width
            band_end = band_start + band_width
            band_max = max(bins[b])
            band_avg = sum(bins[b]) / len(bins[b])
            bar = power_bar(band_max)
            is_peak = (abs(best_freq - (band_start + band_width / 2)) < band_width)
            marker = f" {RED}◄ PEAK{RST}" if is_peak else ""
            print(f"  {band_start:6.1f}-{band_end:.1f}  {band_max:+6.1f}  {bar}  {marker}")

        print()
        if best_db > 5:
            print(f"  {RED}{BOLD}■ STRONG SIGNAL DETECTED{RST} — {best_freq:.2f} MHz @ {best_db:+.1f} dB")
        elif best_db > -5:
            print(f"  {YEL}■ Weak signal{RST} — {best_freq:.2f} MHz @ {best_db:+.1f} dB")
        else:
            print(f"  {DIM}■ Noise floor only{RST}")

        time.sleep(args.interval)

    print(f"\n{BOLD}Final results:{RST}")
    print(f"  Sweeps: {sweep_count}")
    print(f"  Peak: {peak_power:+.1f} dB @ {peak_freq:.2f} MHz")


if __name__ == "__main__":
    main()
