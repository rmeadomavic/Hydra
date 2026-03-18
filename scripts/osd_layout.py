#!/usr/bin/env python3
"""Configure ArduPilot OSD element positions for HD OSD (HDZero via MSP DisplayPort).

Connects to the Pixhawk via MAVLink and sets OSD1_*_X/Y/EN parameters to create
a clean tactical layout on the 50x18 HD OSD canvas.

Canvas: 50 columns (0-49) x 18 rows (0-17)
OSD1_TXT_RES = 1 (HD)

Layout zones:
  Row 0:  [FLTMODE]                                               [BAT_VOLT CURRENT]
  Row 1:  [SATS RSSI]                                             [BATUSED  POWER]
  Row 2:  [---------- MESSAGE (Hydra detection text) ----------]
  Row 3-15: (clear — camera view)
  Row 8:  [ALTITUDE]                                              [HOMEDIST HOMEDIR]
  Row 16: [GPSLAT]                                                [GSPEED]
  Row 17: [GPSLONG]                                               [HEADING THROTTLE]

Disabled: HORIZON (huge overlay), COMPASS (big bar), VSPEED (ground vehicle)

Usage:
    python3 scripts/osd_layout.py

Falls back to /dev/ttyACM0 if /dev/ttyTHS1 is busy (Hydra may hold UART).
If both fail, prints the parameter commands for manual entry in Mission Planner.
"""

from __future__ import annotations

import sys
import time

# ---------------------------------------------------------------------------
# HD OSD canvas: 50 cols x 18 rows
# ---------------------------------------------------------------------------
COLS = 50
ROWS = 18

# ---------------------------------------------------------------------------
# Desired OSD element layout
#
# Format: parameter_base -> (enabled, col, row)
# ArduPilot params: OSD1_{name}_EN, OSD1_{name}_X, OSD1_{name}_Y
# ---------------------------------------------------------------------------
LAYOUT: dict[str, tuple[int, int, int]] = {
    # --- Top-left: flight mode + status ---
    "FLTMODE":   (1,  0,  0),   # "MANUAL", "AUTO", etc.
    "SATS":      (1,  0,  1),   # GPS satellite count
    "RSSI":      (1,  8,  1),   # Radio signal strength

    # --- Row 2: Hydra message panel (own row, no overlap risk) ---
    # MESSAGE element shows STATUSTEXT — this is where Hydra detections appear.
    # Gets its own row so long detection strings never collide with battery info.
    # Col 0 = full width of canvas available (up to 50 chars).
    "MESSAGE":   (1,  0,  2),

    # --- Top-right: battery cluster ---
    "BAT_VOLT":  (1, 38,  0),   # "12.6V" — 5-6 chars
    "CURRENT":   (1, 44,  0),   # "2.1A"  — 4-5 chars
    "BATUSED":   (1, 38,  1),   # "1234mAh" — 7-8 chars
    "POWER":     (1, 46,  1),   # "25W" — 3-4 chars

    # --- Middle: keep clear, just altitude + home ---
    "ALTITUDE":  (1,  0,  8),   # Left edge, mid-screen
    "HOMEDIST":  (1, 40,  8),   # Distance to home
    "HOMEDIR":   (1, 47,  8),   # Arrow to home

    # --- Bottom-left: GPS coordinates ---
    "GPSLAT":    (1,  0, 16),   # Latitude
    "GPSLONG":   (1,  0, 17),   # Longitude

    # --- Bottom-right: speed + heading ---
    "GSPEED":    (1, 40, 16),   # Ground speed
    "HEADING":   (1, 40, 17),   # Heading degrees
    "THROTTLE":  (1, 47, 17),   # Throttle %

    # --- Disabled elements (too big or not needed for ground vehicle) ---
    "HORIZON":   (0,  0,  0),   # Artificial horizon — huge, blocks camera view
    "COMPASS":   (0,  0,  0),   # Compass bar — wide, overlaps other elements
    "VSPEED":    (0,  0,  0),   # Vertical speed — irrelevant for UGV/USV
}


