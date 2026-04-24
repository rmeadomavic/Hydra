# Hydra Detect — Jetson Orin Nano Setup Guide (Docker)

This guide walks through installing Hydra Detect from GitHub onto a fresh
NVIDIA Jetson Orin Nano using Docker. Written for operators and maintainers
to reproduce the setup.

For initial JetPack flashing and OS setup, see
[Jetson Flash Guide](setup/jetson-flash.mdx) first.

> **Prefer the automated path?** Run `bash scripts/hydra-setup.sh` instead —
> it handles everything below in one interactive script. See the
> [Tailscale SSH guide](tailscale-ssh.md) for remote access setup.

## Prerequisites

- NVIDIA Jetson Orin Nano (8 GB recommended)
- JetPack 6.x / L4T R36.4.x flashed and booted
- Internet connection
- GitHub access to the Hydra repo
- USB camera (or RTSP/file source configured in `config.ini`)

## 1. Verify Your Jetson Environment

```bash
# Check L4T version
cat /etc/nv_tegra_release
# Expected: R36 (release), REVISION: 4.x

# Check Docker is installed and your user is in the docker group
docker --version
groups | grep docker

# If not in docker group:
sudo usermod -aG docker $USER

# Allow serial access to the flight controller (MAVLink over USB):
sudo usermod -aG dialout $USER
# Then log out and back in (or run: newgrp docker)

# Check NVIDIA container runtime is installed
dpkg -l | grep nvidia-container-toolkit
```

## 2. Clone the Hydra Repo

```bash
cd ~
git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra
```

## 3. Pull the Base Image

Hydra's Dockerfile uses `dustynv/l4t-pytorch:r36.4.0` as its base image.
This is a ~6 GB download from Docker Hub that includes PyTorch, CUDA-enabled
OpenCV, and TensorRT — everything the detector needs.

```bash
docker pull dustynv/l4t-pytorch:r36.4.0
```

This will take several minutes depending on your internet speed.

> **Note:** The r36.4.0 image works on R36.4.7 (JetPack 6.2.1) hosts
> without issues.

## 4. Build the Hydra Detect Image

```bash
cd ~/Hydra
docker build --network=host -t hydra-detect:latest .
```

Use `--network=host` to ensure DNS resolution works during the build.
The build takes about 2 minutes.

> **What the Dockerfile handles automatically:**
>
> The l4t-pytorch base image already includes opencv-contrib-python (CUDA),
> numpy 1.x, and PyTorch. The Dockerfile:
>
> 1. **Overrides `PIP_INDEX_URL`** — the base image sets it to
>    `pypi.jetson-ai-lab.dev` (via env var), which can't resolve DNS during
>    Docker build. The Dockerfile resets it to `pypi.org`.
> 2. **Installs ultralytics/supervision with `--no-deps`** — both packages
>    depend on `opencv-python`, which would overwrite the base image's
>    CUDA-enabled `opencv-contrib-python` and break `cv2` imports.
> 3. **Pins numpy to <2** — the base image's OpenCV was compiled against
>    numpy 1.x. Letting pip upgrade to numpy 2.x causes
>    `_ARRAY_API not found` crashes.
>
> You will see pip warnings about `opencv-python` not being installed —
> this is expected and safe to ignore. The CUDA-enabled OpenCV from the
> base image is what Hydra uses.

## 5. Configure

Edit `config.ini` before running. Key settings to verify:

```bash
nano config.ini
```

| Setting | Section | Notes |
|---------|---------|-------|
| `source` | `[camera]` | `0` for /dev/video0, RTSP URL, or file path |
| `connection_string` | `[mavlink]` | `/dev/ttyACM0` or UDP endpoint |
| `enabled` | `[mavlink]` | Set `false` if no flight controller connected |
| `enabled` | `[rtsp]` | RTSP video stream for Mission Planner (on by default) |

> **Tip:** YOLO (yolov8n) downloads its model automatically on first run
> (~6 MB). No manual model setup required.

## 6. Run Hydra Detect

