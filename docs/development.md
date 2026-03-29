# Development

This guide covers the project layout, testing, code standards, and extension points for developers building on Hydra.

## Project Layout

```
Hydra/
  config.ini                          # All runtime settings
  config.ini.factory                  # Factory defaults (do not edit)
  profiles.json                       # Mission profiles (class filters, thresholds)
  requirements.txt                    # Python dependencies
  Dockerfile                          # Jetson container build (l4t-pytorch base)
  CLAUDE.md                           # Claude Code guidelines

  scripts/
    hydra-detect.service              # systemd unit file
    hydra-setup.sh                    # Interactive Jetson setup
    hydra-launch.sh                   # tmux session launcher
    hydra_sync.sh                     # Multi-Jetson code sync
    jetson_preflight.sh               # Hardware sanity checks
    setup_headless.sh                 # Headless boot configuration
    setup_tailscale.sh                # Tailscale remote access setup
    hydra_osd.lua                     # ArduPilot Lua script for FPV OSD
    osd_layout.py                     # OSD layout design tool
    rf_hunt_demo.py                   # RF hunt demonstration script
    rf_live_test.py                   # RF hunt live testing
    rf_power_scan.py                  # RTL-SDR power scanner

  models/                             # YOLO model files (.pt)
    manifest.json                     # Model manifest (hash, classes)

  output_data/
    logs/                             # Detection logs (JSONL/CSV) and hydra.log
    images/                           # Annotated detection snapshots
    crops/                            # Cropped object images
    kismet/                           # Kismet capture files

  hydra_detect/
    __init__.py
    __main__.py                       # CLI entry point (python -m hydra_detect)
    pipeline.py                       # Main loop: detect, track, alert, repeat
    camera.py                         # Thread-safe capture (USB, RTSP, file, analog)
    tracker.py                        # ByteTrack multi-object tracker wrapper
    overlay.py                        # Bounding boxes, HUD, target lock rendering
    osd.py                            # FPV OSD (statustext, named_value, msp_displayport)
    msp_displayport.py                # MSP v1 DisplayPort protocol implementation
    mavlink_io.py                     # MAVLink connection, alerts, vehicle commands
    mavlink_video.py                  # Low-bandwidth video over MAVLink telemetry
    geo_tracking.py                   # CAMERA_TRACKING_GEO_STATUS for GCS map
    detection_logger.py               # CSV/JSONL logging with background writer
    event_logger.py                   # Mission event timeline (actions + vehicle track)
    verify_log.py                     # SHA-256 hash chain verifier
    review_export.py                  # Standalone HTML map report generator
    autonomous.py                     # Geofenced autonomous strike controller
    approach.py                       # Follow, Drop, Strike approach modes
    dogleg_rtl.py                     # Tactical return path computation
    mission_profiles.py               # RECON/DELIVERY/STRIKE profile presets
    profiles.py                       # JSON profile loading and validation
    servo_tracker.py                  # Pixel-lock servo controller (pan + strike)
    rtsp_server.py                    # GStreamer RTSP H.264 output
    model_manifest.py                 # Model manifest validation and generation
    config_schema.py                  # Typed config validation with plain-English errors
    system.py                         # Jetson stats, power modes, model listing
    tls.py                            # Self-signed TLS certificate generation

    detectors/
      __init__.py
      base.py                         # Abstract detector interface (Detection, DetectionResult)
      yolo_detector.py                # YOLOv8/v11 detector via ultralytics

    rf/
      __init__.py
      hunt.py                         # RF hunt state machine (IDLE->SEARCHING->HOMING->CONVERGED)
      kismet_client.py                # Kismet REST API client for RSSI polling
      kismet_manager.py               # Kismet subprocess lifecycle manager
      navigator.py                    # Gradient ascent waypoint navigation
      search.py                       # Lawnmower and spiral pattern generators
      signal.py                       # RSSI filtering and gradient analysis
      rssi_protocol.py                # RSSI client protocol interface
      rtl_power_client.py             # RTL-SDR power scanning client

    tak/
      __init__.py
      cot_builder.py                  # CoT XML message builder
      tak_output.py                   # TAK multicast/unicast CoT output thread
      tak_input.py                    # TAK command listener (GeoChat + custom CoT)
      type_mapping.py                 # YOLO class to MIL-STD-2525 type mapping

    web/
      __init__.py
      server.py                       # FastAPI REST API + MJPEG stream + pages
      config_api.py                   # Config read/write with file locking
      templates/
        base.html                     # SPA shell (nav, layout, shared CSS/JS)
        operations.html               # Operator dashboard (detections, controls)
        settings.html                 # Settings panel (camera, model, config)
        control.html                  # Mobile operator control page
        instructor.html               # Multi-vehicle instructor overview
        review.html                   # Post-mission review map (standalone)
        setup.html                    # First-boot setup wizard
      static/                         # CSS, JS, and image assets

  tests/
    __init__.py
    test_alert_filter.py              # Alert class filtering
    test_autonomous.py                # Autonomous controller qualification gates
    test_camera.py                    # Camera abstraction
    test_camera_loss.py               # Camera loss degraded mode
    test_chain_of_custody.py          # SHA-256 hash chain verification
    test_config_api.py                # Config read/write API
    test_config_freeze.py             # Config freeze during engagement
    test_config_schema.py             # Config schema validation
    test_dashboard_resilience.py      # Dashboard degraded mode behavior
    test_detection_logger.py          # Detection logging
    test_detectors.py                 # Detector interface
    test_drop_strike.py               # Drop and strike modes
    test_event_logger.py              # Event timeline logger
    test_geo_tracking.py              # Geo-tracking message generation
    test_instructor_ops.py            # Instructor page and abort
    test_log_endpoint.py              # /api/logs endpoint
    test_map_replay.py                # Map replay event parsing
    test_mavlink_commands.py          # MAVLink vehicle commands
    test_mavlink_safety.py            # MAVLink safety features
    test_mavlink_sim_gps.py           # Simulated GPS
    test_mavlink_video.py             # MAVLink video thumbnails
    test_mission_profiles.py          # Mission profile presets
    test_model_manifest.py            # Model manifest system
    test_osd.py                       # FPV OSD rendering
    test_overlay_dimming.py           # Overlay brightness adaptation
    test_pipeline_callbacks.py        # Pipeline callback wiring
    test_preflight_ui.py              # Pre-flight checklist
    test_profiles.py                  # Profile loading
    test_review.py                    # Review page data parsing
    test_rf_geofence.py               # RF hunt geofence integration
    test_rf_hunt.py                   # RF hunt state machine
    test_rf_integration.py            # RF hunt end-to-end
    test_rf_kismet.py                 # Kismet client
    test_rf_kismet_manager.py         # Kismet process manager
    test_rf_navigator.py              # Gradient ascent navigator
    test_rf_search.py                 # Search pattern generators
    test_rf_signal.py                 # RSSI filtering
    test_rf_web_api.py                # RF hunt API endpoints
    test_rtsp_server.py               # RTSP output
    test_safety_hardening.py          # Safety feature tests
    test_servo_tracker.py             # Servo PWM control
    test_sitl_mode.py                 # SITL simulation mode
    test_tak.py                       # TAK CoT output
    test_tak_input.py                 # TAK command listener
    test_tak_security.py              # TAK callsign allowlist and HMAC
    test_tak_unicast_manifest.py      # TAK unicast target management
    test_telemetry.py                 # Telemetry parsing
    test_tls_security.py              # TLS certificate generation
    test_tracker.py                   # ByteTrack wrapper
    test_vehicle_config.py            # Vehicle profile overrides
    test_web_api.py                   # Web API endpoints
    test_zero_touch.py                # First-boot auto-configuration
```

