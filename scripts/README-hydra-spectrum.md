# Hydra Spectrum Daemon

Feeds live RTL-SDR power sweeps to the Ops dashboard SDR cell. Runs
`rtl_power` in a loop, computes a noise floor and peak set, and writes
the result as JSON to `/tmp/hydra_spectrum.json`. The web server reads
that file via `GET /api/rf/spectrum` and the dashboard JS overlay
paints real spectrum bars at 1 Hz.

If this daemon isn't running (or no SDR is plugged in), the endpoint
returns `{enabled: false}` and the dashboard skips rendering. The
detection pipeline is unaffected.

## Install

```bash
# Prereq: rtl-sdr package (provides rtl_power, rtl_test, etc.)
sudo apt install -y rtl-sdr

# Verify the dongle is detected
rtl_test -t

# Install the systemd unit
sudo cp scripts/hydra-spectrum.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hydra-spectrum.service

# Tail logs
journalctl -u hydra-spectrum -f
```

## Configure

Override defaults by creating `/etc/hydra/spectrum.env`:

```ini
HYDRA_SPECTRUM_FREQ=915          # preset: 433 / 868 / 915 / 2400
HYDRA_SPECTRUM_INTERVAL=1.0      # seconds between sweeps
HYDRA_SPECTRUM_OUTPUT=/tmp/hydra_spectrum.json
```

For non-preset ranges, run the script directly with `--start` / `--stop`
and disable the systemd unit:

```bash
python3 scripts/hydra_spectrum_daemon.py --start 5650 --stop 5950 --step-khz 250
```

## Output Schema

```json
{
    "freq_low_mhz": 2400,
    "freq_high_mhz": 2500,
    "noise_floor_dbm": -42.7,
    "threshold_dbm": -32.7,
    "bins": [[2400.0, -45.1], [2400.1, -44.8]],
    "peaks": [{"freq_mhz": 2462.0, "dbm": -28.4}],
    "sweep_count": 47,
    "status": "ok"
}
```

`status` is one of:
- `ok` — fresh sweep
- `no_sdr` — `rtl_power` produced no data (dongle unplugged or busy)
- `error` — `rtl_power` failed; see the `error` field for details

The endpoint always wraps this with `enabled: true` and `file_age_s`.

## Conflicts

`rtl_power` opens the dongle exclusively. Don't run this daemon
alongside Kismet, `rtl_433`, or `scripts/rf_power_scan.py` against the
same dongle. The Hydra RF Hunt subsystem uses Kismet, which conflicts
with this — run one or the other per dongle.
