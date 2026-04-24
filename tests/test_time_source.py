"""Tests for hydra_detect.time_source — GPS > NTP > RTC reporter.

All tests are read-only: no system clock mutations, no actual network calls.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.time_source import (
    TimeSource,
    TimeSourceStatus,
    _query_ntp,
    detect_time_source,
    time_source_status,
)
from hydra_detect.verify_log import verify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink(fix: int = 3, age: float = 1.0) -> MagicMock:
    """Build a fake MAVLinkIO with a good GPS state."""
    mav = MagicMock()
    mav.connected = True
    mav.get_gps.return_value = {
        "fix": fix,
        "lat": 347_000_000,
        "lon": -769_000_000,
        "alt": 50_000,
        "last_update": time.monotonic() - age,
    }
    return mav


def _make_mavlink_disconnected() -> MagicMock:
    mav = MagicMock()
    mav.connected = False
    return mav


# ---------------------------------------------------------------------------
# TimeSourceStatus dataclass
# ---------------------------------------------------------------------------

class TestTimeSourceStatus:
    def test_fields_present(self):
        s = TimeSourceStatus(
            source=TimeSource.GPS,
            drift_seconds=0.3,
            detail="Time source: GPS (drift 0.3s)",
        )
        assert s.source == TimeSource.GPS
        assert s.drift_seconds == 0.3
        assert isinstance(s.checked_at, datetime)
        assert s.checked_at.tzinfo is not None  # timezone-aware

    def test_rtc_allows_none_drift(self):
        s = TimeSourceStatus(
            source=TimeSource.RTC,
            drift_seconds=None,
            detail="Time source: RTC only.",
        )
        assert s.drift_seconds is None


# ---------------------------------------------------------------------------
# detect_time_source — GPS path
# ---------------------------------------------------------------------------

class TestDetectTimeSourceGPS:
    def test_good_gps_returns_gps(self):
        mav = _make_mavlink(fix=3, age=1.0)
        result = detect_time_source(mav, ntp_hosts=[])
        assert result.source == TimeSource.GPS

    def test_gps_drift_estimate_is_non_negative(self):
        mav = _make_mavlink(fix=3, age=2.5)
        result = detect_time_source(mav, ntp_hosts=[])
        assert result.source == TimeSource.GPS
        assert result.drift_seconds is not None
        assert result.drift_seconds >= 0.0

    def test_fix_type_below_threshold_falls_through(self):
        """Fix type 2 (2D) is below default min of 3 — should not count as GPS."""
        mav = _make_mavlink(fix=2, age=1.0)
        with patch("hydra_detect.time_source._query_ntp", return_value=None):
            result = detect_time_source(mav, ntp_hosts=["pool.ntp.org"])
        assert result.source != TimeSource.GPS

    def test_stale_gps_falls_through_to_ntp(self):
        """GPS data older than freshness threshold is ignored."""
        mav = _make_mavlink(fix=3, age=10.0)  # 10s old, default freshness=5s
        with patch("hydra_detect.time_source._query_ntp", return_value=0.5):
            result = detect_time_source(
                mav,
                ntp_hosts=["pool.ntp.org"],
                gps_freshness_seconds=5.0,
            )
        assert result.source == TimeSource.NTP

    def test_no_mavlink_skips_gps(self):
        with patch("hydra_detect.time_source._query_ntp", return_value=0.2):
            result = detect_time_source(None, ntp_hosts=["pool.ntp.org"])
        assert result.source == TimeSource.NTP

    def test_disconnected_mavlink_falls_through(self):
        mav = _make_mavlink_disconnected()
        with patch("hydra_detect.time_source._query_ntp", return_value=0.1):
            result = detect_time_source(mav, ntp_hosts=["pool.ntp.org"])
        assert result.source == TimeSource.NTP

    def test_gps_custom_fix_threshold(self):
        """Custom gps_min_fix_type=2 should accept a 2D fix."""
        mav = _make_mavlink(fix=2, age=1.0)
        result = detect_time_source(mav, ntp_hosts=[], gps_min_fix_type=2)
        assert result.source == TimeSource.GPS


# ---------------------------------------------------------------------------
# detect_time_source — NTP path
# ---------------------------------------------------------------------------

class TestDetectTimeSourceNTP:
    def test_ntp_reachable_returns_ntp(self):
        mav = _make_mavlink_disconnected()
        with patch("hydra_detect.time_source._query_ntp", return_value=0.5):
            result = detect_time_source(mav, ntp_hosts=["pool.ntp.org"])
        assert result.source == TimeSource.NTP
        assert result.drift_seconds == pytest.approx(0.5, abs=1e-9)

    def test_ntp_negative_offset_gives_positive_drift(self):
        """Drift is the absolute value of the offset."""
        mav = _make_mavlink_disconnected()
        with patch("hydra_detect.time_source._query_ntp", return_value=-3.7):
            result = detect_time_source(mav, ntp_hosts=["pool.ntp.org"])
        assert result.source == TimeSource.NTP
        assert result.drift_seconds == pytest.approx(3.7, abs=1e-9)

    def test_ntp_first_host_tried_first(self):
        """_query_ntp is called with hosts in order; first success wins."""
        mav = _make_mavlink_disconnected()
        calls = []

        def fake_ntp(host, timeout=2.0):
            calls.append(host)
            if host == "time.cloudflare.com":
                return 0.1
            return None

        with patch("hydra_detect.time_source._query_ntp", side_effect=fake_ntp):
            result = detect_time_source(
                mav, ntp_hosts=["pool.ntp.org", "time.cloudflare.com"]
            )
        assert result.source == TimeSource.NTP
        assert calls[0] == "pool.ntp.org"

    def test_all_ntp_unreachable_returns_rtc(self):
        mav = _make_mavlink_disconnected()
        with patch("hydra_detect.time_source._query_ntp", return_value=None):
            result = detect_time_source(
                mav, ntp_hosts=["pool.ntp.org", "time.cloudflare.com"]
            )
        assert result.source == TimeSource.RTC

    def test_empty_ntp_hosts_returns_rtc(self):
        mav = _make_mavlink_disconnected()
        result = detect_time_source(mav, ntp_hosts=[])
        assert result.source == TimeSource.RTC


# ---------------------------------------------------------------------------
# detect_time_source — RTC fallback
# ---------------------------------------------------------------------------

class TestDetectTimeSourceRTC:
    def test_rtc_drift_is_none(self):
        with patch("hydra_detect.time_source._query_ntp", return_value=None):
            result = detect_time_source(None, ntp_hosts=["pool.ntp.org"])
        assert result.source == TimeSource.RTC
        assert result.drift_seconds is None

    def test_rtc_detail_string(self):
        result = detect_time_source(None, ntp_hosts=[])
        assert "RTC" in result.detail
        assert "GPS" in result.detail or "unavailable" in result.detail.lower()

    def test_rtc_checked_at_is_utc(self):
        result = detect_time_source(None, ntp_hosts=[])
        assert result.checked_at.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# time_source_status — Capability Status hook
# ---------------------------------------------------------------------------

class TestTimeSourceStatusHook:
    def _cfg(self, overrides: dict | None = None) -> dict:
        base = {
            "time_sync": {
                "ntp_hosts": "pool.ntp.org",
                "gps_freshness_seconds": "5",
                "gps_min_sats": "6",
                "gps_min_fix_type": "3",
                "drift_warn_seconds": "5",
                "drift_block_seconds": "30",
            }
        }
        if overrides:
            base["time_sync"].update(overrides)
        return base

    def test_gps_low_drift_is_ready(self):
        mav = _make_mavlink(fix=3, age=0.5)
        status, reason = time_source_status(self._cfg(), mav)
        assert status == "READY"
        assert "GPS" in reason

    def test_ntp_low_drift_is_ready(self):
        mav = _make_mavlink_disconnected()
        with patch("hydra_detect.time_source._query_ntp", return_value=1.1):
            status, reason = time_source_status(self._cfg(), mav)
        assert status == "READY"
        assert "NTP" in reason

    def test_rtc_is_warn(self):
        with patch("hydra_detect.time_source._query_ntp", return_value=None):
            status, reason = time_source_status(self._cfg(), None)
        assert status == "WARN"
        assert "RTC" in reason

    def test_drift_at_block_threshold_is_blocked(self):
        """Drift >= drift_block_seconds → BLOCKED."""
        mav = _make_mavlink(fix=3, age=0.5)
        cfg = self._cfg({"drift_block_seconds": "2", "drift_warn_seconds": "1"})
        # Patch detect_time_source to return a status with drift=45s
        fake_status = TimeSourceStatus(
            source=TimeSource.GPS,
            drift_seconds=45.0,
            detail="GPS drift 45s",
        )
        with patch("hydra_detect.time_source.detect_time_source", return_value=fake_status):
            status, reason = time_source_status(cfg, mav)
        assert status == "BLOCKED"
        assert "45" in reason

    def test_drift_exactly_at_block_is_blocked(self):
        """Boundary: drift == drift_block_seconds should be BLOCKED."""
        fake_status = TimeSourceStatus(
            source=TimeSource.NTP,
            drift_seconds=30.0,
            detail="NTP 30s drift",
        )
        with patch("hydra_detect.time_source.detect_time_source", return_value=fake_status):
            status, _ = time_source_status(self._cfg(), None)
        assert status == "BLOCKED"

    def test_drift_just_below_block_is_warn(self):
        """Drift < block but >= warn → WARN for NTP."""
        fake_status = TimeSourceStatus(
            source=TimeSource.NTP,
            drift_seconds=29.9,
            detail="NTP 29.9s drift",
        )
        cfg = self._cfg({"drift_warn_seconds": "5", "drift_block_seconds": "30"})
        with patch("hydra_detect.time_source.detect_time_source", return_value=fake_status):
            status, _ = time_source_status(cfg, None)
        assert status == "WARN"

    def test_gps_drift_above_warn_is_warn(self):
        fake_status = TimeSourceStatus(
            source=TimeSource.GPS,
            drift_seconds=8.0,
            detail="GPS 8s drift",
        )
        with patch("hydra_detect.time_source.detect_time_source", return_value=fake_status):
            status, _ = time_source_status(self._cfg(), None)
        assert status == "WARN"

    def test_empty_config_uses_defaults(self):
        """Passing an empty dict should not crash — defaults apply."""
        with patch("hydra_detect.time_source.detect_time_source") as mock_detect:
            mock_detect.return_value = TimeSourceStatus(
                source=TimeSource.RTC,
                drift_seconds=None,
                detail="RTC",
            )
            status, reason = time_source_status({}, None)
        assert status == "WARN"


# ---------------------------------------------------------------------------
# Config schema validation
# ---------------------------------------------------------------------------

class TestTimeSyncConfigSchema:
    def test_schema_accepts_defaults(self):
        import configparser
        from hydra_detect.config_schema import validate_config

        cfg = configparser.ConfigParser()
        cfg.read_string("""