**Important:** Always use `--runtime nvidia` — without it, OpenCV will
crash with `libwayland-cursor.so.0: file too short` because the container
needs the host's NVIDIA libraries mounted in.

### Quick test run (no MAVLink)

```bash
docker run --rm --privileged --runtime nvidia \
  -v $(pwd)/config.ini:/app/config.ini:ro \
  -v /usr/sbin/nvpmodel:/usr/sbin/nvpmodel:ro \
  -v /usr/bin/jetson_clocks:/usr/bin/jetson_clocks:ro \
  -v /etc/nvpmodel.conf:/etc/nvpmodel.conf:ro \
  -v /etc/nvpmodel:/etc/nvpmodel:ro \
  -v /var/lib/nvpmodel:/var/lib/nvpmodel \
  -v $(pwd)/models:/models \
  -p 8080:8080 \
  -p 8554:8554 \
  hydra-detect:latest
```

### Full run with MAVLink and persistent data

```bash
docker run --rm --privileged --runtime nvidia \
  -v $(pwd)/config.ini:/app/config.ini:ro \
  -v /usr/sbin/nvpmodel:/usr/sbin/nvpmodel:ro \
  -v /usr/bin/jetson_clocks:/usr/bin/jetson_clocks:ro \
  -v /etc/nvpmodel.conf:/etc/nvpmodel.conf:ro \
  -v /etc/nvpmodel:/etc/nvpmodel:ro \
  -v /var/lib/nvpmodel:/var/lib/nvpmodel \
  -v $(pwd)/models:/models \
  -v $(pwd)/output_data:/data \
  -p 8080:8080 \
  --name hydra-detect \
  hydra-detect:latest
```

> **Volume mount explanation:**
> - `--privileged` grants access to all `/dev` devices (cameras, serial ports) so
>   individual `--device` flags are not needed
> - `config.ini` — mount your local config so changes don't require a rebuild
> - `nvpmodel` / `jetson_clocks` — lets the dashboard control Jetson power modes
> - `models/` — drop YOLO `.pt` files here to switch models from the dashboard
> - `output_data/` — detection logs and image snapshots persist outside the container

### Access the web dashboard

Open a browser to `http://<jetson-ip>:8080`

You should see a live camera feed with detection bounding boxes overlaid.

### RTSP video stream for Mission Planner

Hydra publishes annotated detection frames (with bounding boxes, FPS, and
lock indicators) as an H.264 RTSP stream. This is enabled by default.

**In Mission Planner:**

1. Right-click the HUD (the main attitude/video display)
2. Select **Video > Set GStreamer Source**
3. Paste:
   ```
   rtspsrc location=rtsp://<jetson-ip>:8554/hydra latency=0 ! decodebin ! videoconvert ! autovideosink
   ```
4. The annotated detection feed appears in the HUD

**Test with VLC or ffplay:**

```bash
ffplay rtsp://<jetson-ip>:8554/hydra
# or
vlc rtsp://<jetson-ip>:8554/hydra
```

The RTSP stream can be toggled on/off from the web dashboard (System panel)
or via the API (`POST /api/rtsp/toggle`). Config lives in the `[rtsp]`
section of `config.ini`.

### Expected startup log

A healthy startup looks like this:

```
=== Hydra Detect v2.0 starting ===
Loading YOLO model: yolov8n.pt
YOLO model loaded.
Detector engine: yolo
ByteTrack initialised (supervision back-end).
Camera opened: 0 (640x480 @ 30 fps)
Web UI started at http://0.0.0.0:8080
```

You will also see some Argus/GStreamer warnings — these are harmless
(OpenCV tries CSI camera first, then falls back to USB).

## 7. Run as a System Service (Optional)

To start Hydra Detect automatically on boot:

```bash
sudo cp scripts/hydra-detect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hydra-detect
sudo systemctl start hydra-detect

# Check status
sudo systemctl status hydra-detect
sudo journalctl -u hydra-detect -f
```

## 8. Verify with Preflight Check

```bash
# Run inside the repo directory
bash scripts/jetson_preflight.sh
```

## Troubleshooting

