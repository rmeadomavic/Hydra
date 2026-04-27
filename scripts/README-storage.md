# Storage Rotation

Hydra accumulates detection logs, video crops, mission bundles, TAK audit files,
and feedback crops under `output_data/`. On a Jetson with a 64 GB or 128 GB SD
card, weeks of continuous sorties fill the disk. When the disk fills, Hydra dies.

This tool deletes old files by category, logs every removal to an audit trail,
and surfaces a Capability Status signal before you run out of space.

## Configuration

All settings live in `config.ini` under `[storage]`:

```ini
[storage]
retention_detection_logs_days = 365
retention_mission_bundles_days = 90
retention_video_crops_days = 30
retention_tak_audit_days = 90
retention_feedback_crops_days = 90
disk_warn_pct = 15
disk_block_pct = 5
retention_floor_days = 7
retention_ceiling_days = 730
```

`retention_floor_days` is a hard safety belt. No file younger than this is ever
deleted, regardless of other settings. Default is 7 days.

## Manual run

Preview what would be removed (dry-run, no files deleted):

```bash
python scripts/storage_rotation.py
```

Apply — actually delete expired files:

```bash
python scripts/storage_rotation.py --apply
```

The audit log is written to `output_data/storage_rotation.log` after every
`--apply` run (one JSONL line per run).

## Systemd timer (daily, not auto-installed)

The timer unit runs `--apply` once per day at a random time within the first
30 minutes after midnight (to spread load across multiple Jetsons in a team bay).

Copy the units to the correct location and enable:

```bash
sudo cp scripts/hydra-storage-rotation.service /etc/systemd/system/
sudo cp scripts/hydra-storage-rotation.timer    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-storage-rotation.timer
```

Verify it is scheduled:

```bash
systemctl list-timers hydra-storage-rotation.timer
```

Check the last run:

```bash
journalctl -u hydra-storage-rotation.service --since yesterday
```

Do NOT enable the timer automatically from a script or Docker entrypoint.
Operators enable it after confirming retention settings are correct for their
sortie tempo.

## Safety guarantees

- Dry-run is the default. `--apply` is required to delete anything.
- `retention_floor_days` is enforced in code, not config. Even if config is
  corrupted or overridden, no file younger than the floor is deleted.
- Files are only deleted from `output_data/`. Symlinks that resolve outside
  `output_data/` are skipped and logged.
- Every deletion run writes a JSONL audit entry to `output_data/storage_rotation.log`.
- A partial run (one file delete fails mid-loop) logs the error and continues.
  No exception propagates past the runner.
- Retention values below the floor or above the ceiling are clamped with a
  warning at startup. The run proceeds with the clamped values.

## Disk status gate

`hydra_detect.storage_rotation.disk_status(cfg, root)` returns one of:

- `READY` — disk free above warn threshold.
- `WARN` — disk free below `disk_warn_pct`.
- `BLOCKED` — disk free below `disk_block_pct`.

The Capability Status page (#146) imports this function. At boot, Hydra calls
`check_disk_at_boot()` which logs a loud WARNING if the disk is BLOCKED.