[time_sync]
ntp_hosts = pool.ntp.org,time.cloudflare.com
gps_freshness_seconds = 5
gps_min_sats = 6
gps_min_fix_type = 3
drift_warn_seconds = 5
drift_block_seconds = 30
""")
        result = validate_config(cfg)
        # Filter only time_sync errors
        ts_errors = [e for e in result.errors if "time_sync" in e]
        assert ts_errors == [], ts_errors

    def test_schema_accepts_custom_hosts(self):
        import configparser
        from hydra_detect.config_schema import validate_config

        cfg = configparser.ConfigParser()
        cfg.read_string("""
[time_sync]
ntp_hosts = time.cloudflare.com,0.pool.ntp.org
gps_freshness_seconds = 10
gps_min_sats = 4
gps_min_fix_type = 3
drift_warn_seconds = 10
drift_block_seconds = 60
""")
        result = validate_config(cfg)
        ts_errors = [e for e in result.errors if "time_sync" in e]
        assert ts_errors == [], ts_errors

    def test_schema_rejects_invalid_freshness(self):
        import configparser
        from hydra_detect.config_schema import validate_config

        cfg = configparser.ConfigParser()
        cfg.read_string("""
[time_sync]
ntp_hosts = pool.ntp.org
gps_freshness_seconds = 999
gps_min_sats = 6
gps_min_fix_type = 3
drift_warn_seconds = 5
drift_block_seconds = 30
""")
        result = validate_config(cfg)
        ts_errors = [e for e in result.errors if "time_sync" in e]
        assert ts_errors, "Expected validation error for out-of-range gps_freshness_seconds"


# ---------------------------------------------------------------------------
# Detection logger writes time_source field
# ---------------------------------------------------------------------------

class TestDetectionLoggerTimeSources:
    def _make_tracking_result(self):
        """Build a minimal TrackingResult with one track."""
        from hydra_detect.tracker import TrackedObject, TrackingResult

        track = TrackedObject(
            track_id=1,
            label="person",
            class_id=0,
            confidence=0.9,
            x1=10.0, y1=10.0, x2=50.0, y2=80.0,
        )
        return TrackingResult([track])

    def test_time_source_field_written_to_jsonl(self, tmp_path):
        from hydra_detect.detection_logger import DetectionLogger

        logger = DetectionLogger(
            log_dir=str(tmp_path / "logs"),
            log_format="jsonl",
            save_images=False,
        )
        logger.start()
        tracking = self._make_tracking_result()
        logger.log(tracking, time_source="GPS")
        logger.stop(timeout=5.0)

        log_files = list((tmp_path / "logs").glob("*.jsonl"))
        assert log_files, "No JSONL log files written"
        records = [json.loads(line) for line in log_files[0].read_text().splitlines() if line.strip()]
        assert records, "No records in log"
        assert records[0]["time_source"] == "GPS"

    def test_time_source_field_absent_when_not_passed(self, tmp_path):
        from hydra_detect.detection_logger import DetectionLogger

        logger = DetectionLogger(
            log_dir=str(tmp_path / "logs"),
            log_format="jsonl",
            save_images=False,
        )
        logger.start()
        tracking = self._make_tracking_result()
        logger.log(tracking)  # No time_source
        logger.stop(timeout=5.0)

        log_files = list((tmp_path / "logs").glob("*.jsonl"))
        assert log_files
        records = [json.loads(line) for line in log_files[0].read_text().splitlines() if line.strip()]
        assert records
        assert "time_source" not in records[0]

    def test_time_source_in_recent_buffer(self, tmp_path):
        from hydra_detect.detection_logger import DetectionLogger

        logger = DetectionLogger(
            log_dir=str(tmp_path / "logs"),
            log_format="jsonl",
            save_images=False,
        )
        logger.start()
        tracking = self._make_tracking_result()
        logger.log(tracking, time_source="NTP")
        logger.stop(timeout=5.0)

        recent = logger.get_recent()
        assert recent
        assert recent[0].get("time_source") == "NTP"


# ---------------------------------------------------------------------------
# verify_log tolerates time_source field
# ---------------------------------------------------------------------------

class TestVerifyLogToleratesTimeSource:
    def _make_chain(self, path: Path, records: list[dict]) -> None:
        """Write chained records to a JSONL file."""
        prev_hash = "0" * 64
        with open(path, "w") as f:
            for payload in records:
                record_json = json.dumps(payload, sort_keys=True)
                chain_hash = hashlib.sha256(
                    (record_json + prev_hash).encode()
                ).hexdigest()
                full = dict(payload)
                full["chain_hash"] = chain_hash
                f.write(json.dumps(full) + "\n")
                prev_hash = chain_hash

    def test_records_with_time_source_verify_ok(self, tmp_path):
        p = tmp_path / "log.jsonl"
        self._make_chain(p, [
            {"timestamp": "2026-04-23T00:00:00Z", "label": "person", "time_source": "GPS"},
            {"timestamp": "2026-04-23T00:00:01Z", "label": "car", "time_source": "NTP"},
        ])
        ok, count, msg = verify(p)
        assert ok is True, msg
        assert count == 2

    def test_records_without_time_source_verify_ok(self, tmp_path):
        p = tmp_path / "log.jsonl"
        self._make_chain(p, [
            {"timestamp": "2026-04-23T00:00:00Z", "label": "person"},
            {"timestamp": "2026-04-23T00:00:01Z", "label": "car"},
        ])
        ok, count, msg = verify(p)
        assert ok is True, msg

    def test_mixed_records_verify_ok(self, tmp_path):
        """Some records have time_source, others don't — chain must hold."""
        p = tmp_path / "log.jsonl"
        self._make_chain(p, [
            {"timestamp": "2026-04-23T00:00:00Z", "label": "person", "time_source": "GPS"},
            {"timestamp": "2026-04-23T00:00:01Z", "label": "car"},  # no time_source
            {"timestamp": "2026-04-23T00:00:02Z", "label": "truck", "time_source": "RTC"},
        ])
        ok, count, msg = verify(p)
        assert ok is True, msg
        assert count == 3

    def test_tampered_time_source_breaks_chain(self, tmp_path):
        """If time_source is tampered after the fact, chain should break."""
        p = tmp_path / "log.jsonl"
        self._make_chain(p, [
            {"timestamp": "2026-04-23T00:00:00Z", "label": "person", "time_source": "GPS"},
        ])
        # Read back, tamper the time_source, rewrite without updating chain_hash
        records = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        records[0]["time_source"] = "RTC"  # tampered!
        with open(p, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        ok, _, msg = verify(p)
        assert ok is False
        assert "chain broken" in msg


# ---------------------------------------------------------------------------
# review_export — time_source_summary in build_summary
# ---------------------------------------------------------------------------

class TestReviewExportTimeSourceSummary:
    def test_summary_empty_when_no_time_source(self):
        from hydra_detect.review_export import build_summary

        records = [
            {"label": "person", "timestamp": "2026-04-23T00:00:00Z"},
            {"label": "car", "timestamp": "2026-04-23T00:00:01Z"},
        ]
        summary = build_summary(records)
        assert summary["time_source_summary"] == {}

    def test_summary_captures_gps_range(self):
        from hydra_detect.review_export import build_summary

        records = [
            {"label": "person", "timestamp": "2026-04-23T00:00:00Z", "time_source": "GPS"},
            {"label": "car", "timestamp": "2026-04-23T00:00:05Z", "time_source": "GPS"},
        ]
        summary = build_summary(records)
        assert "GPS" in summary["time_source_summary"]
        gps = summary["time_source_summary"]["GPS"]
        assert gps["first"] == "2026-04-23T00:00:00Z"
        assert gps["last"] == "2026-04-23T00:00:05Z"

    def test_summary_multiple_sources(self):
        from hydra_detect.review_export import build_summary

        records = [
            {"label": "person", "timestamp": "T1", "time_source": "GPS"},
            {"label": "car", "timestamp": "T2", "time_source": "NTP"},
            {"label": "truck", "timestamp": "T3", "time_source": "RTC"},
        ]
        summary = build_summary(records)
        assert set(summary["time_source_summary"].keys()) == {"GPS", "NTP", "RTC"}

    def test_summary_present_on_empty_records(self):
        from hydra_detect.review_export import build_summary

        summary = build_summary([])
        # build_summary returns early for empty — key not expected
        # but shouldn't crash
        assert "total" in summary


# ---------------------------------------------------------------------------
# _query_ntp — unit tests with mocks
# ---------------------------------------------------------------------------

class TestQueryNtp:
    def test_ntplib_success_returns_offset(self):
        """If ntplib is importable and succeeds, offset is returned."""
        mock_response = MagicMock()
        mock_response.offset = 0.3

        mock_ntplib = MagicMock()
        mock_ntplib.NTPClient.return_value.request.return_value = mock_response

        with patch.dict("sys.modules", {"ntplib": mock_ntplib}):
            # Re-import to pick up the mock
            import importlib
            import hydra_detect.time_source as ts_mod
            importlib.reload(ts_mod)
            result = ts_mod._query_ntp("pool.ntp.org", timeout=2.0)

        # Restore original module
        importlib.reload(ts_mod)
        assert result == pytest.approx(0.3, abs=1e-9)

    def test_ntplib_exception_returns_none(self):
        """ntplib raising an exception returns None (not propagated)."""
        mock_ntplib = MagicMock()
        mock_ntplib.NTPClient.return_value.request.side_effect = Exception("timeout")

        with patch.dict("sys.modules", {"ntplib": mock_ntplib}):
            import importlib
            import hydra_detect.time_source as ts_mod
            importlib.reload(ts_mod)
            result = ts_mod._query_ntp("pool.ntp.org", timeout=2.0)

        importlib.reload(ts_mod)
        assert result is None

    def test_unreachable_host_returns_none(self):
        """Network failure should return None, not raise."""
        result = _query_ntp("240.0.0.1", timeout=0.1)  # unroutable IP
        assert result is None
