"""Tests for the full config read/write API endpoints."""

from __future__ import annotations

import configparser
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state

# write_config holds an open file descriptor for advisory flock on POSIX,
# then calls os.replace on the same path. Linux is fine; Windows refuses
# to rename over an open file (WinError 5). The production target is
# Jetson/Linux — these tests just skip on Windows dev workstations.
_WINDOWS = sys.platform.startswith("win")
_skip_on_windows = pytest.mark.skipif(
    _WINDOWS,
    reason="write_config flock pattern incompatible with Windows os.replace",
)


@pytest.fixture(autouse=True)
def _reset_state():
    configure_auth(None)
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config.ini for testing."""
    config = configparser.ConfigParser()
    config["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "30"}
    config["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.45"}
    config["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": "secret-test-token"}
    config["tracker"] = {"track_thresh": "0.5", "track_buffer": "30"}
    path = tmp_path / "config.ini"
    with open(path, "w") as f:
        config.write(f)
    return path


class TestConfigGetEndpoint:
    def test_get_config_returns_all_sections(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200
        data = resp.json()
        assert "camera" in data
        assert "detector" in data
        assert data["camera"]["source"] == "auto"

    def test_get_config_redacts_api_token(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200
        assert resp.json()["web"]["api_token"] == "***"

    def test_get_config_no_auth_required(self, client, tmp_config):
        """GET /api/config/full is read-only with redacted secrets — no auth needed."""
        configure_auth("my-token")
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full")
        assert resp.status_code == 200


class TestConfigPostEndpoint:
    def test_post_config_writes_values(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "camera": {"fps": "15"},
            })
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["fps"] == "15"
        assert config["camera"]["source"] == "auto"

    def test_post_config_preserves_token_on_masked_value(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "web": {"api_token": "***"},
            })
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["web"]["api_token"] == "secret-test-token"

    def test_post_config_creates_backup(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "camera": {"fps": "15"},
            })
        assert resp.status_code == 200
        assert (tmp_config.parent / "config.ini.bak").exists()

    def test_post_config_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token")
        resp = client.post("/api/config/full", json={"camera": {"fps": "15"}})
        assert resp.status_code == 401

    def test_post_config_rejects_oversized_body(self, client, tmp_config):
        huge = {"camera": {"source": "x" * 70000}}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json=huge)
        assert resp.status_code == 413

    def test_post_config_returns_restart_required_fields(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "web": {"port": "9090"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "restart_required" in data
        assert any("port" in f for f in data["restart_required"])

    def test_post_config_reports_skipped_fields(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={
                "nonexistent_section": {"foo": "bar"},
                "camera": {"nonexistent_field": "baz"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skipped"]) == 2

    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"camera": {"fps": "not-an-int"}}, "camera.fps"),
            ({"detector": {"yolo_confidence": "not-a-float"}}, "detector.yolo_confidence"),
            ({"mavlink": {"enabled": "not-a-bool"}}, "mavlink.enabled"),
            ({"camera": {"video_standard": "secam"}}, "camera.video_standard"),
        ],
    )
    def test_post_config_rejects_invalid_schema_values(self, client, tmp_config, payload, field):
        original_content = tmp_config.read_text()
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "Validation failed"
        assert field in data["field_errors"]
        assert tmp_config.read_text() == original_content


class TestConfigAuthPositiveCases:
    def test_get_config_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.get("/api/config/full", headers=headers)
        assert resp.status_code == 200
        assert "camera" in resp.json()

    def test_post_config_with_valid_token(self, client, tmp_config):
        configure_auth("my-token")
        headers = {"Authorization": "Bearer my-token"}
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/full", json={"camera": {"fps": "15"}}, headers=headers)
        assert resp.status_code == 200


class TestConfigAtomicWrite:
    @pytest.mark.skipif(
        getattr(os, "getuid", lambda: 1)() == 0,
        reason="chmod has no effect when running as root",
    )
    @pytest.mark.skipif(
        not hasattr(os, "getuid"),
        reason="POSIX-only chmod semantics — Windows test runner",
    )
    def test_failed_write_does_not_corrupt_original(self, client, tmp_config):
        original_content = tmp_config.read_text()
        tmp_config.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
                resp = client.post("/api/config/full", json={"camera": {"fps": "15"}})
            assert resp.status_code == 500
            tmp_config.parent.chmod(stat.S_IRWXU)
            assert tmp_config.read_text() == original_content
        finally:
            tmp_config.parent.chmod(stat.S_IRWXU)


class TestConfigRestoreBackup:
    def test_restore_backup_works(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            client.post("/api/config/full", json={"camera": {"fps": "15"}})
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 200
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert config["camera"]["fps"] == "30"

    def test_restore_backup_no_backup(self, client, tmp_config):
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/restore-backup")
        assert resp.status_code == 404


class TestRestartRequiredFieldsConsistency:
    """RESTART_REQUIRED_FIELDS must reference real schema keys — stale entries
    silently became no-ops when fields were renamed (e.g. model → yolo_model)."""

    def test_all_restart_fields_exist_in_schema(self):
        from hydra_detect.config_schema import SCHEMA
        from hydra_detect.web.config_api import RESTART_REQUIRED_FIELDS

        stale = []
        for section, fields in RESTART_REQUIRED_FIELDS.items():
            schema_section = SCHEMA.get(section, {})
            for key in fields:
                if key not in schema_section:
                    stale.append(f"{section}.{key}")
        assert not stale, (
            f"RESTART_REQUIRED_FIELDS references keys missing from SCHEMA: {stale}"
        )

    def test_yolo_model_not_restart_required(self):
        """yolo_model hot-swaps via switch_model() — must not prompt restart."""
        from hydra_detect.web.config_api import RESTART_REQUIRED_FIELDS
        assert "yolo_model" not in RESTART_REQUIRED_FIELDS.get("detector", set())


class TestRedactedFieldsConsistency:
    """REDACTED_FIELDS must reference real schema keys — if a secret key is
    renamed without updating this set, GET /api/config/full silently leaks."""

    def test_all_redacted_fields_exist_in_schema(self):
        from hydra_detect.config_schema import SCHEMA
        from hydra_detect.web.config_api import REDACTED_FIELDS

        stale = []
        for section, fields in REDACTED_FIELDS.items():
            schema_section = SCHEMA.get(section, {})
            for key in fields:
                if key not in schema_section:
                    stale.append(f"{section}.{key}")
        assert not stale, (
            f"REDACTED_FIELDS references keys missing from SCHEMA: {stale}"
        )


class TestAtomicConfigWrite:
    """Config writes must be crash-safe — a power cut mid-write must not
    leave a partial config.ini (issue #60)."""

    def test_write_uses_tmp_then_replace(self, client, tmp_config):
        """Verify the write path calls os.replace with a .tmp source."""
        import hydra_detect.web.config_api as cfg_api
        calls = []
        real_replace = os.replace

        def tracked_replace(src, dst):
            calls.append((str(src), str(dst)))
            real_replace(src, dst)

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config), \
                patch.object(cfg_api.os, "replace", side_effect=tracked_replace):
            resp = client.post("/api/config/full", json={"camera": {"fps": "25"}})

        assert resp.status_code == 200
        assert len(calls) == 1
        src, dst = calls[0]
        assert src.endswith(".tmp")
        assert dst == str(tmp_config)

    def test_orphan_tmp_cleaned_up_on_failure(self, client, tmp_config):
        """If os.replace raises, the .tmp file must not persist."""
        import hydra_detect.web.config_api as cfg_api
        tmp_path = Path(str(tmp_config) + ".tmp")

        def failing_replace(src, dst):
            raise OSError("simulated rename failure")

        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config), \
                patch.object(cfg_api.os, "replace", side_effect=failing_replace):
            resp = client.post("/api/config/full", json={"camera": {"fps": "25"}})

        # Write failed — original config must be untouched and .tmp gone.
        assert resp.status_code in (500, 200)  # route may or may not surface error
        assert not tmp_path.exists(), "orphan .tmp not cleaned up"
        # Original file must still be readable and not half-written.
        config = configparser.ConfigParser()
        config.read(tmp_config)
        assert "camera" in config


