# Analog FPV Camera Setup for Hydra

Use a traditional analog FPV camera (Caddx Ratel 2, RunCam Phoenix 2, etc.)
with Hydra via a CVBS-to-USB capture dongle. This lets you run the detection
pipeline on an analog video feed while keeping your FPV goggles view intact.

---

## Parts needed

| Part | Purpose | Est. cost |
|------|---------|-----------|
| CVBS-to-USB capture dongle (UVC) | Digitizes analog video for the Jetson | ~$8-15 |
| Passive video Y-splitter (1M→2F RCA) | Splits camera signal to VTX + dongle | ~$3-5 |
| Analog FPV camera | Video source (Caddx Ratel 2, RunCam Phoenix 2, etc.) | ~$20-40 |
| Analog VTX | Transmits live video to goggles | ~$15-30 |

**Total: ~$50-90** (most builds already have the camera and VTX).

### Capture dongle selection

Look for these keywords when buying:

- "CVBS to USB capture UVC" or "AV to USB camera module"
- Must say "UVC" or "driver-free" or "no driver needed"
- Must support NTSC and PAL (most do, with auto-detection)
- MJPEG output preferred (lower USB bandwidth than YUY2)

**Good chipsets:** Macrosilicon MS2109, UTV007.

**Avoid:** anything marked "Windows only", old Empia EM2860/EM2870 EasyCap
clones (flaky on modern kernels), HDMI capture cards (wrong input type).

---

## Signal path

```
                                    ┌──→ Analog VTX ──→ Goggles (pilot view)
                                    │
Caddx Ratel 2 ──→ Y-splitter ──────┤
  (CVBS out)        (1 to 2)        │
                                    └──→ USB capture dongle ──→ Jetson USB 3.0
                                              /dev/videoX         (Hydra input)
```

### With standalone OSD chip (optional)

```
                                    ┌──→ OSD board ──→ VTX ──→ Goggles
                                    │      ↑ MAVLink serial
                                    │      │ from Pixhawk or Jetson
Caddx Ratel 2 ──→ Y-splitter ──────┤
  (CVBS out)        (1 to 2)        │
                                    └──→ USB capture dongle ──→ Jetson USB 3.0
```

The OSD board sits inline on the VTX leg only. The Jetson gets clean,
un-OSD'd video — no bounding boxes in training data, no OSD text confusing
the detector.

---

## Wiring

### Camera → Y-splitter
- Camera video out (yellow RCA or bare wire) → Y-splitter input
- Camera GND → common ground
- Camera 5V → from PDB or BEC (NOT from the capture dongle)

### Y-splitter → VTX
- Splitter output 1 → VTX video in
- VTX powered from PDB as normal

### Y-splitter → Capture dongle
- Splitter output 2 → Capture dongle CVBS/AV input (yellow RCA)
- Capture dongle GND → common ground
- Capture dongle USB → Jetson USB 3.0 port

### OSD board (optional, on VTX leg only)
- Splitter output 1 → OSD video IN
- OSD video OUT → VTX video in
- OSD MAVLink RX → Pixhawk TELEM TX (or Jetson UART TX via level shifter)
- OSD 5V + GND → from PDB

Hydra's existing `statustext` OSD mode works with standalone OSD boards
(MinimOSD, MicroMinimOSD, any MAX7456/AT7456E-based board) with zero code
changes. Just set `[osd] mode = statustext` in config.ini.

---

## Jetson verification (before running Hydra)

Run these commands to verify your capture dongle works:

```bash
# 1. Plug in the capture dongle (no camera needed yet)
dmesg | tail -10
# Look for: uvcvideo: Found UVC x.xx device

# 2. Find the device node
ls /dev/video*
# Note which /dev/videoX is new — that's your capture dongle

# 3. Install V4L2 utilities (if not already present)
sudo apt install v4l-utils

# 4. Check device capabilities
v4l2-ctl -d /dev/videoX --all
# Look for "Video input" showing composite/CVBS
# Look for "Video Standard" showing NTSC or PAL

# 5. List available inputs
v4l2-ctl -d /dev/videoX --list-inputs
# Composite should be input 0

# 6. Quick capture test (requires camera connected)
v4l2-ctl -d /dev/videoX --set-input=0
v4l2-ctl -d /dev/videoX --set-fmt-video=width=720,height=480,pixelformat=MJPG
v4l2-ctl -d /dev/videoX --stream-mmap --stream-count=10 --stream-to=test.raw
# If this runs without errors, the dongle is working

# 7. OpenCV quick test (replace X with your device number)
python3 -c "
import cv2
cap = cv2.VideoCapture(X, cv2.CAP_V4L2)
ret, frame = cap.read()
if ret:
    print(f'Capture OK: {frame.shape}')
else:
    print('Capture FAILED')
cap.release()
"
```

---

## Hydra config.ini for analog

```ini
[camera]
source_type = analog
source = 2                    ; /dev/video2 — verify YOUR device number
width = 720
height = 480
fps = 30
hfov_deg = 120.0              ; FPV cameras are typically 120°+ with 2.1mm lens
video_standard = ntsc         ; ntsc, pal, or auto

[detector]
yolo_model = yolov8s.pt
yolo_confidence = 0.45        ; May need to bump to 0.5 for noisy analog feeds

[osd]
enabled = true
mode = statustext             ; Works with standalone OSD board on the VTX leg
```

---

## udev rule for a persistent device name

Capture dongles can appear as different `/dev/videoX` numbers depending on
plug order. Create a udev rule for a stable symlink:

```bash
# Find your dongle's vendor/product IDs
udevadm info -a /dev/videoX | grep -E "idVendor|idProduct"
```

Add to `/etc/udev/rules.d/99-hydra.rules`:

```
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="534d", ATTRS{idProduct}=="2109", SYMLINK+="videoANALOG"
```

Then use `source = /dev/videoANALOG` in config.ini.

---

## Analog vs. digital comparison

| | Analog (Ratel 2 + VTX) | Digital (Webcam) | Digital (HDZero) |
|---|---|---|---|
| Resolution to Jetson | 720x480 (NTSC) | 1080p | N/A (separate feed) |
| Latency (camera→Jetson) | ~60-100ms | ~30-50ms | N/A |
| Latency (camera→goggles) | ~10ms (analog) | N/A | ~30ms |
| YOLO input (after resize) | 640x480 | 640x480 | 640x480 |
| FPS to detector | 25-30 | 30 | 30 |
| OSD path | Standalone OSD + MAVLink | Web dashboard | MSP DisplayPort |
| Cost of video system | ~$40-60 | ~$20-40 | ~$100-200 |

---

## Troubleshooting

**Dongle not detected (`dmesg` shows nothing)**
- Try a different USB port (prefer USB 3.0)
- Check the cable — some micro-USB cables are charge-only
- Verify the dongle is UVC class, not a proprietary chipset

**`v4l2-ctl --list-inputs` shows no composite input**
- The dongle may not support input selection (some auto-detect)
- Hydra will still try to open the device — this warning is usually harmless

**Black frames or no signal**
- Verify the camera is powered (separate 5V, not from the dongle)
- Check the Y-splitter connections — try swapping the two outputs
- Try setting `video_standard = pal` if your camera is PAL

**Low FPS or stuttering**
- Use a USB 3.0 port, not USB 2.0
- Set `fps = 25` in config.ini to match PAL frame rate
- Ensure no other process has the device open (`fuser /dev/videoX`)