def print_layout_diagram() -> None:
    """Print an ASCII diagram of the OSD layout for visual verification."""
    # Build a grid
    grid = [["." for _ in range(COLS)] for _ in range(ROWS)]

    # Place elements on the grid
    labels = {
        "FLTMODE":  "MODE",
        "SATS":     "SAT",
        "RSSI":     "RSSI",
        "MESSAGE":  "HYDRA T:3 15fps 22ms LK#2TRK:person",
        "BAT_VOLT": "12.6V",
        "CURRENT":  "2.1A",
        "BATUSED":  "1234mAh",
        "POWER":    "25W",
        "ALTITUDE": "ALT",
        "HOMEDIST": "HDIST",
        "HOMEDIR":  "->",
        "GPSLAT":   "LAT:xx.xxxxx",
        "GPSLONG":  "LON:xx.xxxxx",
        "GSPEED":   "12.3m/s",
        "HEADING":  "HDG:123",
        "THROTTLE": "TH%",
    }

    for name, (en, col, row) in LAYOUT.items():
        if not en:
            continue
        text = labels.get(name, name[:4])
        for i, ch in enumerate(text):
            c = col + i
            if 0 <= c < COLS and 0 <= row < ROWS:
                grid[row][c] = ch

    print("\n  HD OSD Layout (50x18):")
    print("  " + "=" * COLS)
    for r, line in enumerate(grid):
        print(f"  {''.join(line)}  [{r:2d}]")
    print("  " + "=" * COLS)
    print(f"  {''.join(str(c % 10) for c in range(COLS))}")
    print()


def connect_mavlink():
    """Try to connect to the Pixhawk. Returns (connection, description) or (None, None)."""
    from pymavlink import mavutil

    # Try ttyACM0 first (USB, often free even when Hydra holds ttyTHS1)
    ports = [
        ("/dev/ttyACM0", 115200),
        ("/dev/ttyTHS1", 921600),
    ]

    for device, baud in ports:
        print(f"  Trying {device} @ {baud} baud ... ", end="", flush=True)
        try:
            conn = mavutil.mavlink_connection(device, baud=baud)
            conn.wait_heartbeat(timeout=8)
            print(f"connected (sysid={conn.target_system}, comp={conn.target_component})")
            return conn, f"{device}@{baud}"
        except Exception as e:
            print(f"failed ({e})")

    return None, None


def param_get(conn, name: str, timeout: float = 3.0) -> float | None:
    """Read a single parameter value from the FC."""
    conn.mav.param_request_read_send(
        conn.target_system,
        conn.target_component,
        name.encode("utf-8").ljust(16, b"\x00"),
        -1,
    )
    start = time.time()
    while time.time() - start < timeout:
        msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
        if msg is None:
            continue
        received_name = msg.param_id.rstrip("\x00").strip()
        if received_name == name:
            return msg.param_value
    return None


def param_set(conn, name: str, value: float, timeout: float = 3.0) -> bool:
    """Set a parameter on the FC and verify it was accepted."""
    for attempt in range(3):
        conn.mav.param_set_send(
            conn.target_system,
            conn.target_component,
            name.encode("utf-8").ljust(16, b"\x00"),
            value,
            9,  # MAV_PARAM_TYPE_REAL32
        )
        start = time.time()
        while time.time() - start < timeout:
            msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
            if msg is None:
                continue
            received_name = msg.param_id.rstrip("\x00").strip()
            if received_name == name:
                if abs(msg.param_value - value) < 0.5:
                    return True
                else:
                    break  # Value not accepted, retry
        # Small delay before retry
        time.sleep(0.1)
    return False


def read_current_positions(conn) -> dict[str, dict[str, float | None]]:
    """Read current OSD positions for all elements in the layout."""
    results: dict[str, dict[str, float | None]] = {}
    for name in LAYOUT:
        en = param_get(conn, f"OSD1_{name}_EN")
        x = param_get(conn, f"OSD1_{name}_X")
        y = param_get(conn, f"OSD1_{name}_Y")
        results[name] = {"en": en, "x": x, "y": y}
    return results


def apply_layout(conn) -> tuple[int, int]:
    """Apply the new OSD layout. Returns (success_count, fail_count)."""
    ok = 0
    fail = 0
    total = len(LAYOUT) * 3  # EN + X + Y per element

    for name, (en, col, row) in LAYOUT.items():
        params = [
            (f"OSD1_{name}_EN", float(en)),
            (f"OSD1_{name}_X",  float(col)),
            (f"OSD1_{name}_Y",  float(row)),
        ]
        for pname, pval in params:
            if param_set(conn, pname, pval):
                ok += 1
            else:
                print(f"    FAILED: {pname} = {pval}")
                fail += 1

    return ok, fail


