# Post-Mission Review

Hydra records every detection and operator action during a mission. After the mission, review the data on a map, verify log integrity, and export reports.

## Detection Logs

Detection events are written to JSONL (default) or CSV files in the configured `log_dir`.

### JSONL Format

Each line is a JSON object:

```json
{
  "timestamp": "2026-03-28T14:32:05.123Z",
  "frame": 1542,
  "track_id": 5,
  "label": "person",
  "class_id": 0,
  "confidence": 0.87,
  "x1": 120, "y1": 80, "x2": 210, "y2": 320,
  "lat": 34.0527, "lon": -118.2437, "alt": 15.2,
  "fix": 3,
  "chain_hash": "a1b2c3d4..."
}
```

### Chain-of-Custody Hash

Every log entry includes a `chain_hash` field. This is a SHA-256 hash of the current record's content concatenated with the previous record's hash. The first record uses a genesis hash of 64 zeros.

This creates a tamper-evident chain. If any record is modified, deleted, or inserted, the hash chain breaks.

The model file hash is recorded at the start of each log session to document which model produced the detections.

## Log Integrity Verification

Verify a log file's hash chain:

```bash
python -m hydra_detect.verify_log output_data/logs/detections_001.jsonl
```

Output:
```
OK -- 1542 records verified, chain intact.
```

If a record has been modified:
```
FAIL -- Line 847: hash mismatch (chain broken).
```

The verifier tolerates a truncated final record (e.g., power loss mid-write). It reports the chain as valid up to the last complete record.

## Event Timeline

The event logger records a parallel timeline of operator actions and vehicle telemetry.

### Event Types

| Type | Content |
|------|---------|
| `mission_start` | Mission name and start time |
| `mission_end` | Mission name and end time |
| `action` | Operator action: lock, unlock, follow, strike, abort, mode_change |
| `track` | Vehicle GPS position at 1 Hz (lat, lon, alt, heading, speed, mode) |
| `state` | System state changes: camera_lost, low_light |
| `detection` | Detection event with track ID, label, confidence, GPS |

### Mission Tagging

Bracket a mission with start and end markers:

```
POST /api/mission/start  {"name": "patrol-alpha"}
... mission runs ...
POST /api/mission/end
```

The event logger opens a new file for each mission: `HYDRA-1_20260328_143200_patrol-alpha.jsonl`.

Events outside a mission bracket go to a `default` session file.

## Review Page

<!-- TODO: Screenshot -- review-map.png -->

The web dashboard at `/review` provides a map-based review interface.

### Features

- **Detection markers**: Each detection with GPS coordinates appears on an OpenStreetMap base layer
- **Marker info**: Click a marker to see label, confidence, timestamp, track ID
- **Confidence filter**: Slider to filter markers by minimum confidence
- **Vehicle track**: Line showing the vehicle's path from event timeline data
- **Event markers**: Operator actions shown as labeled points on the track
- **Timeline slider**: Scrub through the mission to see the vehicle's position at any time
- **Log file selector**: Choose from available detection logs and event timelines
- **Image viewer**: Click a detection to see the saved JPEG snapshot (if `save_images` was enabled)

### Map Replay

Load an event timeline file to see the full mission replay:

1. Select an event timeline from the dropdown (files with `mission_start` events)
2. The vehicle track appears as a line on the map
3. Detection markers appear at their GPS positions
4. Operator actions appear as labeled icons along the track
5. Use the timeline slider to step through the mission

## Export

### ZIP Download

Export all logs and images from the current session:

```
GET /api/export
```

Returns a ZIP file containing:
- `logs/` -- all detection log files (JSONL/CSV)
- `images/` -- all saved detection snapshots

Requires API token authentication.

### GeoJSON Export

The review page can export detection data as GeoJSON for use in GIS tools.

### Standalone HTML Report

Generate a self-contained HTML report with embedded map:

```bash
python -m hydra_detect.review_export output_data/logs/detections.jsonl -o report.html
```

The report includes:
- OpenStreetMap base layer
- Detection markers with popups
- Optional embedded images (base64-encoded)
- No external dependencies, works offline

Options:
```
python -m hydra_detect.review_export <logfile> [OPTIONS]

  -o, --output PATH       Output HTML file (default: report.html)
  --images-dir PATH       Directory with saved detection images
```

## Log File Management

Detection logs rotate automatically based on `max_log_size_mb` and `max_log_files`. When the active log exceeds the size limit, a new file is created with an incremented suffix (`detections_001.jsonl`, `detections_002.jsonl`, etc.). The oldest files beyond the retention limit are deleted.

If `wipe_on_start = true` in `[logging]`, all previous session data is deleted when the pipeline starts. Use this for OPSEC-sensitive deployments where persistent logs are unacceptable.

All file I/O is handled by a background writer thread to keep the detection loop fast.