## Testing

Tests use pytest. Most tests work without hardware by mocking the camera, MAVLink, and YOLO model.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_autonomous.py -v

# Run tests matching a keyword
python -m pytest tests/ -k "rf_hunt" -v
```

Some tests are marked with `@pytest.mark.hardware` for tests that require a real camera or MAVLink connection. These are skipped by default in CI.

Configuration:

```ini
# pytest.ini
[pytest]
testpaths = tests
```

## Config Schema Validation

Every config key is defined in `config_schema.py` with:

- Type (bool, int, float, string, enum)
- Required flag
- Min/max value bounds
- Enum choices
- Human-readable description

The `validate_config()` function runs at startup and returns errors (blocking) and warnings (informational). Unknown keys generate warnings (typo detection).

To add a new config key:

1. Add the key to the `SCHEMA` dict in `config_schema.py`
2. Add a default value in `config.ini` and `config.ini.factory`
3. Read the value in `pipeline.py` with `self._cfg.get()` or `self._cfg.getfloat()`
4. Add a test in `test_config_schema.py`

## Model Manifest

The `models/manifest.json` file tracks model files with their SHA-256 hash and class list. The manifest auto-updates on startup when new `.pt` files are found.

```json
[
  {
    "filename": "yolov8n.pt",
    "sha256": "a1b2c3...",
    "classes": ["person", "bicycle", "car", ...]
  }
]
```

The pre-flight check validates model files against the manifest hash. A mismatch indicates the model was corrupted or replaced.

## Detector Interface

New detectors implement the `BaseDetector` abstract class in `detectors/base.py`:

```python
class BaseDetector(ABC):
    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def detect(self, frame: np.ndarray) -> DetectionResult: ...

    @abstractmethod
    def unload(self) -> None: ...
```

The `DetectionResult` dataclass holds a list of `Detection` objects and the inference time.

## Coding Standards

- Python 3.10+. Use `X | None` not `Optional[X]`.
- All modules use `from __future__ import annotations`.
- Dataclasses for data containers.
- `threading.Lock` for shared state (not asyncio).
- No blocking I/O in the main detection loop.
- Bounded collections everywhere (ring buffers, maxsize queues).
- Bearer token auth for all control endpoints.
- No secrets in committed files. Use config.ini.

## Lint and Type Check

```bash
flake8 hydra_detect/ tests/
mypy hydra_detect/
```

## Common Commands

```bash
# Run the application
python -m hydra_detect --config config.ini

# Run with vehicle profile
python -m hydra_detect --config config.ini --vehicle usv

# SITL mode
python -m hydra_detect --config config.ini --sim

# Build Docker image
docker build --network=host --no-cache -t hydra-detect .

# Monitor Jetson resources
tegrastats
# or
jtop
```
