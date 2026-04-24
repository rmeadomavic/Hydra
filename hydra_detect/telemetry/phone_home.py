"""Phone-home health telemetry — unit side only.

Sends a small operational health payload to a configured collector URL once
per run (or on a schedule when driven by the systemd timer). Default OFF.

What IS in the payload:
  callsign, hostname, version, channel, uptime_hours, mode,
  capability_summary (READY/WARN/BLOCKED counts), last_mission_at,
  disk_free_pct, cpu_temp_c, power_mode, last_update_status

What is NOT in the payload — by design and enforced by test_phone_home_privacy.py:
  GPS coordinates, video frames, image crops, operator names, IP addresses,
  MAVLink system IDs, detection class details, or any personally identifying info.

Collector side is not defined yet — Kyle has not decided where it lives.
"""

from __future__ import annotations

import json
import logging
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Bounded queue cap — keep only the most recent N payloads on disk.
_QUEUE_MAX = 30


@dataclass
class SendResult:
    """Result of a single HTTP send attempt. Never raises."""
    ok: bool
    status_code: int | None
    error: str | None


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def _read_version(root: Path) -> str | None:
    """Read __version__ from the installed package or source tree."""
    try:
        from hydra_detect import __version__  # type: ignore[import]
        return __version__
    except Exception:
        pass
    # Fallback: parse __init__.py directly
    try:
        init = root / "hydra_detect" / "__init__.py"
        for line in init.read_text().splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return None


def _disk_free_pct(root: Path) -> float | None:
    """Return disk free percentage for the filesystem containing root."""
    try:
        usage = shutil.disk_usage(root)
        if usage.total == 0:
            return None
        return round(usage.free / usage.total * 100.0, 1)
    except Exception:
        return None


def _cpu_temp_c() -> float | None:
    """Read CPU thermal zone temperature from sysfs."""
    try:
        from hydra_detect.system import read_thermal
        return read_thermal("0")
    except Exception:
        pass
    # Direct sysfs fallback — avoids importing system on hosts without it.
    try:
        raw = Path("/sys/devices/virtual/thermal/thermal_zone0/temp").read_text().strip()
        return round(int(raw) / 1000.0, 1)
    except Exception:
        return None


def _power_mode() -> str | None:
    """Read Jetson power mode synchronously."""
    try:
        from hydra_detect.system import query_nvpmodel_sync
        return query_nvpmodel_sync()
    except Exception:
        return None


def _last_mission_at(root: Path) -> str | None:
    """Return ISO timestamp of the most recent detection log file modification.

    Scans output_data/logs/ for JSONL files.  Returns None if no logs exist.
    Deliberately does NOT read log contents — just the file mtime.
    """
    log_dir = root / "output_data" / "logs"
    try:
        candidates = list(log_dir.glob("*.jsonl")) + list(log_dir.glob("*.csv"))
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        ts = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
        return ts.isoformat()
    except Exception:
        return None


def _last_update_status(root: Path) -> str | None:
    """Read the last git pull / deploy status from output_data/update_status.txt.

    The deploy script writes one line to this file after each pull.
    Returns None if the file doesn't exist.
    """
    try:
        status_file = root / "output_data" / "update_status.txt"
        if not status_file.exists():
            return None
        text = status_file.read_text().strip()
        # Return only the first 120 chars to keep payloads small.
        return text[:120] if text else None
    except Exception:
        return None


def _capability_summary(root: Path) -> dict:
    """Count READY/WARN/BLOCKED from the #171 capability evaluator if available.

    Returns an empty dict when the evaluator is not present — structured
    absence beats a missing key.
    """
    try:
        # Import lazily — capability evaluator is from PR #171 which may not
        # be merged yet. Graceful degradation when absent.
        from hydra_detect.observability import capabilities  # type: ignore[import]
        report = capabilities.evaluate()
        counts: dict[str, int] = {}
        for cap in report.values():
            status = str(cap.get("status", "")).upper()
            counts[status] = counts.get(status, 0) + 1
        return counts
    except Exception:
        return {}


def _uptime_hours(root: Path) -> float | None:
    """Return system uptime in hours from /proc/uptime."""
    try:
        text = Path("/proc/uptime").read_text().strip()
        seconds = float(text.split()[0])
        return round(seconds / 3600.0, 2)
    except Exception:
        return None


def _channel(cfg: Any) -> str | None:
    """Read the configured deployment channel (stable/dev/etc.) if present."""
    try:
        return cfg.get("telemetry", "channel", fallback=None) or None
    except Exception:
        return None


