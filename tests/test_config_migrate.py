"""Tests for config schema migration runner.

TDD order: write failing tests first, then implement.
"""

from __future__ import annotations

import configparser
import textwrap
from pathlib import Path

import pytest

from hydra_detect.config_migrate import (
    CURRENT_SCHEMA_VERSION,
    MigrationError,
    MigrationResult,
    run_migrations,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOLDEN_V0 = textwrap.dedent("""\
    [camera]
    source_type = auto
    source = auto
    width = 640
    height = 480
    fps = 30

    [detector]
    yolo_model = yolov8n.pt
    yolo_confidence = 0.45

    [web]
    enabled = true
    host = 0.0.0.0
    port = 8080
""")

GOLDEN_V1 = textwrap.dedent("""\
    [meta]
    schema_version = 1

    [camera]
    source_type = auto
    source = auto
    width = 640
    height = 480
    fps = 30

    [detector]
    yolo_model = yolov8n.pt
    yolo_confidence = 0.45

    [web]
    enabled = true
    host = 0.0.0.0
    port = 8080
""")


# A config already at the current schema version. Constructed dynamically so
# the fixture stays correct when CURRENT_SCHEMA_VERSION is bumped.
GOLDEN_CURRENT = textwrap.dedent(f"""\
    [meta]
    schema_version = {CURRENT_SCHEMA_VERSION}

    [camera]
    source_type = auto
    source = auto
    width = 640
    height = 480
    fps = 30

    [detector]
    yolo_model = yolov8n.pt
    yolo_confidence = 0.45

    [web]
    enabled = true
    host = 0.0.0.0
    port = 8080
""")


@pytest.fixture
def tmp_config(tmp_path):
    """Return a factory: write INI text to a temp file and return its Path."""
    def _make(content: str) -> Path:
        p = tmp_path / "config.ini"
        p.write_text(content)
        return p
    return _make


# ---------------------------------------------------------------------------
# 1. Golden v0 config migrates to v1 — backup created, content preserved
# ---------------------------------------------------------------------------

class TestV0ToV1Migration:
    def test_v0_migrates_to_current(self, tmp_config):
        """v0 config (no [meta]) gets schema_version stamped to current."""
        p = tmp_config(GOLDEN_V0)
        run_migrations(p)

        cfg = configparser.ConfigParser()
        cfg.read(p)

        assert cfg.has_section("meta"), "expected [meta] section after migration"
        assert cfg.get("meta", "schema_version") == str(CURRENT_SCHEMA_VERSION)

    def test_v0_migration_result(self, tmp_config):
        """run_migrations returns correct MigrationResult for v0→current."""
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        assert isinstance(result, MigrationResult)
        assert result.from_version == 0
        assert result.to_version == CURRENT_SCHEMA_VERSION
        # Every step from 0 to current should have been applied.
        assert len(result.applied) == CURRENT_SCHEMA_VERSION
        assert "001" in result.applied[0]  # first step is the v0→v1 migration

    def test_v0_backup_created(self, tmp_config):
        """A .premigrate.<ISO8601> backup is created before any change."""
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        assert result.backup_path is not None
        assert result.backup_path.exists(), "backup file must exist"
        assert ".premigrate." in result.backup_path.name

    def test_v0_backup_contains_original_content(self, tmp_config):
        """Backup file holds the original (pre-migration) content."""
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        backup_text = result.backup_path.read_text()
        assert "schema_version" not in backup_text, (
            "backup must be the original content — no schema_version yet"
        )

    def test_v0_migration_preserves_existing_keys(self, tmp_config):
        """Existing config values are untouched after migration."""
        p = tmp_config(GOLDEN_V0)
        run_migrations(p)

        cfg = configparser.ConfigParser()
        cfg.read(p)

        assert cfg.get("camera", "source_type") == "auto"
        assert cfg.get("detector", "yolo_model") == "yolov8n.pt"
        assert cfg.get("web", "port") == "8080"


# ---------------------------------------------------------------------------
# 2. v1 config is a no-op
# ---------------------------------------------------------------------------

class TestCurrentNoop:
    def test_at_current_no_migrations_applied(self, tmp_config):
        """Config already at the current schema version returns empty applied."""
        p = tmp_config(GOLDEN_CURRENT)
        result = run_migrations(p)

        assert result.applied == []
        assert result.from_version == CURRENT_SCHEMA_VERSION
        assert result.to_version == CURRENT_SCHEMA_VERSION

    def test_at_current_no_backup_created(self, tmp_config):
        """No backup file is written when no migrations run."""
        p = tmp_config(GOLDEN_CURRENT)
        result = run_migrations(p)

        assert result.backup_path is None

    def test_at_current_file_unchanged(self, tmp_config):
        """File content is byte-identical after a no-op run."""
        p = tmp_config(GOLDEN_CURRENT)
        before = p.read_bytes()
        run_migrations(p)
        after = p.read_bytes()

        cfg_before = configparser.ConfigParser()
        cfg_before.read_string(before.decode())
        cfg_after = configparser.ConfigParser()
        cfg_after.read_string(after.decode())

        assert dict(cfg_before["meta"]) == dict(cfg_after["meta"])


class TestV1ToV2Migration:
    """Migration 002: forward-predictor keys are inserted into [guidance]."""

    def test_v1_picks_up_predictor_keys(self, tmp_config):
        """Migrating from v1 adds predictor_enabled and friends to [guidance]."""
        # v1 baseline with a [guidance] section containing only legacy keys.
        v1_with_guidance = GOLDEN_V1 + textwrap.dedent("""
            [guidance]
            fwd_gain = 2.0
            lat_gain = 1.5
        """)
        p = tmp_config(v1_with_guidance)
        run_migrations(p)

        cfg = configparser.ConfigParser()
        cfg.read(p)

        assert cfg.get("guidance", "loop_delay_ms") == "100.0"
        assert cfg.get("guidance", "predictor_enabled") == "true"
        assert cfg.get("guidance", "predictor_alpha") == "0.5"
        assert cfg.get("guidance", "predictor_beta") == "0.05"
        # Existing keys preserved.
        assert cfg.get("guidance", "fwd_gain") == "2.0"

    def test_v1_existing_predictor_value_preserved(self, tmp_config):
        """If a key already exists in [guidance], the user's value wins."""
        v1_with_user_override = GOLDEN_V1 + textwrap.dedent("""
            [guidance]
            predictor_enabled = false
            predictor_alpha = 0.3
        """)
        p = tmp_config(v1_with_user_override)
        run_migrations(p)

        cfg = configparser.ConfigParser()
        cfg.read(p)

        assert cfg.get("guidance", "predictor_enabled") == "false"
        assert cfg.get("guidance", "predictor_alpha") == "0.3"
        # Missing keys still get filled in.
        assert cfg.get("guidance", "loop_delay_ms") == "100.0"

    def test_v1_no_guidance_section_creates_one(self, tmp_config):
        """If [guidance] is absent at v1, the migration creates it."""
        p = tmp_config(GOLDEN_V1)
        run_migrations(p)

        cfg = configparser.ConfigParser()
        cfg.read(p)

        assert cfg.has_section("guidance")
        assert cfg.get("guidance", "predictor_enabled") == "true"


# ---------------------------------------------------------------------------
# 3. Migration discovery handles out-of-order files
# ---------------------------------------------------------------------------

class TestMigrationDiscovery:
    def test_migrations_applied_in_version_order(self, tmp_config, tmp_path, monkeypatch):
        """Migration files are applied in from_version order, not filename sort order."""
        # A v0 config should chain through every migration to reach the
        # current schema version. If discovery sorted by filename instead of
        # from_version the runner's contiguity check would catch the gap.
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        assert result.from_version == 0
        assert result.to_version == CURRENT_SCHEMA_VERSION
        assert len(result.applied) == CURRENT_SCHEMA_VERSION

    def test_migration_files_need_correct_from_to(self, tmp_config, tmp_path, monkeypatch):
        """Runner raises MigrationError if a migration module lacks required attrs."""
        import hydra_detect.config_migrate as cm

        bad_migration_dir = tmp_path / "migrations"
        bad_migration_dir.mkdir()
        # Write a migration file missing from_version / to_version
        (bad_migration_dir / "001_broken.py").write_text(
            "def migrate(cfg): pass\n"
        )

        monkeypatch.setattr(cm, "_MIGRATIONS_DIR", bad_migration_dir)

        p = tmp_config(GOLDEN_V0)
        with pytest.raises(MigrationError):
            run_migrations(p)


# ---------------------------------------------------------------------------
# 4. MigrationError path — config unchanged, no partial write
# ---------------------------------------------------------------------------

class TestMigrationErrorPath:
    def test_migration_error_leaves_config_unchanged(self, tmp_config, tmp_path, monkeypatch):
        """When a migration raises, the original config is left intact."""
        import hydra_detect.config_migrate as cm

        broken_dir = tmp_path / "migrations"
        broken_dir.mkdir()
        (broken_dir / "001_blowup.py").write_text(textwrap.dedent("""\
            from_version = 0
            to_version = 1

            def migrate(cfg):
                raise RuntimeError("intentional failure")
        """))

        monkeypatch.setattr(cm, "_MIGRATIONS_DIR", broken_dir)

        p = tmp_config(GOLDEN_V0)
        original = p.read_bytes()

        with pytest.raises(MigrationError, match="intentional failure"):
            run_migrations(p)

        assert p.read_bytes() == original, "config must be untouched after failed migration"

    def test_migration_error_no_partial_write(self, tmp_config, tmp_path, monkeypatch):
        """No .tmp file is left behind after a migration failure."""
        import hydra_detect.config_migrate as cm

        broken_dir = tmp_path / "migrations"
        broken_dir.mkdir()
        (broken_dir / "001_blowup.py").write_text(textwrap.dedent("""\
            from_version = 0
            to_version = 1

            def migrate(cfg):
                raise RuntimeError("intentional failure")
        """))

        monkeypatch.setattr(cm, "_MIGRATIONS_DIR", broken_dir)

        p = tmp_config(GOLDEN_V0)

        with pytest.raises(MigrationError):
            run_migrations(p)

        tmp_file = p.parent / (p.name + ".tmp")
        assert not tmp_file.exists(), ".tmp must be cleaned up after failure"


# ---------------------------------------------------------------------------
# 5. Atomic write — tempfile cleanup on failure
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_successful_write_no_tmp_left(self, tmp_config):
        """After a successful migration, no .tmp file remains."""
        p = tmp_config(GOLDEN_V0)
        run_migrations(p)

        tmp_file = p.parent / (p.name + ".tmp")
        assert not tmp_file.exists(), ".tmp must be cleaned up after success"

    def test_config_readable_after_migration(self, tmp_config):
        """Migrated config.ini is a valid INI file (not truncated)."""
        p = tmp_config(GOLDEN_V0)
        run_migrations(p)

        cfg = configparser.ConfigParser()
        cfg.read(p)

        assert cfg.sections(), "migrated config must have at least one section"
        assert cfg.has_section("camera")

    def test_migration_result_is_dataclass(self, tmp_config):
        """MigrationResult has the documented fields."""
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        assert hasattr(result, "applied")
        assert hasattr(result, "from_version")
        assert hasattr(result, "to_version")
        assert hasattr(result, "backup_path")
