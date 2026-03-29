# Hydra Future Directions — Brainstorm

Ideas and research notes for where Hydra could go beyond v2. Organized by
category. Some are near-term extensions, some are v3 architecture, some are
speculative "what if" ideas.

## Hardware Platforms

### Alternative Compute
- **Raspberry Pi 5 + Hailo-8L** — 13 TOPS NPU, ~$100, runs YOLO at 15+ FPS.
  Much cheaper than Jetson. Could be the "FANG lite" for v3 mesh nodes that
  only need detection, not tracking or autonomy. Hailo supports ONNX/TFLite
  export from Ultralytics. Downside: no CUDA, no TensorRT, smaller model
  ceiling.
- **Seeed reComputer (Jetson Orin NX)** — Same JetPack ecosystem but in a
  sealed industrial enclosure with M.2 slots. Better for wet/dusty
  environments (USV, UGV). ~$500. Drop-in Hydra compatible.
- **Orange Pi 5 / RK3588** — Mali GPU + 6 TOPS NPU. ~$80. Community ONNX
  runtime exists. Unproven for real-time detection. Worth watching.
- **Qualcomm RB5** — Hexagon DSP + Adreno GPU. Good for drone payloads
  (lightweight, low power). SDK is rough compared to JetPack.

### Alternative Flight Controllers
- **ESP32-S3 with ArduPilot** — ArduPilot runs on ESP32 for very small
  platforms (sub-250g). MAVLink over WiFi or BLE. Could pair with Pi + Hailo
  for a micro detection node. No servo outputs though — needs external PWM
  driver.
- **CubePilot Orange+** — High-end FC with redundant IMUs. Overkill for SORCC
  but used in larger DOD programs. Compatible with Hydra's MAVLink layer.
- **Pixhawk 6X** — Current top-of-line. Ethernet port for high-bandwidth
  MAVLink (vs serial). Could enable video-over-MAVLink at higher quality.
- **MatekH743** — Budget FC popular in FPV. Runs ArduPilot. Good candidate for
  student-built platforms where cost matters.
- **Betaflight → ArduPilot migration** — Some FPV quads run Betaflight. Hydra
  requires ArduPilot for MAVLink. Students may need to reflash FCs. Document
  the migration path.

### Sensors
- **Thermal cameras (FLIR Lepton 3.5, Seek Thermal)** — USB thermal for night
  ops. YOLO can detect on thermal with fine-tuned models. Lepton 3.5 is
  160x120 IR — low res but works for person/vehicle detection at close range.
  ~$250. Would solve the night ops gap from grilling Q15.
- **Stereo cameras (OAK-D Lite)** — Depth estimation without GPS projection.
  Would make follow mode distance estimation much more accurate than
  bounding-box heuristics. USB-C, ~$150, has onboard neural accelerator.
- **LiDAR (Garmin LIDAR-Lite v4)** — Single-point range finder. Could be
  used for precise strike distance measurement instead of GPS estimation.
  I2C interface, ~$60.
- **Multispectral** — Agriculture/environmental sensors. Not SORCC-relevant
  now but could be for ISR missions that need to distinguish camouflage.

### Communications
- **LoRa (915 MHz)** — Long range (1-10 km), low bandwidth (~50 kbps).
  Perfect for telemetry and detection alerts (not video). Could replace RFD
  900x for text-only links. Cheap modules (~$15). No license needed under
  Part 15. Pair with Meshtastic firmware for mesh networking.
- **WiFi HaLow (802.11ah, 900 MHz)** — Already planned for v3 (OpenMANET).
  1 km range, IP-native. The backbone for FANG/VIPER/SPINE mesh.
- **Starlink Mini** — Portable satellite internet. ~$300/mo. Would give
  every field site global connectivity for remote monitoring, cloud log
  upload, and model updates. Latency ~40ms (good enough for dashboard,
  not for control). Power draw ~40W — needs dedicated battery.
- **5G/LTE modems** — Already ordered (4G USB modems). Tailscale over LTE
  gives stable addressing. Future: 5G for higher bandwidth video backhaul.
- **Iridium SBD** — Satellite short-burst data. Global coverage, no
  infrastructure. Send detection alerts (GPS + class + confidence) from
  anywhere on Earth. ~$1/message. For truly remote operations.
- **Bluetooth mesh** — Short range but low power. Could network Jetsons in
  a vehicle convoy (50m range). Probably not useful for SORCC but worth
  noting.

## Software Capabilities

### Detection & Tracking
- **Custom YOLO training pipeline** — Students use COCO-pretrained models.
  For mission-relevant classes (specific boat types, vehicle models,
  equipment), need a training pipeline. Roboflow or custom with Ultralytics
  HUB. Could run training on a cloud GPU and push models to Jetsons.
- **Few-shot / zero-shot detection** — Florence-2, GroundingDINO, OWLv2.
  Describe what to detect in natural language instead of training a model.
  "Find the person wearing a red jacket." Runs on Jetson with TensorRT
  optimization. Would eliminate model training entirely for many use cases.
- **Multi-camera fusion** — Multiple cameras on one vehicle (forward +
  downward, or 360 coverage). Pipeline runs one detector per camera,
  fuses tracks. Requires careful GPU memory management on 8GB Jetson.
- **Object re-identification** — Assign persistent IDs across camera views
  and vehicle handoffs. "Person #7 seen by drone is the same as person #7
  seen by USV." Requires appearance embedding model + cross-vehicle
  communication (v3 mesh).
- **Activity recognition** — Beyond detection: what is the person doing?
  Walking, running, digging, carrying equipment. Requires temporal models
  (video transformers). Probably too heavy for Jetson Orin Nano but future
  hardware may support it.

