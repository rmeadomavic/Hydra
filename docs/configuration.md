# Configuration Reference

All Hydra settings live in `config.ini`. The file uses standard INI format with `;` and `#` for inline comments.

Hydra validates every key at startup against `config_schema.py`. Errors block safety-critical features. Warnings appear in the log but do not block startup.

## Common Setups

### Minimal (Webcam, No MAVLink)

```ini
[camera]
source_type = auto
source = auto

[detector]
yolo_model = yolov8n.pt
yolo_confidence = 0.45

[mavlink]
enabled = false

[web]
enabled = true
port = 8080
```

### USV Field Deployment

```ini
[camera]
source_type = usb
source = 0
hfov_deg = 60.0

[detector]
yolo_model = yolov8s.pt
yolo_confidence = 0.5
yolo_classes =

[mavlink]
enabled = true
connection_string = /dev/ttyTHS1
baud = 921600
alert_classes = person, boat, kayak

[autonomous]
enabled = true
geofence_lat = 34.05
geofence_lon = -118.25
geofence_radius_m = 200.0
min_confidence = 0.85
allowed_classes = boat, buoy

[tak]
enabled = true
callsign = HYDRA-1-USV
```

### Drone with Autonomous

```ini
[mavlink]
enabled = true
connection_string = /dev/ttyTHS1
baud = 921600

[autonomous]
enabled = true
geofence_lat = 35.05
geofence_lon = -79.49
geofence_radius_m = 300.0
min_confidence = 0.80
min_track_frames = 3
allowed_classes = person, vehicle
allowed_vehicle_modes = AUTO
dogleg_distance_m = 200
dogleg_bearing = perpendicular
dogleg_altitude_m = 50

[approach]
follow_speed_max = 8.0
follow_distance_m = 20.0
abort_mode = LOITER
```

### SITL Testing

```ini
[camera]
source_type = file
source = sim_video.mp4

[mavlink]
enabled = true
connection_string = udp:127.0.0.1:14550
sim_gps_lat = 35.0527
sim_gps_lon = -79.4927

[osd]
enabled = false

[servo_tracking]
enabled = false
```

---

## [camera]

Camera source configuration. Hydra auto-detects webcams and capture cards via V4L2 sysfs.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `source_type` | enum | `auto` | auto, usb, rtsp, file, v4l2, analog | Camera source type. `auto` scans for the first usable webcam or capture card. |
| `source` | string | `auto` | -- | Camera source path, URL, or device index. Examples: `0`, `/dev/video2`, `rtsp://host:554/stream`, `test.mp4` |
| `width` | int | `640` | 160-3840 | Capture frame width in pixels. |
| `height` | int | `480` | 120-2160 | Capture frame height in pixels. |
| `fps` | int | `30` | 1-120 | Target capture frame rate. |
| `hfov_deg` | float | `60.0` | 10.0-180.0 | Horizontal field of view in degrees. Used to estimate target bearing from pixel offset. Set accurately for your lens. |
| `video_standard` | enum | `ntsc` | ntsc, pal | Analog video standard. Only applies when `source_type = analog`. |

## [detector]

YOLO model and inference parameters.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `yolo_model` | string | `yolov8n.pt` | -- | YOLO model filename. Searched in `/models` (Docker), `./models`, then project root. **Required.** |
| `yolo_confidence` | float | `0.45` | 0.0-1.0 | Detection confidence threshold. Lower catches more, higher reduces false positives. Adjustable at runtime via API. |
| `yolo_imgsz` | int | `416` | 32-1280 | Inference resolution (pixels). Lower is faster, higher is more accurate. Must be a multiple of 32. |
| `yolo_classes` | string | *(empty)* | -- | Comma-separated COCO class IDs to detect. Empty means all classes. Example: `0,2,7` for person, car, truck. |
| `low_light_luminance` | float | `40` | -- | Average frame brightness below this triggers the LOW LIGHT badge on the dashboard. |

## [tracker]

ByteTrack multi-object tracker parameters.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `track_thresh` | float | `0.5` | 0.0-1.0 | Minimum confidence to initialize a new track. |
| `track_buffer` | int | `30` | 1-300 | Frames to keep a lost track alive before dropping it. Higher values tolerate longer occlusions. |
| `match_thresh` | float | `0.8` | 0.0-1.0 | IoU threshold for matching detections to existing tracks. |