def verify_layout(conn) -> tuple[int, int]:
    """Verify that all OSD parameters match the desired layout."""
    match = 0
    mismatch = 0

    for name, (en, col, row) in LAYOUT.items():
        expected = {
            f"OSD1_{name}_EN": float(en),
            f"OSD1_{name}_X":  float(col),
            f"OSD1_{name}_Y":  float(row),
        }
        for pname, pval in expected.items():
            actual = param_get(conn, pname)
            if actual is not None and abs(actual - pval) < 0.5:
                match += 1
            else:
                print(f"    MISMATCH: {pname} expected={pval} actual={actual}")
                mismatch += 1

    return match, mismatch


def print_mission_planner_commands() -> None:
    """Print parameter set commands for manual entry in Mission Planner."""
    print("\n" + "=" * 65)
    print("  MANUAL PARAMETER COMMANDS")
    print("  Copy these into Mission Planner > Config > Full Parameter List")
    print("  or use the MAVProxy 'param set' command.")
    print("=" * 65)

    for name, (en, col, row) in sorted(LAYOUT.items()):
        print(f"  OSD1_{name}_EN  = {en}")
        if en:
            print(f"  OSD1_{name}_X   = {col}")
            print(f"  OSD1_{name}_Y   = {row}")
        else:
            print(f"  OSD1_{name}_X   = 0   (disabled)")
            print(f"  OSD1_{name}_Y   = 0   (disabled)")

    print()
    print("  After setting all parameters, reboot the flight controller.")
    print("=" * 65)


def main() -> None:
    print()
    print("  Hydra Detect — HD OSD Layout Configurator")
    print("  Canvas: 50 cols x 18 rows (OSD1_TXT_RES=1)")
    print()

    # Show the planned layout
    print_layout_diagram()

    # Try to connect
    conn, desc = connect_mavlink()

    if conn is None:
        print("  Could not connect to the flight controller.")
        print("  Printing manual parameter commands instead.")
        print_mission_planner_commands()
        sys.exit(1)

    print(f"\n  Connected via {desc}")
    print()

    # Step 1: Read current positions
    print("  [1/4] Reading current OSD positions ...")
    current = read_current_positions(conn)
    for name, vals in current.items():
        en_str = "ON" if vals["en"] and vals["en"] > 0.5 else "OFF"
        x_str = f"x={int(vals['x'])}" if vals["x"] is not None else "x=?"
        y_str = f"y={int(vals['y'])}" if vals["y"] is not None else "y=?"
        print(f"    {name:12s}  {en_str:3s}  {x_str}  {y_str}")

    # Step 2: Apply new layout
    print(f"\n  [2/4] Applying new layout ({len(LAYOUT)} elements) ...")
    ok, fail = apply_layout(conn)
    print(f"    Set {ok} parameters OK, {fail} failed")

    if fail > 0:
        print("\n  Some parameters failed. Printing manual commands as fallback.")
        print_mission_planner_commands()

    # Step 3: Verify
    print("\n  [3/4] Verifying layout ...")
    match, mismatch = verify_layout(conn)
    print(f"    {match} parameters verified, {mismatch} mismatches")

    # Step 4: Summary
    print("\n  [4/4] Summary")
    print("  " + "-" * 50)

    enabled = [n for n, (e, _, _) in LAYOUT.items() if e]
    disabled = [n for n, (e, _, _) in LAYOUT.items() if not e]

    print(f"    Enabled  ({len(enabled)}): {', '.join(enabled)}")
    print(f"    Disabled ({len(disabled)}): {', '.join(disabled)}")

    if mismatch == 0 and fail == 0:
        print("\n    All parameters set and verified successfully.")
    else:
        print(f"\n    WARNING: {mismatch} mismatches, {fail} set failures.")
        print("    Check the output above and retry or use Mission Planner.")

    print("\n    IMPORTANT: Reboot the flight controller to apply changes.")
    print("    (This script does NOT reboot automatically.)")
    print()

    conn.close()


if __name__ == "__main__":
    main()
