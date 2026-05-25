"""Tests for safety hardening quick wins (PR #1)."""

from __future__ import annotations

import configparser
import hashlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. RTSP bind address parameter
# ---------------------------------------------------------------------------

@pytest.fixture()
def _mock_gi(monkeypatch):
    """Provide a fake gi module so rtsp_server can be imported."""
    mock_gi = MagicMock()
    mock_gi.require_version = MagicMock()

    mock_gst = MagicMock()
    mock_gst.init.return_value = None
    mock_gst.Buffer.new_wrapped.return_value = MagicMock()

    mock_rtsp = MagicMock()
    mock_server = MagicMock()
    mock_factory = MagicMock()
    mock_rtsp.RTSPServer.return_value = mock_server
    mock_rtsp.RTSPMediaFactory.return_value = mock_factory

    mock_glib = MagicMock()
    mock_loop = MagicMock()
    mock_glib.MainLoop.return_value = mock_loop

    mock_gi.repository.Gst = mock_gst
    mock_gi.repository.GstRtspServer = mock_rtsp
    mock_gi.repository.GLib = mock_glib

    monkeypatch.setitem(sys.modules, 'gi', mock_gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', mock_gi.repository)

    mod_name = 'hydra_detect.rtsp_server'
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    yield {
        'gi': mock_gi,
        'Gst': mock_gst,
        'GstRtspServer': mock_rtsp,
        'GLib': mock_glib,
        'server': mock_server,
        'factory': mock_factory,
        'loop': mock_loop,
    }

    if mod_name in sys.modules:
        del sys.modules[mod_name]


class TestRTSPBindAddress:
    def test_default_bind_is_localhost(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        assert srv._bind_address == "127.0.0.1"
        assert "127.0.0.1" in srv.url

    def test_custom_bind_address(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra", bind_address="0.0.0.0")
        assert srv._bind_address == "0.0.0.0"
        assert "0.0.0.0" in srv.url

    def test_start_calls_set_address(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra", bind_address="192.168.1.1")
        srv.start()
        _mock_gi['server'].set_address.assert_called_once_with("192.168.1.1")


# ---------------------------------------------------------------------------
# 2. config_api write_config fsync
# ---------------------------------------------------------------------------

class TestConfigWriteFsync:
    @pytest.mark.skipif(
        sys.platform.startswith("win"),
        reason="write_config flock pattern incompatible with Windows os.replace "
        "(mirrors tests/test_config_api.py::_skip_on_windows)",
    )
    def test_write_config_calls_fsync(self, tmp_path):
        """write_config should call os.fsync after writing."""
        config = configparser.ConfigParser()
        config["camera"] = {"source": "auto", "width": "640"}
        path = tmp_path / "config.ini"
        with open(path, "w") as f:
            config.write(f)

        from hydra_detect.web.config_api import write_config

        with patch("hydra_detect.web.config_api.get_config_path", return_value=path), \
             patch("os.fsync") as mock_fsync:
            write_config({"camera": {"width": "1280"}})
            mock_fsync.assert_called_once()


# ---------------------------------------------------------------------------
# 3. backup_on_boot creates .bak file
# ---------------------------------------------------------------------------

class TestBackupOnBoot:
    def test_backup_on_boot_creates_bak(self, tmp_path):
        path = tmp_path / "config.ini"
        path.write_text("[camera]\nsource = auto\n")

        from hydra_detect.web.config_api import backup_on_boot

        with patch("hydra_detect.web.config_api.get_config_path", return_value=path):
            backup_on_boot()

        bak = tmp_path / "config.ini.bak"
        assert bak.exists()
        assert bak.read_text() == path.read_text()

    def test_backup_on_boot_noop_when_no_config(self, tmp_path):
        path = tmp_path / "config.ini"
        # File does not exist

        from hydra_detect.web.config_api import backup_on_boot

        with patch("hydra_detect.web.config_api.get_config_path", return_value=path):
            backup_on_boot()  # Should not raise

        bak = tmp_path / "config.ini.bak"
        assert not bak.exists()


# ---------------------------------------------------------------------------
# 3b. attempt_corrupt_recovery — boot-time fallback (#60)
# ---------------------------------------------------------------------------

class TestAttemptCorruptRecovery:
    """Power-loss recovery: corrupted config.ini falls back to .bak on boot.

    Covers issue #60 "Corrupted config falls back to backup with warning."
    The function is wired into __main__.main() before _run_boot_migrations
    so the migration runner never sees a half-written file.
    """

    def test_valid_config_passes_through(self, tmp_path):
        """Healthy config returns True with no changes to the file."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        path = tmp_path / "config.ini"
        path.write_text("[camera]\nsource = auto\n")
        before = path.read_text()
        assert attempt_corrupt_recovery(path) is True
        # Recovery must be a no-op on healthy configs.
        assert path.read_text() == before

    def test_missing_config_returns_true(self, tmp_path):
        """Fresh install with no config.ini is not a corruption case."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        path = tmp_path / "config.ini"
        assert attempt_corrupt_recovery(path) is True
        assert not path.exists()  # we must not create one

    def test_truncated_config_restored_from_bak(self, tmp_path, caplog):
        """A truncated config.ini is replaced by its .bak, with WARNING logged."""
        import logging
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        # .bak holds the last-known-good snapshot.
        bak.write_text("[camera]\nsource = auto\nwidth = 640\n")
        # config.ini was truncated mid-write (configparser raises on a bare
        # key with no '=', which is the most plausible truncation residue).
        path.write_text("[camera]\nsource\n")

        with caplog.at_level(logging.WARNING):
            assert attempt_corrupt_recovery(path) is True

        # The restored file must match the backup byte-for-byte.
        assert path.read_text() == bak.read_text()
        assert any("restored from last-known-good" in r.message for r in caplog.records)

    def test_empty_config_with_no_sections_restored_from_bak(self, tmp_path):
        """Empty / sectionless config.ini is treated as corrupt and restored."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        bak.write_text("[camera]\nsource = auto\n")
        path.write_text("")  # zero bytes — power-cut at the start of write_config
        assert attempt_corrupt_recovery(path) is True
        assert "[camera]" in path.read_text()

    def test_corrupt_config_no_bak_returns_false(self, tmp_path, caplog):
        """No .bak available means no recovery; caller should exit 1."""
        import logging
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        path = tmp_path / "config.ini"
        path.write_text("[camera]\nsource\n")  # bad

        with caplog.at_level(logging.CRITICAL):
            assert attempt_corrupt_recovery(path) is False
        assert any(
            "no .bak exists" in r.message.lower() for r in caplog.records
        )

    def test_corrupt_config_and_corrupt_bak_returns_false(self, tmp_path, caplog):
        """Both files unparseable — caller should exit 1, not loop."""
        import logging
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        path.write_text("[camera]\nsource\n")
        bak.write_text("[also-bad\n")  # missing ']' — configparser ParsingError

        with caplog.at_level(logging.CRITICAL):
            assert attempt_corrupt_recovery(path) is False
        assert any("backup" in r.message.lower() for r in caplog.records)

    def test_recovery_is_atomic_no_tmp_left(self, tmp_path):
        """The .tmp sibling must not survive a successful recovery."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        bak.write_text("[camera]\nsource = auto\n")
        path.write_text("[bad")
        attempt_corrupt_recovery(path)
        assert not (tmp_path / "config.ini.tmp").exists()

    # PR #231, R2-1 — orphan .tmp from a SIGKILL between copy2 and replace
    # in a prior boot's write_config / recovery is swept up on the next boot.
    def test_orphan_tmp_swept_on_boot_with_valid_config(self, tmp_path):
        """A leftover .tmp next to a healthy config.ini is removed on boot."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        path = tmp_path / "config.ini"
        tmp = tmp_path / "config.ini.tmp"
        path.write_text("[camera]\nsource = auto\n")
        tmp.write_text("[half-written\n")  # SIGKILL residue
        assert attempt_corrupt_recovery(path) is True
        assert not tmp.exists()
        # Config itself must not be touched on the healthy path.
        assert "[camera]" in path.read_text()

    def test_orphan_tmp_swept_before_recovery(self, tmp_path):
        """Orphan .tmp is removed even when the active config is corrupt."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        tmp = tmp_path / "config.ini.tmp"
        path.write_text("[bad")
        bak.write_text("[camera]\nsource = auto\n")
        tmp.write_text("[stale-orphan\n")
        assert attempt_corrupt_recovery(path) is True
        # Both the orphan AND the in-flight .tmp from THIS recovery must
        # be gone — os.replace consumes the latter, the sweep handles the
        # former.
        assert not tmp.exists()
        assert "[camera]" in path.read_text()

    # PR #231, R3-1 — successful .bak restore emits a hydra.audit event
    # so the dashboard /api/audit/summary endpoint can surface a banner.
    def test_recovery_emits_audit_event(self, tmp_path):
        """Successful .bak restore pushes a recovery_from_bak audit event."""
        from hydra_detect.audit import attach_to_logger, get_default_sink
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        attach_to_logger()  # idempotent — sets up the ring if not yet wired
        sink = get_default_sink()
        # Snapshot count BEFORE recovery so we don't depend on prior tests.
        before = sink.summary(window_seconds=3600).get(
            "counts", {}
        ).get("recovery_from_bak", 0)

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        bak.write_text("[camera]\nsource = auto\n")
        path.write_text("[bad")
        assert attempt_corrupt_recovery(path) is True

        after = sink.summary(window_seconds=3600).get(
            "counts", {}
        ).get("recovery_from_bak", 0)
        assert after == before + 1, (
            f"expected recovery_from_bak count to grow by 1, got {before} -> {after}"
        )

    def test_no_audit_event_on_healthy_config(self, tmp_path):
        """No recovery event when config.ini parses on first try."""
        from hydra_detect.audit import attach_to_logger, get_default_sink
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        attach_to_logger()
        sink = get_default_sink()
        before = sink.summary(window_seconds=3600).get(
            "counts", {}
        ).get("recovery_from_bak", 0)

        path = tmp_path / "config.ini"
        path.write_text("[camera]\nsource = auto\n")
        assert attempt_corrupt_recovery(path) is True

        after = sink.summary(window_seconds=3600).get(
            "counts", {}
        ).get("recovery_from_bak", 0)
        assert after == before, (
            "healthy boot must not emit recovery_from_bak"
        )

    def test_no_audit_event_when_recovery_fails(self, tmp_path):
        """No recovery event when no .bak is available (failure path)."""
        from hydra_detect.audit import attach_to_logger, get_default_sink
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        attach_to_logger()
        sink = get_default_sink()
        before = sink.summary(window_seconds=3600).get(
            "counts", {}
        ).get("recovery_from_bak", 0)

        path = tmp_path / "config.ini"
        path.write_text("[bad")  # corrupt, no .bak alongside
        assert attempt_corrupt_recovery(path) is False

        after = sink.summary(window_seconds=3600).get(
            "counts", {}
        ).get("recovery_from_bak", 0)
        assert after == before, (
            "failed recovery must not claim a successful restore"
        )

    # ------------------------------------------------------------------
    # Code-rollback policy: .bak schema_version > CURRENT must be rejected
    # (R3-2 from PR #237 / issue #241)
    # ------------------------------------------------------------------

    def test_bak_with_too_new_schema_is_rejected(self, tmp_path, caplog):
        """Operator code-rollback: .bak from a newer binary must not be
        silently restored — older binary cannot fully understand it.
        """
        import logging
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        from hydra_detect.config_migrate import CURRENT_SCHEMA_VERSION

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        path.write_text("")  # corrupted / empty current config
        too_new = CURRENT_SCHEMA_VERSION + 1
        bak.write_text(
            f"[meta]\nschema_version = {too_new}\n\n[camera]\nsource = auto\n"
        )

        with caplog.at_level(logging.CRITICAL):
            result = attempt_corrupt_recovery(path)

        assert result is False, (
            f"Recovery should reject .bak with schema_version={too_new} > "
            f"CURRENT_SCHEMA_VERSION={CURRENT_SCHEMA_VERSION}. Got {result!r}."
        )
        # config.ini must NOT have been overwritten with the newer .bak
        assert path.read_text() == "", (
            "config.ini was overwritten with a too-new .bak — code-rollback "
            "policy violated."
        )
        assert any(
            "exceeds" in r.message.lower() or "schema_version" in r.message
            for r in caplog.records
        ), "Expected a CRITICAL log explaining the rejection."

    def test_bak_with_equal_schema_is_accepted(self, tmp_path):
        """Sanity: same-version .bak still restores."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        from hydra_detect.config_migrate import CURRENT_SCHEMA_VERSION

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        path.write_text("")
        bak.write_text(
            f"[meta]\nschema_version = {CURRENT_SCHEMA_VERSION}\n\n"
            f"[camera]\nsource = auto\n"
        )
        assert attempt_corrupt_recovery(path) is True
        assert "[camera]" in path.read_text()

    def test_bak_with_lower_schema_is_accepted(self, tmp_path):
        """Forward-migration case: an older .bak still restores; the
        migration runner will bring it up to CURRENT_SCHEMA_VERSION after."""
        from hydra_detect.web.config_api import attempt_corrupt_recovery
        from hydra_detect.config_migrate import CURRENT_SCHEMA_VERSION

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        path.write_text("")
        older = max(0, CURRENT_SCHEMA_VERSION - 1)
        bak.write_text(
            f"[meta]\nschema_version = {older}\n\n[camera]\nsource = auto\n"
        )
        assert attempt_corrupt_recovery(path) is True
        assert "[camera]" in path.read_text()

    def test_bak_with_no_meta_section_is_accepted(self, tmp_path):
        """A .bak from a pre-meta-section era is treated as schema_version=0.
        That is below CURRENT, so it restores and gets migrated forward.
        """
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        path.write_text("")
        bak.write_text("[camera]\nsource = auto\n")  # no [meta]
        assert attempt_corrupt_recovery(path) is True

    def test_bak_with_unreadable_schema_version_is_rejected(self, tmp_path, caplog):
        """A non-integer schema_version in .bak is refused — better to
        bail than silently restore a malformed config."""
        import logging
        from hydra_detect.web.config_api import attempt_corrupt_recovery

        path = tmp_path / "config.ini"
        bak = tmp_path / "config.ini.bak"
        path.write_text("")
        bak.write_text(
            "[meta]\nschema_version = not-an-int\n\n[camera]\nsource = auto\n"
        )
        with caplog.at_level(logging.CRITICAL):
            assert attempt_corrupt_recovery(path) is False


# ---------------------------------------------------------------------------
# 4. restore_factory works
# ---------------------------------------------------------------------------

class TestRestoreFactory:
    def test_restore_factory_copies_factory_file(self, tmp_path):
        path = tmp_path / "config.ini"
        path.write_text("[camera]\nsource = auto\n")
        factory_path = tmp_path / "config.ini.factory"
        factory_path.write_text("[camera]\nsource = /dev/video0\n")

        from hydra_detect.web.config_api import restore_factory, has_factory

        with patch("hydra_detect.web.config_api.get_config_path", return_value=path):
            assert has_factory() is True
            result = restore_factory()

        assert result is True
        assert path.read_text() == "[camera]\nsource = /dev/video0\n"

    def test_restore_factory_returns_false_when_missing(self, tmp_path):
        path = tmp_path / "config.ini"
        path.write_text("[camera]\nsource = auto\n")

        from hydra_detect.web.config_api import restore_factory, has_factory

        with patch("hydra_detect.web.config_api.get_config_path", return_value=path):
            assert has_factory() is False
            result = restore_factory()

        assert result is False


# ---------------------------------------------------------------------------
# 5. Servo channel reserved validation
# ---------------------------------------------------------------------------

class TestServoReservedChannels:
    @staticmethod
    def _make_config(pan_ch=5, strike_ch=6, vehicle="drone", reserved="1,2,3,4"):
        """Build a ConfigParser with servo_tracking and vehicle sections."""
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg["mavlink"] = {
            "enabled": "true",
            "connection_string": "udp:127.0.0.1:14550",
            "baud": "57600",
            "source_system": "1",
            "alert_statustext": "true",
            "alert_interval_sec": "5",
            "severity": "2",
            "min_gps_fix": "3",
            "auto_loiter_on_detect": "false",
            "guided_roi_on_detect": "false",
            "alert_classes": "",
            "strike_distance_m": "20",
            "geo_tracking": "false",
            "geo_tracking_interval": "2",
            "sim_gps_lat": "",
            "sim_gps_lon": "",
        }
        cfg["servo_tracking"] = {
            "enabled": "true",
            "pan_channel": str(pan_ch),
            "pan_pwm_center": "1500",
            "pan_pwm_range": "500",
            "pan_invert": "false",
            "pan_dead_zone": "0.05",
            "pan_smoothing": "0.3",
            "strike_channel": str(strike_ch),
            "strike_pwm_fire": "1900",
            "strike_pwm_safe": "1100",
            "strike_duration": "0.5",
            "replaces_yaw": "false",
        }
        cfg["alerts"] = {
            "light_bar_enabled": "false",
            "light_bar_channel": "4",
            "light_bar_pwm_on": "1900",
            "light_bar_pwm_off": "1100",
            "light_bar_flash_sec": "0.5",
            "global_max_per_sec": "2",
            "priority_labels": "",
        }
        cfg[f"vehicle.{vehicle}"] = {"reserved_channels": reserved}
        return cfg

    def test_conflict_disables_servo_tracker(self):
        """pan_ch=1 conflicts with drone reserved {1,2,3,4}."""
        cfg = self._make_config(pan_ch=1, strike_ch=6, vehicle="drone", reserved="1,2,3,4")
        # Simulate the validation logic from pipeline __init__
        pan_ch = cfg.getint("servo_tracking", "pan_channel")
        strike_ch = cfg.getint("servo_tracking", "strike_channel")
        vehicle = "drone"
        vehicle_section = f"vehicle.{vehicle}"
        reserved_raw = cfg.get(vehicle_section, "reserved_channels", fallback="")
        servo_disabled = False
        if reserved_raw.strip():
            reserved = {int(c.strip()) for c in reserved_raw.split(",") if c.strip()}
            conflicts = []
            if pan_ch in reserved:
                conflicts.append(f"pan channel {pan_ch}")
            if strike_ch in reserved:
                conflicts.append(f"strike channel {strike_ch}")
            if conflicts:
                servo_disabled = True

        assert servo_disabled is True

    def test_no_conflict_keeps_servo_tracker(self):
        """pan_ch=5, strike_ch=6 do not conflict with drone reserved {1,2,3,4}."""
        cfg = self._make_config(pan_ch=5, strike_ch=6, vehicle="drone", reserved="1,2,3,4")
        pan_ch = cfg.getint("servo_tracking", "pan_channel")
        strike_ch = cfg.getint("servo_tracking", "strike_channel")
        vehicle = "drone"
        vehicle_section = f"vehicle.{vehicle}"
        reserved_raw = cfg.get(vehicle_section, "reserved_channels", fallback="")
        servo_disabled = False
        if reserved_raw.strip():
            reserved = {int(c.strip()) for c in reserved_raw.split(",") if c.strip()}
            conflicts = []
            if pan_ch in reserved:
                conflicts.append(f"pan channel {pan_ch}")
            if strike_ch in reserved:
                conflicts.append(f"strike channel {strike_ch}")
            if conflicts:
                servo_disabled = True

        assert servo_disabled is False

    def test_empty_reserved_channels_no_conflict(self):
        """Empty reserved_channels means no validation."""
        cfg = self._make_config(pan_ch=1, strike_ch=2, vehicle="drone", reserved="")
        vehicle_section = "vehicle.drone"
        reserved_raw = cfg.get(vehicle_section, "reserved_channels", fallback="")
        assert not reserved_raw.strip()


# ---------------------------------------------------------------------------
# 6. Model swap not in RESTART_REQUIRED_FIELDS
# ---------------------------------------------------------------------------

class TestModelSwapNoRestart:
    def test_yolo_model_not_in_restart_fields(self):
        from hydra_detect.web.config_api import RESTART_REQUIRED_FIELDS
        detector_fields = RESTART_REQUIRED_FIELDS.get("detector", set())
        assert "yolo_model" not in detector_fields


# ---------------------------------------------------------------------------
# 7. verify_log tolerates truncated final record
# ---------------------------------------------------------------------------

class TestVerifyLogTruncated:
    @staticmethod
    def _make_chain_record(data: dict, prev_hash: str) -> tuple[str, str]:
        """Build a JSON line with chain_hash, return (line, hash)."""
        record_json = json.dumps(data, sort_keys=True)
        chain_hash = hashlib.sha256(
            (record_json + prev_hash).encode()
        ).hexdigest()
        data["chain_hash"] = chain_hash
        return json.dumps(data), chain_hash

    def test_valid_chain_passes(self, tmp_path):
        from hydra_detect.verify_log import verify
        logfile = tmp_path / "test.jsonl"
        prev = "0" * 64
        lines = []
        for i in range(3):
            data = {"frame": i, "ts": f"2026-01-01T00:00:0{i}Z"}
            line, prev = self._make_chain_record(data, prev)
            lines.append(line)
        logfile.write_text("\n".join(lines) + "\n")
        ok, count, msg = verify(logfile)
        assert ok is True
        assert count == 3

    def test_truncated_final_record_tolerated(self, tmp_path):
        from hydra_detect.verify_log import verify
        logfile = tmp_path / "test.jsonl"
        prev = "0" * 64
        lines = []
        for i in range(3):
            data = {"frame": i, "ts": f"2026-01-01T00:00:0{i}Z"}
            line, prev = self._make_chain_record(data, prev)
            lines.append(line)
        # Append a truncated line
        lines.append('{"frame": 3, "ts": "2026-01-01T00:00:03Z", "chain_ha')
        logfile.write_text("\n".join(lines) + "\n")
        ok, count, msg = verify(logfile)
        assert ok is True
        assert count == 3
        assert "truncated" in msg

    def test_broken_middle_record_fails(self, tmp_path):
        from hydra_detect.verify_log import verify
        logfile = tmp_path / "test.jsonl"
        prev = "0" * 64
        lines = []
        for i in range(3):
            data = {"frame": i, "ts": f"2026-01-01T00:00:0{i}Z"}
            line, prev = self._make_chain_record(data, prev)
            lines.append(line)
        # Corrupt the second line (index 1)
        lines[1] = '{"broken json'
        lines.append('{"also broken')
        logfile.write_text("\n".join(lines) + "\n")
        ok, count, msg = verify(logfile)
        assert ok is False
        assert count == 2  # fails on line 2