## [mavlink]

MAVLink connection and alert behavior.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `true` | -- | Enable MAVLink connection. |
| `connection_string` | string | `/dev/ttyTHS1` | -- | Serial port or UDP address. Examples: `/dev/ttyACM0`, `udp:127.0.0.1:14550` |
| `baud` | int | `921600` | 9600-3000000 | Serial baud rate. Ignored for UDP connections. |
| `source_system` | int | `1` | 1-255 | MAVLink system ID. |
| `min_gps_fix` | int | `3` | 0-6 | Minimum GPS fix type. 0=no GPS, 2=2D, 3=3D, 4=DGPS, 5=RTK float, 6=RTK fixed. |
| `alert_statustext` | bool | `true` | -- | Send detection alerts as STATUSTEXT messages to the GCS. |
| `alert_interval_sec` | float | `5.0` | 0.1-60.0 | Per-label throttle. Minimum seconds between repeat STATUSTEXT alerts for the same object class. |
| `severity` | int | `2` | 0-7 | MAVLink severity level. 0=Emergency, 2=Critical, 4=Warning, 6=Info, 7=Debug. |
| `alert_classes` | string | *(list)* | -- | Comma-separated class labels that trigger alerts. Empty means all detected classes. |
| `auto_loiter_on_detect` | bool | `false` | -- | Switch vehicle to LOITER on first detection. Use with caution. |
| `guided_roi_on_detect` | bool | `false` | -- | Point gimbal at the highest-confidence detection. |
| `strike_distance_m` | float | `20.0` | 0.0-1000.0 | Distance in metres to project the strike waypoint ahead of the target bearing. |
| `geo_tracking` | bool | `true` | -- | Send CAMERA_TRACKING_GEO_STATUS messages for GCS map markers. |
| `geo_tracking_interval` | float | `2.0` | 0.1-60.0 | Seconds between geo-tracking updates. |
| `sim_gps_lat` | float | *(empty)* | -90.0-90.0 | Simulated GPS latitude. Overrides real GPS for bench testing. |
| `sim_gps_lon` | float | *(empty)* | -180.0-180.0 | Simulated GPS longitude. |

## [alerts]

Alert output configuration for light bar and rate limiting.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `light_bar_enabled` | bool | `false` | -- | Flash a servo-controlled light bar on detection. |
| `light_bar_channel` | int | `4` | 1-16 | Servo channel for the light bar. |
| `light_bar_pwm_on` | int | `1900` | 500-2500 | PWM value for light on. |
| `light_bar_pwm_off` | int | `1100` | 500-2500 | PWM value for light off. |
| `light_bar_flash_sec` | float | `0.5` | 0.05-10.0 | Flash duration in seconds. |
| `global_max_per_sec` | float | `2.0` | 0.1-20.0 | Maximum STATUSTEXT alerts per second across all labels. Prevents log flooding. |
| `priority_labels` | string | *(empty)* | -- | Comma-separated labels that bypass the global rate cap. |

## [web]

Web dashboard and API server.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `true` | -- | Enable the web dashboard and REST API. |
| `host` | string | `0.0.0.0` | -- | Bind address. `0.0.0.0` listens on all interfaces. |
| `port` | int | `8080` | 1-65535 | HTTP(S) port. |
| `mjpeg_quality` | int | `70` | 1-100 | JPEG quality for the MJPEG stream. Lower saves bandwidth. Adjustable at runtime. |
| `api_token` | string | *(empty)* | -- | Bearer token for control endpoints. Auto-generated on first boot if empty. |
| `tls_enabled` | bool | `false` | -- | Enable HTTPS with self-signed certificate. |
| `tls_cert` | string | `certs/hydra.crt` | -- | Path to TLS certificate. Auto-generated if missing. |
| `tls_key` | string | `certs/hydra.key` | -- | Path to TLS private key. |

## [osd]

FPV OSD overlay. Requires MAVLink enabled.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `false` | -- | Enable FPV OSD overlay. |
| `mode` | enum | `statustext` | statustext, named_value, msp_displayport | OSD rendering mode. See [FPV OSD](fpv-osd.md). |
| `update_interval` | float | `2.0` | 0.1-30.0 | Seconds between OSD updates. Lower is snappier but uses more MAVLink bandwidth. |
| `serial_port` | string | `/dev/ttyUSB0` | -- | Serial device for MSP DisplayPort mode only. |
| `serial_baud` | int | `115200` | 9600-3000000 | Baud rate for MSP serial. |
| `canvas_cols` | int | `50` | 1-100 | OSD canvas columns (MSP DisplayPort only). |
| `canvas_rows` | int | `18` | 1-100 | OSD canvas rows (MSP DisplayPort only). |

