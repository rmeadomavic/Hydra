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

        The historical config_lkg snapshot module applied the same filter
        before writing config.ini.lkg; the module was deleted in PR #231
        but the stripping rule for [identity] still applies here.
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


# ── Issue #224 — Runtime drift surface + opt-in auto_restart ────────────────


class TestRuntimeConfigDiff:
    """Issue #224 — /api/config/diff surfaces disk vs in-memory drift.

    The diff endpoint is the Layer-1 signal that the operator sees in
    the dashboard banner after factory-reset / import. These tests pin
    the contract: empty diff when configs agree, populated diff when
    they don't, runtime-empty when no pipeline callback is registered.
    No actual config writes — the Windows os.replace flake is avoided
    by mocking the in-memory snapshot callback.
    """

    def _make_cfg(self, **sections):
        import configparser
        cfg = configparser.ConfigParser()
        for name, kvs in sections.items():
            cfg[name] = kvs
        return cfg

    def test_diff_empty_when_disk_and_runtime_match(self, client, tmp_config):
        # Build an in-memory ConfigParser that mirrors tmp_config exactly.
        import configparser
        runtime_cfg = configparser.ConfigParser()
        runtime_cfg.read(tmp_config)
        stream_state.set_callbacks(get_in_memory_config=lambda: runtime_cfg)

        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/diff")

        assert resp.status_code == 200
        data = resp.json()
        assert data["diff"] == {}
        # Both sides populated — runtime is not empty when callback registered.
        assert data["disk"]
        assert data["runtime"]

    def test_diff_non_empty_after_manual_disk_edit(
        self, client, tmp_config, tmp_path, monkeypatch,
    ):
        # Snapshot the in-memory state, then mutate the disk file directly.
        import configparser
        runtime_cfg = configparser.ConfigParser()
        runtime_cfg.read(tmp_config)
        stream_state.set_callbacks(get_in_memory_config=lambda: runtime_cfg)

        # Mutate disk without going through write_config (simulates the
        # post-factory-reset state: file changed, in-memory cfg stale).
        disk_cfg = configparser.ConfigParser()
        disk_cfg.read(tmp_config)
        disk_cfg["camera"]["fps"] = "12"  # was "30"
        disk_cfg["web"]["port"] = "9999"  # was "8080"
        with open(tmp_config, "w") as f:
            disk_cfg.write(f)

        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/diff")

        assert resp.status_code == 200
        data = resp.json()
        assert "camera" in data["diff"]
        assert "fps" in data["diff"]["camera"]
        assert data["diff"]["camera"]["fps"] == {"disk": "12", "runtime": "30"}
        assert data["diff"]["web"]["port"] == {"disk": "9999", "runtime": "8080"}

    def test_diff_empty_runtime_when_no_pipeline_callback(self, client, tmp_config):
        # No pipeline registered (fresh boot or test harness) — runtime
        # is empty, diff is empty, dashboard suppresses the banner.
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/diff")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime"] == {}
        assert data["diff"] == {}

    def test_diff_redacted_field_drift_is_suppressed(self, client, tmp_config):
        # Both sides redact api_token to "***"; the diff must NOT report
        # spurious drift even when the underlying values differ — we
        # cannot tell from the redacted projection alone.
        import configparser
        runtime_cfg = configparser.ConfigParser()
        runtime_cfg.read(tmp_config)
        # Mutate the runtime token only; disk side is unchanged.
        runtime_cfg["web"]["api_token"] = "a-different-token"
        stream_state.set_callbacks(get_in_memory_config=lambda: runtime_cfg)

        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.get("/api/config/diff")

        assert resp.status_code == 200
        data = resp.json()
        # Both sides redact api_token -> diff suppresses this key.
        web_diff = data["diff"].get("web", {})
        assert "api_token" not in web_diff