### Docker permission denied
```
Got permission denied while trying to connect to the Docker daemon socket
```
**Fix:** `sudo usermod -aG docker $USER` then log out/in.

### MAVLink permission denied on serial port
```
MAVLink connection failed: [Errno 13] Permission denied: '/dev/ttyACM0'
```
**Fix:** Your user needs to be in the `dialout` group:
```bash
sudo usermod -aG dialout $USER
```
Then log out and back in. The preflight script (`scripts/jetson_preflight.sh`) checks for this.

### NVIDIA runtime not found
```
docker: Error response from daemon: unknown or invalid runtime name: nvidia
```
**Fix:** Install NVIDIA Container Toolkit:
```bash
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

### OpenCV crash: libwayland-cursor.so.0 file too short
```
ImportError: /usr/lib/aarch64-linux-gnu/nvidia/libwayland-cursor.so.0: file too short
```
**Fix:** You forgot `--runtime nvidia` in your `docker run` command. The
NVIDIA container runtime mounts the correct host GPU libraries into the
container. Without it, stale library stubs inside the image cause crashes.

### CUDA out of memory
```
NVML_SUCCESS == r INTERNAL ASSERT FAILED at CUDACachingAllocator.cpp
```
**Fix:** Close other GPU-using applications. Adding swap can help:
```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# Make permanent:
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### numpy 2.x / _ARRAY_API error
```
A module that was compiled using NumPy 1.x cannot be run in NumPy 2.x
```
**Fix:** Already handled in the Dockerfile. If you see this after modifying
`requirements.txt`, make sure numpy is pinned to `<2.0`.

### pip DNS failure during build
```
Failed to establish a new connection: Name or service not known
```
for `pypi.jetson-ai-lab.dev` — **Fix:** Already handled in the Dockerfile
(`PIP_INDEX_URL` override). If you see this, make sure you're using the
current Dockerfile and building with `--network=host`.

### Port already allocated
```
Bind for 0.0.0.0:8080 failed: port is already allocated
```
**Fix:** A previous container is still running. Stop it:
```bash
docker kill $(docker ps -q --filter "publish=8080")
```

### Camera not detected
```bash
ls -la /dev/video*
# If empty, check USB connection or CSI ribbon cable
v4l2-ctl --list-devices
```

### Argus/GStreamer warnings (safe to ignore)
```
(Argus) Error FileOperationFailed: Connecting to nvargus-daemon failed
GStreamer: pipeline have not been created
```
These appear because OpenCV tries CSI camera access first, then falls
back to USB V4L2. They do not affect camera capture.

### NvMap errors (safe to ignore)
```
NvMapMemAllocInternalTagged: 1075072515 error 12
```
These are CUDA memory allocator messages on Jetson, not fatal errors.
They appear during GPU initialization and are normal.

---

## Summary of Issues Encountered During Setup

This guide was built by doing the actual install and documenting every
problem. Here's the full list of issues hit and resolved:

| # | Issue | Root Cause | Resolution |
|---|-------|-----------|------------|
| 1 | pip DNS failure during Docker build | Base image sets `PIP_INDEX_URL` env var to unreachable `pypi.jetson-ai-lab.dev` | Override `PIP_INDEX_URL` in Dockerfile |
| 2 | opencv-python-headless conflicts | Base image has `opencv-contrib-python`; pip's `opencv-python-headless` overwrites it | Filter out opencv from requirements |
| 3 | numpy 2.x breaks OpenCV | pip upgrades numpy to 2.x but OpenCV was compiled against 1.x | Pin `numpy<2.0` in Dockerfile |
| 4 | ultralytics pulls in opencv-python | Transitive dependency overwrites base image's CUDA OpenCV | Install ultralytics/supervision with `--no-deps` |
| 5 | cv2 crashes without `--runtime nvidia` | Container needs host NVIDIA libs mounted | Always use `--runtime nvidia` |
| 6 | supervision import fails (no matplotlib) | supervision requires matplotlib at import time | Added matplotlib to Dockerfile pip install |

---

*Guide tested on Jetson Orin Nano Super 8GB, JetPack 6.2.1, L4T R36.4.7, 2026-03-14*