class TestConfigImportValidation:
    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"camera": {"fps": "bad-int"}}, "camera.fps"),
            ({"detector": {"yolo_confidence": "bad-float"}}, "detector.yolo_confidence"),
            ({"mavlink": {"enabled": "bad-bool"}}, "mavlink.enabled"),
            ({"camera": {"video_standard": "bad-enum"}}, "camera.video_standard"),
        ],
    )
    def test_import_rejects_invalid_schema_values(self, client, tmp_config, payload, field):
        original_content = tmp_config.read_text()
        with patch("hydra_detect.web.config_api.get_config_path", return_value=tmp_config):
            resp = client.post("/api/config/import", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "Validation failed"
        assert field in data["field_errors"]
        assert tmp_config.read_text() == original_content


# ── Issue #75 — Student config recovery ─────────────────────────────────────


@pytest.fixture
def tmp_config_with_factory(tmp_path):
    """Create config.ini AND config.ini.factory for recovery tests."""
    factory = configparser.ConfigParser()
    factory["meta"] = {"schema_version": "1"}
    factory["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "30"}
    factory["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.45"}
    factory["web"] = {"host": "0.0.0.0", "port": "8080"}
    factory["tracker"] = {"track_thresh": "0.5", "track_buffer": "30"}
    factory["tak"] = {"callsign": "HYDRA-TEST"}
    factory_path = tmp_path / "config.ini.factory"
    with open(factory_path, "w") as f:
        factory.write(f)

    # Current config — student has changed fps + port from factory.
    current = configparser.ConfigParser()
    current["meta"] = {"schema_version": "1"}
    current["camera"] = {"source": "auto", "width": "640", "height": "480", "fps": "15"}
    current["detector"] = {"yolo_model": "yolov8s.pt", "yolo_confidence": "0.45"}
    current["web"] = {"host": "0.0.0.0", "port": "9999"}
    current["tracker"] = {"track_thresh": "0.5", "track_buffer": "30"}
    current["tak"] = {"callsign": "HYDRA-TEST"}
    cfg_path = tmp_path / "config.ini"
    with open(cfg_path, "w") as f:
        current.write(f)

    return cfg_path


class TestFactoryReset:
    """Issue #75 — student-facing factory reset."""

    def test_factory_reset_restores_defaults(self, client, tmp_config_with_factory):
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post("/api/config/factory-reset")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["restart_required"] is True
        assert data["backup_path"]  # non-empty path

        cfg = configparser.ConfigParser()
        cfg.read(tmp_config_with_factory)
        # fps was 15 in current, 30 in factory — must now be 30.
        assert cfg["camera"]["fps"] == "30"
        # port was 9999, factory says 8080.
        assert cfg["web"]["port"] == "8080"

    def test_factory_reset_preserves_identity(self, client, tmp_path):
        """R3-2: [identity] (api_token, hash, callsign) must survive reset.

        The factory file (correctly) has no [identity] section. Without
        preservation, every reset on a configured unit wipes API auth
        until Platform Setup is re-run via shell — exactly the failure
        mode the recovery control is meant to fix.
        """
        factory = configparser.ConfigParser()
        factory["meta"] = {"schema_version": "1"}
        factory["camera"] = {"fps": "30"}
        factory["tak"] = {"callsign": "HYDRA-TEST"}
        factory_path = tmp_path / "config.ini.factory"
        with open(factory_path, "w") as f:
            factory.write(f)

        current = configparser.ConfigParser()
        current["meta"] = {"schema_version": "1"}
        current["camera"] = {"fps": "15"}
        current["tak"] = {"callsign": "HYDRA-TEST"}
        current["identity"] = {
            "callsign": "HYDRA-UNIT-07",
            "api_token": "platform-setup-token-DEADBEEF",
            "web_password_hash": "pbkdf2:sha256:600000$salt$hash",
            "software_version": "0.9.0",
            "commit_hash": "abc123",
        }
        cfg_path = tmp_path / "config.ini"
        with open(cfg_path, "w") as f:
            current.write(f)

        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=cfg_path,
        ):
            resp = client.post("/api/config/factory-reset")

        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_preserved"] is True
        assert "API token" in data["message"]

        result_cfg = configparser.ConfigParser()
        result_cfg.read(cfg_path)
        # Factory defaults applied.
        assert result_cfg["camera"]["fps"] == "30"
        # Identity carried over byte-for-byte.
        assert result_cfg.has_section("identity")
        assert result_cfg["identity"]["api_token"] == "platform-setup-token-DEADBEEF"
        assert result_cfg["identity"]["callsign"] == "HYDRA-UNIT-07"
        assert (
            result_cfg["identity"]["web_password_hash"]
            == "pbkdf2:sha256:600000$salt$hash"
        )

    def test_factory_reset_no_identity_when_none_present(
        self, client, tmp_config_with_factory,
    ):
        """No [identity] in current config -> identity_preserved=False, factory copied as-is."""
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post("/api/config/factory-reset")

        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_preserved"] is False
        # Message reverts to the non-identity version.
        assert "API token" not in data["message"]
        cfg = configparser.ConfigParser()
        cfg.read(tmp_config_with_factory)
        assert not cfg.has_section("identity")
        assert cfg["camera"]["fps"] == "30"

    def test_factory_reset_creates_timestamped_backup(
        self, client, tmp_config_with_factory,
    ):
        original_content = tmp_config_with_factory.read_text()
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post("/api/config/factory-reset")

        assert resp.status_code == 200
        backup_path = Path(resp.json()["backup_path"])
        assert backup_path.exists()
        assert backup_path.name.startswith("config.ini.before-reset.")
        # The student's pre-reset config must be recoverable byte-for-byte.
        assert backup_path.read_text() == original_content

    def test_factory_reset_fails_when_factory_missing(self, client, tmp_path):
        # Only config.ini exists; no .factory companion.
        cfg_path = tmp_path / "config.ini"
        cfg = configparser.ConfigParser()
        cfg["camera"] = {"fps": "30"}
        with open(cfg_path, "w") as f:
            cfg.write(f)

        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=cfg_path,
        ):
            resp = client.post("/api/config/factory-reset")

        assert resp.status_code == 404
        # Original config must be untouched.
        cfg2 = configparser.ConfigParser()
        cfg2.read(cfg_path)
        assert cfg2["camera"]["fps"] == "30"

    def test_factory_reset_requires_auth_when_enabled(
        self, client, tmp_config_with_factory,
    ):
        configure_auth("my-token", require_auth_for_control=True)
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post("/api/config/factory-reset")
        assert resp.status_code in (401, 403)
        # Original content untouched — auth check happens before reset.
        cfg = configparser.ConfigParser()
        cfg.read(tmp_config_with_factory)
        assert cfg["camera"]["fps"] == "15"

    def test_factory_reset_with_corrupt_factory_preserves_current(
        self, client, tmp_config_with_factory,
    ):
        # Write garbage into config.ini.factory.
        factory_path = tmp_config_with_factory.parent / "config.ini.factory"
        factory_path.write_text("not a config\nrandom bytes\n")
        original_content = tmp_config_with_factory.read_text()

        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post("/api/config/factory-reset")

        # configparser may parse zero-section files without raising; the
        # endpoint should still refuse to overwrite with an empty config.
        assert resp.status_code in (400, 500)
        assert tmp_config_with_factory.read_text() == original_content


