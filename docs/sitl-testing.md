# SITL Testing Guide

Test Hydra without hardware using ArduPilot Software-in-the-Loop.

## Prerequisites

- Python 3.10+ with Hydra dependencies installed
- ArduPilot SITL (optional — Hydra works with sim GPS even without SITL)
- A test video file (or webcam)

## Quick Start (No SITL, Just Hydra)

The `--sim` flag configures Hydra for simulation:

```bash
# With a test video file
python -m hydra_detect --config config.ini --sim

# With webcam (laptop camera as source)
python -m hydra_detect --config config.ini --sim --camera-source 0
```

This gives you:
- Detection and tracking on video/webcam
- Web dashboard at http://localhost:8080
- Simulated GPS at default training coordinates (configurable)
- TAK output (if enabled)
- No MAVLink connection required

## With ArduPilot SITL

For full autonomous testing (follow mode, strike, waypoints):

### 1. Install SITL

```bash
# Clone ArduPilot
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git
cd ardupilot

# Install dependencies
Tools/environment_install/install-prereqs-ubuntu.sh -y
. ~/.profile

# Build SITL for your vehicle type
./waf configure --board sitl
./waf copter   # or rover, plane
```

### 2. Start SITL

```bash
# ArduCopter (drone)
cd ArduCopter
sim_vehicle.py -v ArduCopter --map --console

# ArduRover (USV or UGV)
cd ArduRover
sim_vehicle.py -v Rover --map --console

# ArduPlane (fixed wing)
cd ArduPlane
sim_vehicle.py -v ArduPlane --map --console
```

SITL listens on UDP port 14550 by default.

### 3. Start Hydra

```bash
python -m hydra_detect --config config.ini --sim --vehicle drone
```

Hydra connects to SITL via MAVLink UDP. You can:
- See detections on the dashboard
- Lock targets and test follow mode
- Test autonomous strike with geofence
- Verify TAK markers on ATAK
- Test abort and RTL behavior

### 4. Connect GCS

Mission Planner or QGroundControl can connect to SITL simultaneously:
- SITL forwards to UDP 14551 for a second GCS
- Or connect to TCP 5760

## Multi-Vehicle SITL

Test 5-team CULEX scenarios:

```bash
# Terminal 1: Vehicle 1 (drone)
sim_vehicle.py -v ArduCopter --instance 0 --sysid 1

# Terminal 2: Vehicle 2 (rover/USV)
sim_vehicle.py -v Rover --instance 1 --sysid 2

# Terminal 3: Vehicle 3 (rover/UGV)
sim_vehicle.py -v Rover --instance 2 --sysid 3
```

Each instance uses a different port: 14550, 14560, 14570.

```bash
# Hydra instance 1
python -m hydra_detect --sim --vehicle drone --config config-drone.ini

# Hydra instance 2
python -m hydra_detect --sim --vehicle usv --config config-usv.ini
```

## Test Scenarios

### Follow Mode
1. Start SITL ArduRover
2. Start Hydra with `--sim --vehicle ugv`
3. Open dashboard, point webcam at a person
4. Lock the track, click Follow
5. In SITL console, verify `GUIDED` mode and waypoint updates

### Strike Safety Gates
1. Start Hydra with `--sim --vehicle usv`
2. Set geofence in config: small radius around SITL home
3. Verify strike is rejected outside geofence
4. Verify config freeze during active engagement

### Camera Loss Recovery
1. Start with webcam: `--sim --camera-source 0`
2. Unplug webcam during detection
3. Verify CAM LOST alert and degraded mode
4. Replug — verify recovery

### Multi-Instance Identity
1. Start two Hydra instances with same callsign
2. Verify duplicate callsign warning
3. Start with different callsigns, test TAK command routing

## Config Overrides for SITL

The `--sim` flag sets these defaults (override in config.ini):

| Setting | SITL Default | Normal Default |
|---------|-------------|----------------|
| camera.source_type | file | auto |
| camera.source | sim_video.mp4 | auto |
| mavlink.connection_string | udp:127.0.0.1:14550 | /dev/ttyTHS1 |
| mavlink.baud | 115200 | 921600 |
| mavlink.sim_gps_lat | 35.0527 | (none) |
| mavlink.sim_gps_lon | -79.4927 | (none) |
| osd.enabled | false | false |
| servo_tracking.enabled | false | false |
| rf_homing.enabled | false | false |

## Windows / Mission Planner SITL (Recommended for SORCC)

The easiest way to run SITL on a Windows laptop and connect to Hydra on the
Jetson over Tailscale.

### 1. Start SITL in Mission Planner

1. Open Mission Planner
2. Go to **Simulation** tab (bottom-left)
3. Select vehicle type: **Multirotor** (drone), **Rover** (UGV/USV), or **Plane**
4. Click **Start SITL** — MP downloads and runs the SITL binary automatically

### 2. Forward to Jetson over Tailscale

In the Mission Planner **SITL** window or MAVProxy console:

```
output add <JETSON_TAILSCALE_IP>:14550
```

Example with Jetson at `100.109.160.122`:
```
output add 100.109.160.122:14550
```

### 3. Configure Hydra on the Jetson

Edit `config.ini` (or use `--sim` flag which sets UDP automatically):

```ini
[mavlink]
enabled = true
port = udp:0.0.0.0:14550
baud = 115200
```

Start Hydra in Docker:
```bash
sudo systemctl restart hydra-detect
```

### 4. Verify Connection

```bash
curl -s http://localhost:8080/api/stats | python3 -c "
import sys, json
s = json.load(sys.stdin)
print(f'MAVLink: {s[\"mavlink\"]} | Mode: {s[\"vehicle_mode\"]} | GPS: {s[\"gps_fix\"]}')
"
```

Expected: `MAVLink: True | Mode: STABILIZE | GPS: 3`

### 5. Test from the Dashboard

Open `http://<JETSON_TAILSCALE_IP>:8080` in your laptop browser.
You should see the SITL vehicle's mode, GPS position, and battery.

### Alternative: WSL2 Command Line

If you prefer `sim_vehicle.py` over Mission Planner:

```bash
# In WSL2 Ubuntu terminal
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git
cd ardupilot
Tools/environment_install/install-prereqs-ubuntu.sh -y
. ~/.profile

# Start with output to Jetson
cd ArduCopter
sim_vehicle.py -v ArduCopter --out=udp:100.109.160.122:14550
```

### Firewall Notes

- Windows Firewall may block SITL's outbound UDP. Allow `sim_vehicle.exe` or
  `ArduCopter.exe` through Windows Firewall if connection fails.
- Tailscale handles routing — no port forwarding needed on your router.
- If using WSL2, the UDP output goes through the WSL network bridge. Use the
  WSL2 host's Tailscale IP, not the WSL internal IP.

## Troubleshooting

**No MAVLink connection:** Check SITL is running and listening on 14550. Try `mavproxy.py --master=udp:127.0.0.1:14550` to verify.

**No video:** Place a test video at `sim_video.mp4` in the project root, or use `--camera-source 0` for webcam.

**GPS timeout:** If not running SITL, sim_gps provides fake coordinates. If running SITL, GPS fix takes ~10 seconds after SITL boot.
