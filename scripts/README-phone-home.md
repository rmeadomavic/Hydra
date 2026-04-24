# Hydra Phone-Home Telemetry — Operator Enable Guide

## What it does

Sends a small health snapshot to a central collector once per day so Kyle can
tell whether a unit at an operator's home is alive, what version it's running,
and whether anything is wrong — without any access to video, detections, or
operator location.

**Default: OFF.** Nothing is sent unless you explicitly enable it.

## What is in the payload

| Field | Description |
|---|---|
| `callsign` | TAK callsign from config.ini (e.g. HYDRA-1) |
| `hostname` | OS hostname of the Jetson |
| `version` | Hydra software version string |
| `channel` | Optional deployment channel label |
| `uptime_hours` | System uptime in hours |
| `mode` | Optional mode label |
| `capability_summary` | Count of READY/WARN/BLOCKED subsystems |
| `last_mission_at` | Timestamp of the most recent detection log file |
| `disk_free_pct` | Free disk percentage |
| `cpu_temp_c` | CPU temperature °C |
| `power_mode` | Jetson nvpmodel power mode |
| `last_update_status` | Last deploy result from output_data/update_status.txt |

## What is NOT in the payload

- GPS coordinates
- Video frames or thumbnails
- Detection images or crops
- Operator names or identifying info
- MAVLink system IDs
- IP addresses

## Enable

1. Edit `/home/sorcc/Hydra/config.ini` and set:

```ini
[telemetry]
enabled = true
collector_url = https://collector.example.com/api/ingest
api_token = <token Kyle provides>
opt_out = false
```

2. Copy the systemd units:

```bash
sudo cp /home/sorcc/Hydra/scripts/hydra-phone-home.service /etc/systemd/system/
sudo cp /home/sorcc/Hydra/scripts/hydra-phone-home.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-phone-home.timer
```

3. Verify:

```bash
sudo systemctl status hydra-phone-home.timer
sudo systemctl start hydra-phone-home.service
journalctl -u hydra-phone-home.service -n 50
```

## Disable

```bash
sudo systemctl disable --now hydra-phone-home.timer
```

Or set `enabled = false` in config.ini.

## Dry run (no network required)

```bash
python3 /home/sorcc/Hydra/scripts/phone_home.py --dry-run
```

Prints the JSON payload that would be sent. Safe to run any time.

## Offline queue

If a send fails (no network, collector down), the payload is saved to
`output_data/telemetry/queue/`. The next successful send flushes up to 10
queued payloads in order. Queue is capped at 30 entries — oldest are evicted
automatically.