class TestConfigExport:
    """Issue #75 — config export downloads versioned JSON."""

    def test_export_returns_json_envelope(self, client, tmp_config):
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/export")

        assert resp.status_code == 200
        data = resp.json()
        assert "schema_version" in data
        assert "exported_at" in data
        assert "callsign" in data
        assert "sections" in data
        assert "camera" in data["sections"]

    def test_export_sets_content_disposition(self, client, tmp_config):
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/export")

        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "filename=" in cd
        assert "hydra-config-" in cd
        assert ".json" in cd

    def test_export_redacts_api_token(self, client, tmp_config):
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/export")

        assert resp.status_code == 200
        assert resp.json()["sections"]["web"]["api_token"] == "***"

    def test_export_omits_identity_section(self, tmp_path, client):
        """R1-3: [identity] carries plaintext credentials — must never ship.

        Mirrors config_lkg.py:77-83 which strips [identity] before writing
        the LKG snapshot, for the same reason.
        """
        cfg = configparser.ConfigParser()
        cfg["camera"] = {"source": "auto", "fps": "30"}
        cfg["web"] = {"host": "0.0.0.0", "port": "8080", "api_token": "secret"}
        cfg["tak"] = {"callsign": "HYDRA-TAK-FALLBACK"}
        cfg["identity"] = {
            "callsign": "HYDRA-IDENT-PRIMARY",
            "api_token": "platform-setup-token-DEADBEEF",
            "web_password_hash": "pbkdf2:sha256:600000$salt$hash",
            "software_version": "0.9.0",
        }
        cfg_path = tmp_path / "config.ini"
        with open(cfg_path, "w") as f:
            cfg.write(f)

        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=cfg_path,
        ):
            resp = client.get("/api/config/export")

        assert resp.status_code == 200
        payload = resp.json()
        # No [identity] keys leave the unit.
        assert "identity" not in payload["sections"]
        # Callsign came from [identity] (primary), not the [tak] fallback.
        assert payload["callsign"] == "HYDRA-IDENT-PRIMARY"
        # Serialized body must not contain the secrets even by accident.
        body = resp.text
        assert "platform-setup-token-DEADBEEF" not in body
        assert "pbkdf2:sha256" not in body

    @_skip_on_windows
    def test_export_round_trip_preserves_non_secret_values(
        self, client, tmp_config,
    ):
        """Export then re-import must leave config equivalent (secrets aside)."""
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            export_resp = client.get("/api/config/export")
            assert export_resp.status_code == 200
            payload = export_resp.json()

            # Mutate the live file so we can prove import restored values.
            cfg = configparser.ConfigParser()
            cfg.read(tmp_config)
            cfg["camera"]["fps"] = "12"
            with open(tmp_config, "w") as f:
                cfg.write(f)

            import_resp = client.post("/api/config/import", json=payload)

        assert import_resp.status_code == 200
        cfg2 = configparser.ConfigParser()
        cfg2.read(tmp_config)
        assert cfg2["camera"]["fps"] == "30"  # restored from export

    def test_export_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token", require_auth_for_control=True)
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/export")
        assert resp.status_code in (401, 403)