def build_payload(cfg: Any, root: Path) -> dict:
    """Assemble the health telemetry payload.

    Args:
        cfg: ConfigParser instance with the loaded config.
        root: Repo / working directory root (used to locate output_data/).

    Returns:
        A dict safe to serialise as JSON.  All keys always present — missing
        signals are represented as None rather than omitting the key.

    Privacy guarantee: GPS, video, crops, MAVLink system IDs, and operator
    identifying info are never included.  See test_phone_home_privacy.py.
    """
    callsign: str | None = None
    mode: str | None = None
    try:
        callsign = cfg.get("tak", "callsign", fallback=None) or None
        mode = cfg.get("telemetry", "mode", fallback=None) or None
    except Exception:
        pass

    return {
        "callsign": callsign,
        "hostname": socket.gethostname(),
        "version": _read_version(root),
        "channel": _channel(cfg),
        "uptime_hours": _uptime_hours(root),
        "mode": mode,
        "capability_summary": _capability_summary(root),
        "last_mission_at": _last_mission_at(root),
        "disk_free_pct": _disk_free_pct(root),
        "cpu_temp_c": _cpu_temp_c(),
        "power_mode": _power_mode(),
        "last_update_status": _last_update_status(root),
    }


# ---------------------------------------------------------------------------
# HTTP send
# ---------------------------------------------------------------------------

def send_payload(
    url: str,
    payload: dict,
    api_token: str,
    timeout: float = 10,
) -> SendResult:
    """POST payload as JSON to url with Bearer auth.

    Never raises.  Network errors, timeouts, and HTTP failures all return a
    structured SendResult with ok=False and a descriptive error string.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_token}",
            "User-Agent": f"hydra-phone-home/{payload.get('version', 'unknown')}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return SendResult(ok=True, status_code=resp.status, error=None)
    except urllib.error.HTTPError as exc:
        return SendResult(ok=False, status_code=exc.code, error=str(exc.reason))
    except urllib.error.URLError as exc:
        return SendResult(ok=False, status_code=None, error=str(exc.reason))
    except TimeoutError:
        return SendResult(ok=False, status_code=None, error="connection timed out")
    except Exception as exc:  # pragma: no cover — defensive
        return SendResult(ok=False, status_code=None, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Local queue (send-on-failure / offline buffer)
# ---------------------------------------------------------------------------

def _queue_dir(root: Path) -> Path:
    return root / "output_data" / "telemetry" / "queue"


def queue_payload(root: Path, payload: dict) -> None:
    """Write payload to the local queue directory.

    Bounded to _QUEUE_MAX files — oldest are evicted when the cap is exceeded.
    Filenames are ISO timestamps so sort order == chronological order.
    """
    q = _queue_dir(root)
    try:
        q.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        dest = q / f"{ts}.json"
        dest.write_text(json.dumps(payload, indent=2))
        logger.info("phone-home: payload queued at %s", dest)
        _evict_queue(q)
    except Exception as exc:
        logger.warning("phone-home: failed to write queue entry: %s", exc)


def _evict_queue(q: Path) -> None:
    """Keep only the _QUEUE_MAX most recent files in q; delete the rest."""
    try:
        files = sorted(q.glob("*.json"))
        excess = len(files) - _QUEUE_MAX
        if excess > 0:
            for old in files[:excess]:
                old.unlink(missing_ok=True)
                logger.debug("phone-home: evicted old queue entry %s", old.name)
    except Exception as exc:
        logger.warning("phone-home: queue eviction error: %s", exc)


# ---------------------------------------------------------------------------
# Queue flush
# ---------------------------------------------------------------------------

def flush_queue(
    root: Path,
    url: str,
    api_token: str,
    max_batch: int = 10,
) -> None:
    """Send queued payloads in chronological order.

    Stops on the first send failure — the queue is preserved and will be
    retried on the next scheduled run.  Successfully sent files are removed.

    Args:
        root: Repo root (same as build_payload).
        url: Collector URL.
        api_token: Bearer token.
        max_batch: Maximum number of queued payloads to send per run.
    """
    q = _queue_dir(root)
    if not q.exists():
        return

    pending = sorted(q.glob("*.json"))[:max_batch]
    if not pending:
        return

    logger.info("phone-home: flushing %d queued payload(s)", len(pending))

    for entry in pending:
        try:
            payload = json.loads(entry.read_text())
        except Exception as exc:
            logger.warning("phone-home: skipping corrupt queue entry %s: %s", entry.name, exc)
            entry.unlink(missing_ok=True)
            continue

        result = send_payload(url, payload, api_token)
        if result.ok:
            entry.unlink(missing_ok=True)
            logger.info("phone-home: flushed %s (HTTP %s)", entry.name, result.status_code)
        else:
            logger.warning(
                "phone-home: flush send failed for %s: %s — stopping batch",
                entry.name,
                result.error,
            )
            break  # Stop on first failure; retry next run.
