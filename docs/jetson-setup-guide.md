# Hydra Detect — Jetson Orin Nano Setup Guide (Docker)

This guide walks through installing Hydra Detect from GitHub onto a fresh
NVIDIA Jetson Orin Nano using Docker. Written for students and instructors
to reproduce the setup.

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
# Then log out and back in

# Check NVIDIA container runtime is installed
dpkg -l | grep nvidia-container-toolkit
```

## 2. Clone the Hydra Repo

```bash
cd ~
git clone https://github.com/rmeadomavic/Hydra.git
cd Hydra
```

## 3. Get the NanoOWL Base Image

Hydra's Dockerfile uses `nanoowl:r36.4.3` as its base image. This comes from
NVIDIA's `jetson-containers` project and includes TensorRT-optimized OWL-ViT.

### Option A: Pull pre-built image (fastest)

```bash
# Pull the closest available pre-built image (~19 GB, takes a while)
docker pull dustynv/nanoowl:r36.4.0

# Tag it so our Dockerfile can find it
docker tag dustynv/nanoowl:r36.4.0 nanoowl:r36.4.3
```

> **Note:** The r36.4.0 image works on R36.4.7 hosts. If you hit issues,
> use Option B instead.

### Option B: Build from jetson-containers (matches your exact L4T)

```bash
# Clone the jetson-containers project
cd ~
git clone https://github.com/dusty-nv/jetson-containers
bash jetson-containers/install.sh

# Build NanoOWL (this takes a LONG time — 1-2+ hours)
jetson-containers build --skip-tests=all nanoowl

# Find and tag the built image
docker images | grep nanoowl
# Tag it for our Dockerfile:
docker tag <image_id> nanoowl:r36.4.3
```

## 4. Build the Hydra Detect Image

```bash
cd ~/Hydra
docker build --network=host -t hydra-detect:latest .
```

Use `--network=host` to ensure DNS resolution works during the build.

> **What the Dockerfile handles automatically:**
>
> The NanoOWL base image already includes opencv-contrib-python, numpy 1.x,
> torch 2.5, and jinja2. The Dockerfile:
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

## 5. Configure

Edit `config.ini` before running. Key settings to verify:

```bash
nano config.ini
```

| Setting | Section | Notes |
|---------|---------|-------|
| `source` | `[camera]` | `0` for /dev/video0, RTSP URL, or file path |
| `engine` | `[detector]` | `yolo` (recommended) or `nanoowl` |
| `connection_string` | `[mavlink]` | `/dev/ttyACM0` or UDP endpoint |
| `enabled` | `[mavlink]` | Set `false` if no flight controller connected |

> **Recommendation:** Start with `engine = yolo` for testing. NanoOWL
> requires more GPU memory and may fail with `NVML_SUCCESS` CUDA errors
> on 8 GB Jetson boards under memory pressure. YOLO (yolov8n) is lighter
> and downloads its model automatically on first run (~6 MB).

## 6. Run Hydra Detect

**Important:** Always use `--runtime nvidia` — without it, OpenCV will
crash with `libwayland-cursor.so.0: file too short` because the container
needs the host's NVIDIA libraries mounted in.

### Quick test run (no MAVLink)

```bash
docker run --rm --runtime nvidia \
  --device /dev/video0:/dev/video0 \
  -p 8080:8080 \
  hydra-detect:latest
```

### Full run with MAVLink and persistent data

```bash
docker run --rm --runtime nvidia \
  --device /dev/video0:/dev/video0 \
  --device /dev/ttyACM0:/dev/ttyACM0 \
  -v $(pwd)/output_data:/data \
  -p 8080:8080 \
  --name hydra-detect \
  hydra-detect:latest
```

### Access the web dashboard

Open a browser to `http://<jetson-ip>:8080`

You should see a live camera feed with detection bounding boxes overlaid.

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

### NanoOWL CUDA memory error
```
NVML_SUCCESS == r INTERNAL ASSERT FAILED at CUDACachingAllocator.cpp
```
**Fix:** The Jetson's 7.4 GB shared RAM is too tight for NanoOWL under
memory pressure. Switch to `engine = yolo` in `config.ini`, or close other
GPU-using applications. Adding swap can help:
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

### GStreamer warnings (safe to ignore)
```
cannot register existing type 'GstRtpSrc'
```
These are harmless GStreamer plugin warnings inside the container.
They do not affect camera capture.

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
| 1 | No exact NanoOWL image for R36.4.7 | NVIDIA only publishes up to r36.4.0 | Use `dustynv/nanoowl:r36.4.0` and tag it |
| 2 | pip DNS failure during Docker build | Base image sets `PIP_INDEX_URL` env var to unreachable `pypi.jetson-ai-lab.dev` | Override `PIP_INDEX_URL` in Dockerfile |
| 3 | opencv-python-headless conflicts | Base image has `opencv-contrib-python`; pip's `opencv-python-headless` overwrites it | Filter out opencv from requirements |
| 4 | numpy 2.x breaks OpenCV | pip upgrades numpy to 2.x but OpenCV was compiled against 1.x | Pin `numpy<2.0` in Dockerfile |
| 5 | ultralytics pulls in opencv-python | Transitive dependency overwrites base image's CUDA OpenCV | Install ultralytics/supervision with `--no-deps` |
| 6 | cv2 crashes without `--runtime nvidia` | Container needs host NVIDIA libs mounted | Always use `--runtime nvidia` |
| 7 | NanoOWL API changed | `OwlPredictor.predict()` requires explicit `text_encodings=None` | Fixed in `nanoowl_detector.py` |
| 8 | NanoOWL CUDA OOM on 8GB Jetson | Not enough shared GPU/CPU RAM for OWL-ViT model | Use YOLO engine instead, or add swap |

---

*Guide tested on Jetson Orin Nano 8GB, L4T R36.4.7, 2026-03-14*
