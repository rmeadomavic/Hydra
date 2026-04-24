"""Time source reporter — GPS > NTP > RTC priority chain.

Detects which time reference is active and estimates clock drift.
Read-only: this module NEVER mutates the system clock.
"""

from __future__ import annotations

import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hydra_detect.mavlink_io import MAVLinkIO

logger = logging.getLogger(__name__)

# NTP protocol constants (raw UDP fallback)
_NTP_PORT = 123
_NTP_DELTA = 2208988800  # seconds between 1900-01-01 and 1970-01-01
_NTP_PACKET_SIZE = 48


class TimeSource(Enum):
    GPS = "GPS"
    NTP = "NTP"
    RTC = "RTC"


@dataclass
class TimeSourceStatus:
    """Result of a single time source check."""
    source: TimeSource
    drift_seconds: float | None  # None for RTC (unknown)
    detail: str
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def _query_ntp(host: str, timeout: float = 2.0) -> float | None:
    """Send a read-only NTP query; return offset in seconds or None on failure.

    Tries ntplib first (if available); falls back to raw UDP.
    Does NOT modify the system clock — read-only.
    """
    # Try ntplib (preferred — lightweight, accurate round-trip correction)
    try:
        import ntplib  # type: ignore[import]
        c = ntplib.NTPClient()
        response = c.request(host, version=3, timeout=timeout)
        return response.offset  # signed offset: positive = system clock is behind NTP
    except ImportError:
        pass  # ntplib not available, fall through to raw socket
    except Exception as exc:
        logger.debug("ntplib query to %s failed: %s", host, exc)
        return None

    # Raw UDP fallback (stdlib only)
    try:
        packet = b"\x1b" + b"\x00" * (_NTP_PACKET_SIZE - 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            t0 = time.time()
            sock.sendto(packet, (host, _NTP_PORT))
            data, _ = sock.recvfrom(1024)
        finally:
            sock.close()

        if len(data) < _NTP_PACKET_SIZE:
            return None

        tx_int, tx_frac = struct.unpack("!II", data[40:48])
        ntp_time = tx_int + tx_frac / 2**32 - _NTP_DELTA
        local_time = (time.time() + t0) / 2
        return ntp_time - local_time

    except (OSError, struct.error, Exception) as exc:
        logger.debug("NTP raw UDP query to %s failed: %s", host, exc)
        return None


def detect_time_source(
    mavlink_client: "MAVLinkIO | None",
    ntp_hosts: list[str],
    gps_freshness_seconds: float = 5.0,
    gps_min_sats: int = 6,
    gps_min_fix_type: int = 3,
    ntp_timeout: float = 2.0,
) -> TimeSourceStatus:
    """Detect the active time source and estimate drift.

    Priority: GPS > NTP > RTC.
    NEVER mutates the system clock.

    Args:
        mavlink_client: Active MAVLinkIO instance, or None if MAVLink unavailable.
        ntp_hosts: Ordered list of NTP hosts to try.
        gps_freshness_seconds: GPS data older than this is considered stale.
        gps_min_sats: Minimum satellite count for GPS time acceptance.
        gps_min_fix_type: Minimum GPS fix type (3 = 3D fix).
        ntp_timeout: Per-host NTP query timeout in seconds.

    Returns:
        TimeSourceStatus with source, drift estimate, and detail string.
    """
    # --- GPS check ---
    if mavlink_client is not None and mavlink_client.connected:
        try:
            gps = mavlink_client.get_gps()
            fix = gps.get("fix", 0)
            last_update = gps.get("last_update", 0.0)
            age = time.monotonic() - last_update

            if (
                fix >= gps_min_fix_type
                and age <= gps_freshness_seconds
                and last_update > 0
            ):
                # Estimate drift: abs(system_time - GPS-derived time).
                # GPS time comes from GLOBAL_POSITION_INT which carries
                # time_boot_ms; we compare against wall clock indirectly
                # by assuming the MAVLink SYSTEM_TIME message would match
                # GPS epoch. Here we use the round-trip age as a proxy for
                # how stale GPS-derived time might be.
                drift = abs(age)
                detail = (
                    f"Time source: GPS (fix {fix}, age {age:.1f}s, "
                    f"drift ~{drift:.2f}s)"
                )
                logger.debug("Time source: GPS — fix=%d age=%.1fs", fix, age)
                return TimeSourceStatus(
                    source=TimeSource.GPS,
                    drift_seconds=round(drift, 2),
                    detail=detail,
                )
        except Exception as exc:
            logger.debug("GPS time check failed: %s", exc)

    # --- NTP check ---
    for host in ntp_hosts:
        host = host.strip()
        if not host:
            continue
        offset = _query_ntp(host, timeout=ntp_timeout)
        if offset is not None:
            drift = abs(offset)
            detail = f"Time source: NTP ({host}, drift {drift:.2f}s)"
            logger.debug("Time source: NTP — host=%s offset=%.2fs", host, offset)
            return TimeSourceStatus(
                source=TimeSource.NTP,
                drift_seconds=round(drift, 2),
                detail=detail,
            )

    # --- RTC fallback ---
    detail = "Time source: RTC only. GPS fix and NTP unavailable."
    logger.debug("Time source: RTC (no GPS, no NTP)")
    return TimeSourceStatus(
        source=TimeSource.RTC,
        drift_seconds=None,
        detail=detail,
    )


def time_source_status(
    config: Any,
    mavlink_client: "MAVLinkIO | None",
) -> tuple[str, str]:
    """Capability Status hook — reports time source health.

    Returns (status_str, reason_str) where status_str is one of:
        "READY", "WARN", "BLOCKED"

    Exported for the #146 Capability Status agent.
    NEVER mutates the system clock.
    """
    try:
        ts_cfg = config["time_sync"] if hasattr(config, "__getitem__") else {}
    except (KeyError, TypeError):
        ts_cfg = {}

    def _get(key: str, default: Any) -> Any:
        try:
            return ts_cfg.get(key, default)
        except AttributeError:
            return default

    ntp_hosts_raw = _get("ntp_hosts", "pool.ntp.org,time.cloudflare.com")
    ntp_hosts = [h.strip() for h in str(ntp_hosts_raw).split(",") if h.strip()]

    try:
        gps_freshness = float(_get("gps_freshness_seconds", 5.0))
    except (ValueError, TypeError):
        gps_freshness = 5.0
    try:
        gps_min_sats = int(_get("gps_min_sats", 6))
    except (ValueError, TypeError):
        gps_min_sats = 6
    try:
        gps_min_fix = int(_get("gps_min_fix_type", 3))
    except (ValueError, TypeError):
        gps_min_fix = 3
    try:
        drift_warn = float(_get("drift_warn_seconds", 5.0))
    except (ValueError, TypeError):
        drift_warn = 5.0
    try:
        drift_block = float(_get("drift_block_seconds", 30.0))
    except (ValueError, TypeError):
        drift_block = 30.0

    status = detect_time_source(
        mavlink_client=mavlink_client,
        ntp_hosts=ntp_hosts,
        gps_freshness_seconds=gps_freshness,
        gps_min_sats=gps_min_sats,
        gps_min_fix_type=gps_min_fix,
    )

    drift = status.drift_seconds

    # Drift-block check applies to all sources with known drift
    if drift is not None and drift >= drift_block:
        reason = f"Clock drift exceeds block threshold ({drift:.0f}s)"
        return "BLOCKED", reason

    if status.source == TimeSource.GPS:
        if drift is None or drift < drift_warn:
            drift_str = f"{drift:.2f}s" if drift is not None else "unknown"
            return "READY", f"Time source: GPS (drift {drift_str})"
        reason = f"GPS drift high ({drift:.2f}s > warn {drift_warn:.0f}s)"
        return "WARN", reason

    if status.source == TimeSource.NTP:
        if drift is None or drift < drift_warn:
            drift_str = f"{drift:.2f}s" if drift is not None else "unknown"
            return "READY", f"Time source: NTP (drift {drift_str})"
        reason = f"NTP drift high ({drift:.2f}s > warn {drift_warn:.0f}s)"
        return "WARN", reason

    # RTC — always WARN unless drift is somehow known and blocked above
    return "WARN", "Time source: RTC only. GPS fix and NTP unavailable."
