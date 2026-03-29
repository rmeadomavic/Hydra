---
title: "Quickstart"
sidebarTitle: "Quickstart"
description: "Install Hydra Detect and get the operator dashboard running."
keywords:
  - install
  - setup
  - quickstart
  - Jetson
  - Docker
  - ArduPilot
---

# Quickstart

Get Hydra running on your Jetson (or any Linux machine) and open the operator dashboard. Docker is the recommended path. Bare-metal is available if you need direct hardware access without containerization.

<Info>
  **Jetson users:** If you haven't flashed your Jetson yet, start with the [Jetson flash guide](/setup/jetson-flash) first, then come back here.
</Info>

## Install

<Tabs>

<Tab title="Docker (recommended)">

Docker is the easiest path on a Jetson. The base image is large (~6 GB) but includes CUDA, PyTorch, and TensorRT pre-configured.

<Steps>

<Step title="Pull the base image">
  One-time download. Contains the full L4T PyTorch runtime.

  ```bash
  docker pull dustynv/l4t-pytorch:r36.4.0
  ```
</Step>

<Step title="Clone the repository">
  ```bash
  git clone https://github.com/rmeadomavic/Hydra.git
  cd Hydra
  ```
</Step>

<Step title="Build the Hydra image">
  ```bash
  docker build --network=host -t hydra-detect .
  ```
</Step>

<Step title="Configure">
  Open `config.ini` and verify your hardware settings. At minimum, check:

  - `[camera] source` : your camera device index or RTSP URL
  - `[mavlink] connection_string` : your serial device (e.g., `/dev/ttyACM0`) or UDP endpoint
  - `[mavlink] enabled` : set `false` if no flight controller is connected

  ```bash
  nano config.ini
  ```
</Step>

<Step title="Run the container">
  ```bash
  docker run --rm --privileged --runtime nvidia \
    --device /dev/video0 --device /dev/video2 \
    --device /dev/ttyACM0 \
    -v /usr/sbin/nvpmodel:/usr/sbin/nvpmodel:ro \
    -v /usr/bin/jetson_clocks:/usr/bin/jetson_clocks:ro \
    -v /etc/nvpmodel.conf:/etc/nvpmodel.conf:ro \
    -v /etc/nvpmodel:/etc/nvpmodel:ro \
    -v /var/lib/nvpmodel:/var/lib/nvpmodel \
    -v $(pwd)/models:/models \
    -v $(pwd)/output_data:/data \
    -p 8080:8080 \
    hydra-detect
  ```

  <Warning>
    Adjust `--device` flags to match your actual camera and serial device paths. Run `ls /dev/video*` and `ls /dev/ttyACM*` to check.
  </Warning>
</Step>

<Step title="Open the dashboard">
  Navigate to [http://localhost:8080](http://localhost:8080) in a browser. You should see a live video stream with detection overlays.
</Step>

</Steps>

</Tab>

<Tab title="Bare metal">

<Steps>

<Step title="Install pip">
  Fresh JetPack installs don't include pip.

  ```bash
  sudo apt update && sudo apt install -y python3-pip
  ```
</Step>

<Step title="Enable serial access">
  Your user needs to be in the `dialout` group to talk to the flight controller over serial. One-time step. Log out and back in after running it.

  ```bash
  sudo usermod -aG dialout $USER
  ```

  <Warning>
    You must log out and log back in (or reboot) for the group change to take effect. Run `groups` to confirm `dialout` appears in the list.
  </Warning>
</Step>

<Step title="Clone the repository">
  ```bash
  git clone https://github.com/rmeadomavic/Hydra.git
  cd Hydra
  ```
</Step>

<Step title="Install dependencies">
  ```bash
  sudo pip3 install -r requirements.txt  # includes the RF/Kismet `requests` runtime dependency
  ```

  <Note>
    Using `sudo` for pip is required because Hydra needs root access for MAVLink serial devices and `nvpmodel`/`jetson_clocks`. If you prefer a virtualenv, run the application with `sudo` as well.
  </Note>
</Step>

<Step title="Download a YOLO model">
  YOLOv8s is a good balance of speed and accuracy on Jetson. The model auto-converts to TensorRT on first run for maximum inference speed.

  ```bash
  mkdir -p models
  wget -P models https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s.pt
  ```
</Step>

<Step title="Configure">
  Open `config.ini` and point it at your hardware. At minimum, verify:

  - `[camera] source` : your camera device index or RTSP URL
  - `[mavlink] connection_string` : your serial device or UDP endpoint
  - `[detector] yolo_model` : path to the model you downloaded

  ```bash
  nano config.ini
  ```
</Step>

<Step title="Run Hydra">
  ```bash
  sudo python3 -m hydra_detect --config config.ini
  ```
</Step>

<Step title="Open the dashboard">
  Navigate to [http://localhost:8080](http://localhost:8080) in a browser on the same network.
</Step>

</Steps>

</Tab>

</Tabs>

## Preflight check (Jetson Orin Nano)

Before heading to the field, run the preflight script to verify hardware and dependencies:

```bash
./scripts/jetson_preflight.sh
```

For best inference performance, set the Jetson to maximum power mode:

```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```

<Check>
  If preflight passes, you're good to go. Make sure your camera and MAVLink wiring match what's in `config.ini`.
</Check>

## Auto-start on boot

To start Hydra automatically when the Jetson powers on, install the systemd service:

```bash
sudo cp scripts/hydra-detect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-detect
```

This ensures the detection pipeline starts on every boot without manual intervention. Useful for field-deployed vehicles.

## Next steps

<CardGroup cols={2}>

<Card title="Dashboard overview" icon="gauge" href="/features/dashboard">
  What every section of the operator dashboard does.
</Card>

<Card title="Target control" icon="crosshairs" href="/features/target-control">
  Keep in Frame, Strike, and Hold Position explained.
</Card>

<Card title="Pixhawk wiring" icon="plug" href="/setup/pixhawk">
  Connect the Jetson to your flight controller.
</Card>

<Card title="Configuration reference" icon="gear" href="/reference/configuration">
  Every config.ini parameter documented.
</Card>

</CardGroup>
