#!/usr/bin/env python3
"""Hydra spectrum daemon — feed live RTL-SDR sweeps to the dashboard.

Runs rtl_power in a loop, computes a noise floor and peak set, and
writes the result as JSON to a file the web server reads. Intended to
be run under systemd (see scripts/hydra-spectrum.service) but works
fine standalone for ad-hoc testing.

Output schema (matches what the dashboard rtl-spectrum-overlay.js
expects):
    {
        "freq_low_mhz": 2400,
        "freq_high_mhz": 2500,
        "noise_floor_dbm": -42.7,
        "threshold_dbm": -32.7,
        "bins": [[2400.0, -45.1], ...],
        "peaks": [{"freq_mhz": 2462.0, "dbm": -28.4}, ...],
        "sweep_count": 12,
        "status": "ok"
    }

Usage:
    python3 scripts/hydra_spectrum_daemon.py
    python3 scripts/hydra_spectrum_daemon.py --start 900 --stop 930
    python3 scripts/hydra_spectrum_daemon.py --output /tmp/hydra_spectrum.json --interval 1.0
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rf_power_scan import scan_once  # noqa: E402


PRESETS = {
    "915":  {"start": 900,  "stop": 930},
    "433":  {"start": 430,  "stop": 440},
    "2400": {"start": 2400, "stop": 2500},
    "868":  {"start": 863,  "stop": 870},
}

DEFAULT_OUTPUT = "/tmp/hydra_spectrum.json"


def find_peaks(samples, threshold_dbm, max_peaks=12, min_separation_mhz=1.0):
    """Return top-N peaks above threshold, separated by min_separation_mhz."""
    above = [(f, db) for (f, db) in samples if db >= threshold_dbm]
    above.sort(key=lambda x: x[1], reverse=True)
    chosen: list[tuple[float, float]] = []
    for freq, db in above:
        if all(abs(freq - f) >= min_separation_mhz for f, _ in chosen):
            chosen.append((freq, db))
            if len(chosen) >= max_peaks:
                break
    chosen.sort(key=lambda x: x[0])
    return [{"freq_mhz": round(f, 4), "dbm": round(db, 2)} for f, db in chosen]


def write_json_atomic(path: Path, payload: dict) -> None:
    """Tempfile + rename so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_payload(start, stop, samples, sweep_count, threshold_delta_db,
                  status="ok", error=None):
    if not samples:
        return {
            "freq_low_mhz": start,
            "freq_high_mhz": stop,
            "noise_floor_dbm": None,
            "threshold_dbm": None,
            "bins": [],
            "peaks": [],
            "sweep_count": sweep_count,
            "status": status,
            "error": error,
        }
    db_values = [db for _, db in samples]
    nf = statistics.median(db_values)
    threshold = nf + threshold_delta_db
    bins = [[round(f, 4), round(db, 2)] for f, db in samples]
    peaks = find_peaks(samples, threshold)
    return {
        "freq_low_mhz": start,
        "freq_high_mhz": stop,
        "noise_floor_dbm": round(nf, 2),
        "threshold_dbm": round(threshold, 2),
        "bins": bins,
        "peaks": peaks,
        "sweep_count": sweep_count,
        "status": status,
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Hydra spectrum daemon (rtl_power -> JSON)")
    parser.add_argument("--freq", choices=list(PRESETS.keys()),
                        help="Preset frequency band (default: 2400)")
    parser.add_argument("--start", type=float, help="Start frequency (MHz)")
    parser.add_argument("--stop", type=float, help="Stop frequency (MHz)")
    parser.add_argument("--step-khz", type=float, default=100.0,
                        help="Bin width in kHz (default: 100)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between sweeps (default: 1.0)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"Output JSON path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--threshold-delta-db", type=float, default=10.0,
                        help="Peak threshold above noise floor in dB (default: 10)")
    parser.add_argument("--retry-sleep", type=float, default=5.0,
                        help="Sleep on no-data / error before retry (default: 5s)")
    args = parser.parse_args()

    if args.start is not None and args.stop is not None:
        start, stop = args.start, args.stop
    else:
        preset = PRESETS[args.freq or "2400"]
        start, stop = preset["start"], preset["stop"]

    if stop <= start:
        print("--stop must be greater than --start", file=sys.stderr)
        sys.exit(2)

    output = Path(args.output)
    print(f"hydra-spectrum: {start}-{stop} MHz step={args.step_khz}kHz "
          f"output={output}", flush=True)

    running = True

    def on_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    sweep_count = 0
    while running:
        loop_start = time.monotonic()
        status = "ok"
        error = None
        samples = []
        try:
            samples = scan_once(start, stop, step_khz=args.step_khz)
        except SystemExit:
            status, error = "error", "rtl_power binary not found"
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"

        if status == "ok" and not samples:
            status = "no_sdr"
            error = "rtl_power produced no samples (dongle unplugged or busy?)"

        if samples:
            sweep_count += 1

        payload = build_payload(start, stop, samples, sweep_count,
                                args.threshold_delta_db,
                                status=status, error=error)
        try:
            write_json_atomic(output, payload)
        except Exception as exc:
            print(f"hydra-spectrum: failed to write {output}: {exc}",
                  file=sys.stderr, flush=True)

        if status != "ok":
            time.sleep(args.retry_sleep)
            continue

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, args.interval - elapsed))

    print("hydra-spectrum: shutting down", flush=True)


if __name__ == "__main__":
    main()