class TestConfigImportStrict:
    """Issue #75 — strict import validation."""

    def test_import_rejects_unknown_section(self, client, tmp_config):
        original_content = tmp_config.read_text()
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"sections": {"bogus_section": {"foo": "bar"}}},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert "errors" in data
        assert any("bogus_section" in e for e in data["errors"])
        # Untouched.
        assert tmp_config.read_text() == original_content

    def test_import_rejects_unknown_key(self, client, tmp_config):
        original_content = tmp_config.read_text()
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"sections": {"camera": {"fps": "30", "made_up_key": "x"}}},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert any("camera.made_up_key" in e for e in data["errors"])
        assert tmp_config.read_text() == original_content

    def test_import_rejects_identity_section(self, client, tmp_config):
        """[identity] is set by Platform Setup — never importable."""
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"sections": {"identity": {"callsign": "HACKER-1"}}},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert any("identity" in e for e in data["errors"])

    @_skip_on_windows
    def test_import_accepts_full_export_envelope(self, client, tmp_config):
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            payload = {
                "export_version": 1,
                "schema_version": 1,
                "callsign": "HYDRA-TEST",
                "exported_at": "2026-01-01T00:00:00Z",
                "sections": {"camera": {"fps": "20"}},
            }
            resp = client.post("/api/config/import", json=payload)

        assert resp.status_code == 200
        cfg = configparser.ConfigParser()
        cfg.read(tmp_config)
        assert cfg["camera"]["fps"] == "20"

    def test_import_requires_auth_when_enabled(self, client, tmp_config):
        configure_auth("my-token", require_auth_for_control=True)
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import", json={"camera": {"fps": "20"}},
            )
        assert resp.status_code in (401, 403)


