#!/usr/bin/env python3
"""RF Hunt demo — runs the real hunt state machine against live RTL-SDR signals.

Uses rtl_power instead of Kismet so it works with FHSS radios (CRSF, SiK, ELRS).
Simulates vehicle movement based on waypoint commands so you can see the full
IDLE → SEARCHING → HOMING → CONVERGED state machine in action.

Usage:
    # Hunt 915 MHz CRSF (other platforms nearby)
    python scripts/rf_hunt_demo.py --freq 915

    # Hunt 433 MHz SiK radio
    python scripts/rf_hunt_demo.py --freq 433

    # Lower threshold to trigger homing on weaker signals
    python scripts/rf_hunt_demo.py --freq 915 --threshold -5
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

sys.path.insert(0, ".")

from hydra_detect.rf.hunt import HuntState, RFHuntController
from hydra_detect.rf.replay_source import KismetReplaySource
from hydra_detect.rf.rtl_power_client import RtlPowerClient

# ANSI
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
YEL = "\033[93m"
GRN = "\033[92m"
CYN = "\033[96m"
MAG = "\033[95m"
RST = "\033[0m"

STATE_COLORS = {
    "idle": DIM,
    "searching": CYN,
    "homing": YEL,
    "converged": GRN,
    "lost": RED,
    "aborted": DIM,
}


class SimulatedMAVLink:
    """Fake MAVLink that simulates vehicle movement for the hunt demo.

    When the hunt controller sends command_guided_to(), we gradually move
    the simulated GPS position toward the target. This lets the hunt state
    machine exercise its waypoint logic and arrival detection.
    """

    def __init__(self, lat: float = 34.0522, lon: float = -118.2437, alt: float = 15.0):
        self._lat = lat
        self._lon = lon
        self._alt = alt
        self._target_lat = lat
        self._target_lon = lon
        self._target_alt = alt
        self._lock = threading.Lock()
        self._messages: list[str] = []
        self.connected = True

        # Background thread to simulate movement
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._move_loop, daemon=True)
        self._thread.start()

    def get_lat_lon(self) -> tuple[float, float, float]:
        with self._lock:
            return self._lat, self._lon, self._alt

    def command_guided_to(self, lat: float, lon: float, alt: float | None = None) -> bool:
        with self._lock:
            self._target_lat = lat
            self._target_lon = lon
            if alt is not None:
                self._target_alt = alt
        return True

    def send_statustext(self, text: str, severity: int | None = None) -> None:
        with self._lock:
            self._messages.append(text)

    def get_position_string(self) -> str:
        with self._lock:
            return f"{self._lat:.5f},{self._lon:.5f}"

    def get_messages(self) -> list[str]:
        with self._lock:
            msgs = list(self._messages)
            self._messages.clear()
            return msgs

    def stop(self):
        self._stop.set()

    def _move_loop(self):
        """Simulate vehicle moving toward target at ~5 m/s."""
        while not self._stop.wait(0.2):
            with self._lock:
                # Move 1m toward target each tick (5 m/s)
                dlat = self._target_lat - self._lat
                dlon = self._target_lon - self._lon
                # ~111320 m per degree
                dist_m = ((dlat * 111320) ** 2 + (dlon * 111320) ** 2) ** 0.5
                if dist_m < 1.0:
                    self._lat = self._target_lat
                    self._lon = self._target_lon
                else:
                    step = 1.0 / 111320  # ~1 metre in degrees
                    ratio = step / (dist_m / 111320)
                    self._lat += dlat * ratio
                    self._lon += dlon * ratio


def main():
    parser = argparse.ArgumentParser(description="RF Hunt demo with real signals")
    parser.add_argument("--freq", type=float, default=915.0,
                        help="Target frequency in MHz (default: 915)")
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="RSSI threshold to start homing in dB (default: 0)")
    parser.add_argument("--converge", type=float, default=10.0,
                        help="RSSI to declare found in dB (default: 10)")
    parser.add_argument("--pattern", choices=["spiral", "lawnmower"], default="spiral",
                        help="Search pattern (default: spiral)")
    parser.add_argument("--area", type=float, default=50.0,
                        help="Search area in metres (default: 50)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show debug logging")
    parser.add_argument("--replay", type=str, default=None,
                        help="Path to a Kismet JSONL replay fixture. When set, "
                             "the hunt runs against canned data instead of "
                             "RTL-SDR — useful for tabletop demos without "
                             "hardware.")
    parser.add_argument("--mode", choices=["wifi", "sdr"], default=None,
                        help="Hunt mode (default: sdr for --freq, wifi for "
                             "--replay)")
    parser.add_argument("--bssid", type=str, default=None,
                        help="Target BSSID for wifi/replay mode "
                             "(default: AA:BB:CC:DE:AD:01 in replay mode)")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    replay_mode = bool(args.replay)
    default_bssid = "AA:BB:CC:DE:AD:01"
    hunt_mode = args.mode or ("wifi" if replay_mode else "sdr")
    hunt_bssid = args.bssid or (default_bssid if replay_mode else None)

    print(f"{BOLD}=== Hydra RF Hunt Demo ==={RST}")
    if replay_mode:
        print(f"Source: replay fixture {args.replay}")
        if hunt_mode == "wifi":
            print(f"Target: BSSID {hunt_bssid}")
        else:
            print(f"Target: {args.freq:.1f} MHz (SDR mode)")
    else:
        print(f"Source: RTL-SDR live")
        print(f"Target: {args.freq:.0f} MHz")
    print(f"Thresholds: search→homing @ {args.threshold:+.0f} dB, converge @ {args.converge:+.0f} dB")
    print(f"Pattern: {args.pattern}, area: {args.area:.0f}m")
    print()

    # Data source
    if replay_mode:
        print("[1/3] Loading replay fixture...")
        try:
            client = KismetReplaySource(args.replay, loop=True, speed=1.0)
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"  {RED}FAILED{RST} — {exc}")
            sys.exit(1)
        print(
            f"  Replay OK — {client.device_count} devices, "
            f"{client.sample_count} samples, "
            f"{client.duration:.0f}s"
        )
    else:
        print("[1/3] Checking RTL-SDR...")
        client = RtlPowerClient(tolerance_mhz=5.0, step_khz=100.0)
        if not client.check_connection():
            print(f"  {RED}FAILED{RST} — RTL-SDR not available")
            sys.exit(1)
        baseline = client._scan_peak(args.freq)
        if baseline is not None:
            print(f"  RTL-SDR OK — baseline {args.freq:.0f} MHz: {baseline:+.1f} dB")
        else:
            print("  RTL-SDR OK — no baseline reading")
    print()

    # Simulated vehicle
    print("[2/3] Starting simulated vehicle...")
    mav = SimulatedMAVLink()
    lat, lon, alt = mav.get_lat_lon()
    print(f"  Position: {lat:.6f}, {lon:.6f} @ {alt:.0f}m")
    print()

    # Build hunt controller
    print("[3/3] Starting RF hunt...")
    states_seen: list[tuple[float, HuntState]] = []
    t0 = time.monotonic()

    def on_state_change(state: HuntState):
        states_seen.append((time.monotonic() - t0, state))

    # Replay polls cheap canned data (~500µs), so poll faster than rtl_power.
    poll_interval = 0.5 if replay_mode else 2.0
    ctrl = RFHuntController(
        mav,
        mode=hunt_mode,
        target_bssid=hunt_bssid,
        target_freq_mhz=args.freq,
        kismet_host="http://unused",  # won't be used — injected client is used
        search_pattern=args.pattern,
        search_area_m=args.area,
        search_spacing_m=max(args.area / 5, 5.0),
        search_alt_m=15.0,
        rssi_threshold_dbm=args.threshold,
        rssi_converge_dbm=args.converge,
        rssi_window=5,
        gradient_step_m=5.0,
        gradient_rotation_deg=45.0,
        poll_interval_sec=poll_interval,
        arrival_tolerance_m=3.0,
        on_state_change=on_state_change,
        client=client,
    )

    result = ctrl.start()
    if not result:
        print(f"  {RED}Hunt failed to start{RST}")
        mav.stop()
        sys.exit(1)

    print(f"  Hunt running!")
    print()
    print(f"  {DIM}Ctrl+C to stop{RST}")
    print()

    stop = threading.Event()
    def on_signal(sig, frame):
        stop.set()
    signal.signal(signal.SIGINT, on_signal)

    last_state = ""
    try:
        while not stop.is_set():
            status = ctrl.get_status()
            state = status["state"]
            color = STATE_COLORS.get(state, "")

            lat, lon, _ = mav.get_lat_lon()

            line = (
                f"  {color}{BOLD}{state:>11}{RST}  "
                f"RSSI: {status['best_rssi']:>6.1f} dB  "
                f"Samples: {status['samples']:>3}  "
                f"WP: {status['wp_progress']:>6}  "
                f"Pos: ({lat:.5f}, {lon:.5f})"
            )
            print(line)

            # Print MAVLink messages
            for msg in mav.get_messages():
                print(f"           {MAG}GCS: {msg}{RST}")

            if state != last_state:
                last_state = state

            if state in ("converged", "aborted"):
                break

            stop.wait(2.5)
    finally:
        if ctrl.state not in (HuntState.CONVERGED, HuntState.ABORTED):
            ctrl.stop()
        mav.stop()

    print()
    print(f"{BOLD}=== Hunt Complete ==={RST}")
    print(f"Final state: {STATE_COLORS.get(ctrl.state.value, '')}{ctrl.state.value}{RST}")
    print(f"Best RSSI: {ctrl.best_rssi:+.1f} dB")
    blat, blon = ctrl.best_position
    if blat != 0:
        print(f"Best position: ({blat:.6f}, {blon:.6f})")
    print(f"Samples collected: {ctrl.sample_count}")
    print()
    print(f"State transitions:")
    for t, s in states_seen:
        print(f"  {t:6.1f}s  → {STATE_COLORS.get(s.value, '')}{s.value}{RST}")


if __name__ == "__main__":
    main()