### Autonomy
- **Swarm coordination** — v3 mesh enables: converge on target, distributed
  search patterns, handoff tracking between vehicles. Requires inter-vehicle
  state sharing and deconfliction. Research: Reynolds flocking rules,
  auction-based task allocation.
- **Path planning with obstacles** — ArduPilot handles navigation but doesn't
  see obstacles in camera. Hydra could detect obstacles and send avoidance
  waypoints. Relevant for UGV in complex terrain.
- **Autonomous search patterns** — RF hunt already has lawnmower and spiral.
  Extend to visual search: optimize search pattern based on detections
  ("I found something here, search this area more densely").
- **Multi-vehicle task allocation** — "5 vehicles, 3 targets. Who goes where?"
  Requires centralized coordinator or distributed consensus. v3 NEST node
  could coordinate.
- **Geofence from TAK** — Import operational boundaries from TAK COPs
  instead of manual lat/lon in config. ATAK can draw polygons and export
  them as KML/CoT. Hydra could ingest these as geofences.

### Integration
- **TAK Server** — Currently using multicast/unicast. TAK Server (FreeTAK
  or official) provides persistence, replay, user management, and web
  access to the COP. Would enable after-action replay directly in TAK.
- **ATAK plugin (in progress)** — Radial menu for Lock/Strike/Unlock
  directly in ATAK. Extends to mission profile selection, follow mode
  initiation from the TAK map.
- **ROS2 bridge** — Some DOD programs use ROS2. A Hydra-to-ROS2 bridge
  would publish detections as ROS topics. Low priority for SORCC but
  valuable for interoperability.
- **STANAG 4586** — NATO standard for UxV interoperability. Hydra currently
  uses MAVLink. A STANAG adapter would allow Hydra to interface with
  NATO-standard ground stations. Far future.
- **Cloud dashboard** — Web dashboard served from a cloud instance (AWS/GCP)
  that aggregates all field Jetsons via LTE/Starlink. Remote instructor
  oversight from anywhere. The instructor page architecture already
  supports this — just change the polling targets from Tailscale IPs to
  cloud-routed endpoints.

### Training & Simulation
- **SITL integration** — ArduPilot Software-in-the-Loop runs on any laptop.
  Pair with a video file as camera source and Hydra runs a full simulated
  mission. Students could practice before touching real hardware.
- **Digital twin** — Gazebo or AirSim simulation with Hydra running against
  virtual cameras. Full mission rehearsal. Heavy setup but powerful for
  pre-mission planning.
- **Scenario generator** — Script that creates detection scenarios (person
  walking across field, boat on lake) as video files. Use for automated
  testing and student exercises without going to the field.
- **Scoring system** — Automated CULEX scoring: compare detections against
  ground truth target placements, compute detection rate, time-to-detect,
  false positive rate. Extends the mission tagging feature.

## Hardware Projects

### Near-Term Builds (v2 timeframe)
- **Waterproof Jetson enclosure for USV** — 3D printed or Pelican case with
  cable glands for USB camera, serial, power. Conformal coat the Jetson
  board for spray protection.
- **Servo-actuated camera gimbal** — Pan/tilt gimbal driven by servo_tracker.
  Better than fixed camera for follow mode. Could use SG90 servos + 3D
  printed frame. ~$10 in parts.
- **LED indicator strip** — NeoPixel strip on vehicle shows status:
  green=ready, blue=detecting, red=armed the alert light bar feature
  in config.ini already supports a single-channel light. Extend to
  addressable LEDs for richer status.
- **Trigger circuit PCB** — Custom PCB for the two-stage arm circuit
  (software + hardware + physical contact). Could be a simple MOSFET AND
  gate on a perfboard. Document the circuit for students to build.

### v3 Mesh Nodes
- **FANG (detection node)** — Jetson + camera + WiFi HaLow. Full Hydra
  stack. Detects and reports.
- **VIPER (effector node)** — FC + WiFi HaLow. No detection, just receives
  commands. Lightweight, fast, cheap.
- **SPINE (relay node)** — RPi + WiFi HaLow. OpenMANET mesh relay.
  Infrastructure node.
- **NEST (coordinator)** — Laptop/tablet + WiFi HaLow gateway. Instructor
  GCS with multi-vehicle C2.

## Research Questions
1. Can Hailo-8L run the same YOLO models as Jetson at acceptable FPS?
2. What's the realistic WiFi HaLow range in wooded terrain (training area)?
3. Can LoRa carry enough bandwidth for detection alerts + GPS telemetry?
4. Is Starlink Mini power-viable on a portable battery for 4-hour exercises?
5. Can Florence-2 / GroundingDINO run on Jetson Orin Nano at >2 FPS?
6. What's the minimum viable swarm coordination for 3-5 vehicles?
7. Can ArduPilot SmartRTL record enough breadcrumbs for a 30-minute mission?
8. What thermal camera resolution is needed for person detection at 50m?

## Decision Log
- 2026-03-29: Follow mode > strike as Week 4 "wow moment" (safer, more visual)
- 2026-03-29: SmartRTL for all platforms (consistent behavior, simple to teach)
- 2026-03-29: Dogleg RTL for drones only (tactical LZ concealment)
- 2026-03-29: Fixed wing = sensor only, no autonomous behaviors (speed constraints)
- 2026-03-29: Mission profiles (RECON/DELIVERY/STRIKE) as dashboard presets
- 2026-03-29: Two-stage arm circuit for strike (software + hardware + physical)
- 2026-03-29: Instructor page is grading tool with abort exception, not C2
- 2026-03-29: Tailscale for cross-team comms, multicast for same-subnet only