class TestFactoryResetAutoRestart:
    """Issue #224 — factory-reset accepts an opt-in auto_restart flag.

    The default behavior (no body, or body without auto_restart) MUST
    remain unchanged — the existing
    `test_factory_reset_does_not_trigger_in_process_restart` test in
    test_zero_touch.py pins this. The new test class adds coverage for
    the opt-in path.
    """

    def test_factory_reset_no_auto_restart_does_not_fire_callback(
        self, client, tmp_config_with_factory,
    ):
        # Regression: default behavior MUST NOT call the restart callback.
        # Mirrors test_zero_touch.py:207-219 but on the recovery fixture.
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            # No body at all — backward-compat with PR #212 callers.
            resp = client.post("/api/config/factory-reset")
        # Status check is OS-dependent due to the Windows os.replace flake;
        # the assertion that MATTERS for this test is the restart cb.
        restart_cb.assert_not_called()
        # Also verify the response reports restart_triggered=false when
        # the request itself succeeded.
        if resp.status_code == 200:
            assert resp.json()["restart_triggered"] is False

    @_skip_on_windows
    def test_factory_reset_with_auto_restart_fires_callback_once(
        self, client, tmp_config_with_factory,
    ):
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post(
                "/api/config/factory-reset", json={"auto_restart": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["restart_triggered"] is True
        assert data["restart_suppressed_reason"] is None
        assert "Pipeline restart triggered" in data["message"]
        restart_cb.assert_called_once()

    @_skip_on_windows
    def test_factory_reset_auto_restart_suppressed_when_engagement_active(
        self, client, tmp_config_with_factory,
    ):
        """Adversarial finding R3-1 in docs/adversarial/228.md:
        auto_restart=true while autonomous engagement is active would
        drop the engagement mid-cycle. The disk reset goes through;
        the restart is suppressed with an operator-facing reason."""
        from unittest.mock import MagicMock
        from hydra_detect.web import config_api as _cfg_api

        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        prior_cb = _cfg_api._engagement_active_cb
        _cfg_api._engagement_active_cb = lambda: True
        try:
            with patch(
                "hydra_detect.web.config_api.get_config_path",
                return_value=tmp_config_with_factory,
            ):
                resp = client.post(
                    "/api/config/factory-reset", json={"auto_restart": True},
                )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            # Disk reset still went through.
            assert data["status"] == "ok"
            # Restart was suppressed.
            assert data["restart_triggered"] is False
            assert data["restart_suppressed_reason"] is not None
            assert "engagement active" in data["restart_suppressed_reason"].lower()
            restart_cb.assert_not_called()
        finally:
            _cfg_api._engagement_active_cb = prior_cb

    @_skip_on_windows
    def test_factory_reset_with_auto_restart_false_does_not_fire(
        self, client, tmp_config_with_factory,
    ):
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post(
                "/api/config/factory-reset", json={"auto_restart": False},
            )
        assert resp.status_code == 200
        assert resp.json()["restart_triggered"] is False
        restart_cb.assert_not_called()

    def test_factory_reset_auto_restart_requires_auth_when_enabled(
        self, client, tmp_config_with_factory,
    ):
        # Auth gate fires before any restart callback consideration.
        configure_auth("my-token", require_auth_for_control=True)
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path",
            return_value=tmp_config_with_factory,
        ):
            resp = client.post(
                "/api/config/factory-reset", json={"auto_restart": True},
            )
        assert resp.status_code in (401, 403)
        # Auth-gated request must NOT fire the restart even when asked.
        restart_cb.assert_not_called()


class TestConfigImportAutoRestart:
    """Issue #224 — import accepts the same opt-in auto_restart flag."""

    @_skip_on_windows
    def test_import_no_auto_restart_does_not_fire_callback(
        self, client, tmp_config,
    ):
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import", json={"camera": {"fps": "20"}},
            )
        assert resp.status_code == 200
        assert resp.json()["restart_triggered"] is False
        restart_cb.assert_not_called()

    @_skip_on_windows
    def test_import_with_auto_restart_fires_callback_once(
        self, client, tmp_config,
    ):
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"auto_restart": True, "sections": {"camera": {"fps": "20"}}},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["restart_triggered"] is True
        restart_cb.assert_called_once()

    @_skip_on_windows
    def test_import_auto_restart_with_bare_dict_envelope(
        self, client, tmp_config,
    ):
        # The bare-dict path (no `sections` wrapper) still accepts
        # auto_restart as a sibling key.
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"auto_restart": True, "camera": {"fps": "20"}},
            )
        assert resp.status_code == 200
        # auto_restart should be popped from the validation surface — it
        # is NOT a config section, so import validation must not reject
        # the request with "unknown section: auto_restart".
        assert resp.json()["restart_triggered"] is True
        restart_cb.assert_called_once()

    def test_import_auto_restart_requires_auth_when_enabled(
        self, client, tmp_config,
    ):
        configure_auth("my-token", require_auth_for_control=True)
        from unittest.mock import MagicMock
        restart_cb = MagicMock()
        stream_state.set_callbacks(on_restart_command=restart_cb)
        with patch(
            "hydra_detect.web.config_api.get_config_path", return_value=tmp_config,
        ):
            resp = client.post(
                "/api/config/import",
                json={"auto_restart": True, "camera": {"fps": "20"}},
            )
        assert resp.status_code in (401, 403)
        restart_cb.assert_not_called()


class TestComputeConfigDiff:
    """Direct unit tests for compute_config_diff()."""

    def test_diff_empty_when_dicts_equal(self):
        from hydra_detect.web.config_api import compute_config_diff
        d = {"camera": {"fps": "30"}}
        assert compute_config_diff(d, d) == {}

    def test_diff_reports_changed_value(self):
        from hydra_detect.web.config_api import compute_config_diff
        disk = {"camera": {"fps": "12"}}
        runtime = {"camera": {"fps": "30"}}
        assert compute_config_diff(disk, runtime) == {
            "camera": {"fps": {"disk": "12", "runtime": "30"}},
        }

    def test_diff_reports_section_added_on_disk(self):
        from hydra_detect.web.config_api import compute_config_diff
        disk = {"camera": {"fps": "30"}, "new_section": {"a": "1"}}
        runtime = {"camera": {"fps": "30"}}
        result = compute_config_diff(disk, runtime)
        assert "new_section" in result
        assert result["new_section"]["a"] == {"disk": "1", "runtime": ""}

    def test_diff_suppresses_both_redacted(self):
        from hydra_detect.web.config_api import compute_config_diff
        disk = {"web": {"api_token": "***"}}
        runtime = {"web": {"api_token": "***"}}
        assert compute_config_diff(disk, runtime) == {}