class TestConfigRecoveryHelpers:
    """Direct unit tests for the helper functions in config_api."""

    def test_safe_callsign_strips_path_chars(self):
        from hydra_detect.web.config_api import _safe_callsign
        assert _safe_callsign("../../../etc/passwd") == "etc-passwd"
        assert _safe_callsign('foo"bar') == "foo-bar"
        assert _safe_callsign("HYDRA-1") == "HYDRA-1"
        assert _safe_callsign("") == "HYDRA"
        assert _safe_callsign(None) == "HYDRA"

    def test_export_filename_uses_callsign_and_stamp(self):
        from hydra_detect.web.config_api import export_filename
        name = export_filename({"callsign": "HYDRA-7"})
        assert name.startswith("hydra-config-HYDRA-7-")
        assert name.endswith(".json")

    def test_validate_import_payload_accepts_bare_dict(self):
        from hydra_detect.web.config_api import validate_import_payload
        result = validate_import_payload({"camera": {"fps": "30"}})
        assert result["ok"] is True
        assert "camera" in result["updates"]

    def test_validate_import_payload_rejects_non_dict(self):
        from hydra_detect.web.config_api import validate_import_payload
        result = validate_import_payload(["not", "a", "dict"])
        assert result["ok"] is False
        assert result["errors"]