## [autonomous]

Autonomous strike controller. Off by default. See [Autonomous Operations](autonomous-operations.md).

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `false` | -- | Enable autonomous controller. |
| `geofence_lat` | float | `0.0` | -90.0-90.0 | Circle geofence center latitude. |
| `geofence_lon` | float | `0.0` | -180.0-180.0 | Circle geofence center longitude. |
| `geofence_radius_m` | float | `500.0` | 1.0-50000.0 | Circle geofence radius in metres. |
| `geofence_polygon` | string | *(empty)* | -- | Polygon vertices as `lat,lon;lat,lon;...`. Overrides circle geofence when set. |
| `min_confidence` | float | `0.85` | 0.0-1.0 | Minimum detection confidence for autonomous action. |
| `min_track_frames` | int | `5` | 1-100 | Consecutive frames a target must be tracked before qualifying. |
| `allowed_classes` | string | *(empty)* | -- | Comma-separated class labels. Empty means fail-closed (no classes qualify). |
| `strike_cooldown_sec` | float | `30.0` | 0.0-3600.0 | Seconds between autonomous strikes. |
| `allowed_vehicle_modes` | string | `AUTO` | -- | Comma-separated ArduPilot modes. Vehicle must be in one of these for autonomous action. |
| `gps_max_stale_sec` | float | `2.0` | 0.5-30.0 | Abort if GPS data is older than this many seconds. |
| `require_operator_lock` | bool | `true` | -- | Require operator to lock a target before autonomous strike. |
| `arm_channel` | int | `0` | -- | Software arm servo channel. 0 disables. |
| `arm_pwm_armed` | int | `1900` | -- | PWM for software arm engaged. |
| `arm_pwm_safe` | int | `1100` | -- | PWM for software arm safe. |
| `hardware_arm_channel` | int | `0` | -- | RC channel for hardware arm switch. 0 disables. |
| `dogleg_distance_m` | float | `200` | -- | Offset distance for dogleg RTL (drones). |
| `dogleg_bearing` | string | `perpendicular` | -- | Offset bearing. `perpendicular` or compass degrees. |
| `dogleg_altitude_m` | float | `50` | -- | Climb altitude before dogleg waypoint. |

## [approach]

Approach controller parameters for Follow, Drop, and Strike modes.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `follow_speed_min` | float | `2.0` | -- | Minimum follow speed (m/s). Used when target is at frame edge. |
| `follow_speed_max` | float | `10.0` | -- | Maximum follow speed (m/s). Used when target is centered. |
| `follow_distance_m` | float | `15.0` | -- | Approach distance for waypoint projection. |
| `follow_yaw_rate_max` | float | `30.0` | -- | Maximum yaw rate (deg/s) during follow. |
| `abort_mode` | string | `LOITER` | -- | Vehicle mode on approach abort. |
| `waypoint_interval` | float | `0.5` | -- | Minimum seconds between waypoint sends. |

## [drop]

Payload drop configuration.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `false` | -- | Enable drop mode. |
| `servo_channel` | int | `0` | -- | Servo channel for drop mechanism. 0 disables. |
| `pwm_release` | int | `1900` | -- | PWM value to release payload. |
| `pwm_hold` | int | `1100` | -- | PWM value to hold payload. |
| `pulse_duration` | float | `1.0` | -- | Seconds to hold release PWM. |
| `drop_distance_m` | float | `3.0` | -- | Release distance from target in metres. |

## [rf_homing]

RF source localization via Kismet. See [RF Homing](rf-homing.md).

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `false` | -- | Enable RF homing. |
| `mode` | enum | `wifi` | wifi, sdr, rtl433 | RF homing mode. |
| `target_bssid` | string | *(empty)* | -- | Target MAC address for WiFi mode. Format: `AA:BB:CC:DD:EE:FF` |
| `target_freq_mhz` | float | `915.0` | 1.0-6000.0 | Target frequency for SDR mode. |
| `kismet_host` | string | `http://localhost:2501` | -- | Kismet REST API URL. |
| `kismet_user` | string | *(empty)* | -- | Kismet username. |
| `kismet_pass` | string | *(empty)* | -- | Kismet password. Redacted in API responses. |
| `search_pattern` | enum | `lawnmower` | lawnmower, spiral, expanding_square | Search flight pattern. |
| `search_area_m` | float | `100.0` | 10.0-10000.0 | Search area size in metres. |
| `search_spacing_m` | float | `20.0` | 1.0-1000.0 | Grid spacing between search legs. |
| `search_alt_m` | float | `15.0` | 1.0-500.0 | Search altitude in metres. |
| `rssi_threshold_dbm` | float | `-80.0` | -120.0-0.0 | RSSI level to switch from search to homing. |
| `rssi_converge_dbm` | float | `-40.0` | -120.0-0.0 | RSSI level to declare source found. |
| `rssi_window` | int | `10` | 1-100 | RSSI sliding window average size. |
| `gradient_step_m` | float | `5.0` | 0.5-100.0 | Step size for gradient ascent probes. |
| `gradient_rotation_deg` | float | `45.0` | 1.0-180.0 | Rotation angle when signal drops. |
| `poll_interval_sec` | float | `0.5` | 0.1-10.0 | Seconds between RSSI polls. |
| `arrival_tolerance_m` | float | `3.0` | 0.5-50.0 | Distance to consider a waypoint reached. |
| `gps_required` | bool | `false` | -- | Require GPS fix before starting a hunt. |
| `kismet_source` | string | `rtl433-0` | -- | Kismet data source name. |
| `kismet_capture_dir` | string | `./output_data/kismet` | -- | Kismet capture file directory. |
| `kismet_max_capture_mb` | float | `100.0` | 1.0-10000.0 | Maximum Kismet capture file size in MB. |
| `kismet_auto_spawn` | bool | `false` | -- | Auto-start Kismet server if not running. |

## [servo_tracking]

Pixel-lock servo tracker. Maps camera error to PWM output for pan and strike servos.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `false` | -- | Enable servo tracking. |
| `pan_channel` | int | `1` | 1-16 | Pan servo channel. |
| `pan_pwm_center` | int | `1500` | 500-2500 | Pan center PWM. |
| `pan_pwm_range` | int | `500` | 50-1000 | Pan PWM range from center. |
| `pan_invert` | bool | `false` | -- | Invert pan direction. |
| `pan_dead_zone` | float | `0.05` | 0.0-0.5 | Dead zone as fraction of frame width. |
| `pan_smoothing` | float | `0.3` | 0.0-1.0 | Exponential smoothing alpha. Higher = more responsive, lower = smoother. |
| `strike_channel` | int | `2` | 1-16 | Strike servo channel. |
| `strike_pwm_fire` | int | `1900` | 500-2500 | Strike fire PWM. |
| `strike_pwm_safe` | int | `1100` | 500-2500 | Strike safe PWM. |
| `strike_duration` | float | `0.5` | 0.1-10.0 | Strike pulse duration in seconds. |
| `replaces_yaw` | bool | `false` | -- | Pan servo replaces vehicle yaw control. When true, the vehicle does not yaw to track. |

> [!WARNING]
> Servo channels must not collide with motor outputs. Hydra checks against `reserved_channels` in the vehicle profile and disables servo tracking if a conflict is detected.

## [logging]

Detection logging and application log configuration.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `log_dir` | string | `./output_data/logs` | -- | Detection log directory. |
| `log_format` | enum | `jsonl` | jsonl, csv | Detection log format. |
| `save_images` | bool | `true` | -- | Save annotated JPEG snapshots on detection. |
| `image_dir` | string | `./output_data/images` | -- | Annotated image directory. |
| `image_quality` | int | `90` | 1-100 | JPEG quality for saved images. |
| `save_crops` | bool | `false` | -- | Save cropped images of detected objects. |
| `crop_dir` | string | `./output_data/crops` | -- | Crop image directory. |
| `max_log_size_mb` | float | `10.0` | 1.0-1000.0 | Max log file size before rotation. |
| `max_log_files` | int | `20` | 1-100 | Max rotated log files to keep. |
| `app_log_file` | bool | `true` | -- | Write application log to `hydra.log`. |
| `app_log_level` | enum | `INFO` | DEBUG, INFO, WARNING, ERROR, CRITICAL | Application log level. |
| `wipe_on_start` | bool | `false` | -- | Delete previous session logs on startup. Use for OPSEC-sensitive deployments. |

## [watchdog]

Pipeline health monitoring.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `max_stall_sec` | float | `30.0` | 5.0-300.0 | Force-exit if no frame is processed within this many seconds. |

## [rtsp]

GStreamer RTSP output stream.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `true` | -- | Enable RTSP output. Requires GStreamer. |
| `bind` | string | `127.0.0.1` | -- | Bind address. `127.0.0.1` for local only, `0.0.0.0` for all interfaces. |
| `port` | int | `8554` | 1-65535 | RTSP server port. |
| `mount` | string | `/hydra` | -- | RTSP stream mount path. URL becomes `rtsp://host:port/hydra`. |
| `bitrate` | int | `2000000` | 100000-50000000 | H.264 encoding bitrate in bits/sec. |

## [mavlink_video]

Low-bandwidth video thumbnails over MAVLink telemetry radio.

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `false` | -- | Enable MAVLink video streaming. |
| `width` | int | `160` | 32-1280 | Thumbnail width. |
| `height` | int | `120` | 32-720 | Thumbnail height. |
| `jpeg_quality` | int | `20` | 1-100 | JPEG compression quality. Lower saves bandwidth. |
| `max_fps` | float | `2.0` | 0.1-30.0 | Maximum frame rate. |
| `min_fps` | float | `0.2` | 0.01-10.0 | Minimum frame rate. |
| `link_budget_bytes_sec` | int | `8000` | 100-1000000 | Available telemetry bandwidth in bytes/sec. |

## [tak]

TAK/ATAK integration. See [TAK Integration](tak-integration.md).

| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `enabled` | bool | `false` | -- | Enable TAK CoT output. |
| `callsign` | string | `HYDRA-1` | -- | Vehicle callsign. Used in TAK markers, STATUSTEXT, logs, and dashboard title. |
| `multicast_group` | string | `239.2.3.1` | -- | TAK multicast group address. |
| `multicast_port` | int | `6969` | 1-65535 | TAK multicast port. |
| `unicast_targets` | string | *(empty)* | -- | Comma-separated `host:port` unicast targets. |
| `emit_interval` | float | `2.0` | 0.1-60.0 | Seconds between detection CoT events. |
| `sa_interval` | float | `5.0` | 0.1-60.0 | Seconds between self-SA position events. |
| `stale_detection` | float | `60.0` | 1.0-3600.0 | Detection marker stale time in seconds. |
| `stale_sa` | float | `30.0` | 1.0-3600.0 | Self-SA stale time in seconds. |
| `advertise_host` | string | *(empty)* | -- | IP address advertised in TAK video feed links. Set to the Jetson IP reachable by ATAK devices. |
| `listen_commands` | bool | `false` | -- | Listen for incoming TAK command messages. |
| `listen_port` | int | `6969` | 1-65535 | UDP port for TAK command listener. |
| `allowed_callsigns` | string | *(empty)* | -- | Comma-separated callsigns allowed to send commands. Empty means all TAK commands are disabled (fail-closed). |
| `command_hmac_secret` | string | *(empty)* | -- | Shared secret for HMAC-SHA256 verification on TAK commands. |

## Vehicle Profiles

Vehicle-specific overrides live in `[vehicle.<name>]` sections. Activate with `--vehicle <name>` or `HYDRA_VEHICLE=<name>`.

Keys use dotted notation: `section.option` overrides the matching base section.

```ini
[vehicle.drone]
reserved_channels = 1,2,3,4
autonomous.post_drop_mode = DOGLEG_RTL
autonomous.post_strike_mode = LOITER

[vehicle.usv]
reserved_channels = 1,3
autonomous.post_drop_mode = SMART_RTL
autonomous.post_strike_mode = LOITER

[vehicle.ugv]
reserved_channels = 1,3
autonomous.post_drop_mode = SMART_RTL
autonomous.post_strike_mode = HOLD

[vehicle.fw]
autonomous.post_action_mode = LOITER
autonomous.min_track_frames = 2
```

`reserved_channels` prevents servo tracking from using motor output channels. If a servo channel conflicts with a reserved channel, Hydra disables servo tracking and logs a CRITICAL safety message.
