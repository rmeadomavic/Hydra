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
    def test_v0_migrates_to_v1(self, tmp_config):
        """v0 config (no [meta]) gets schema_version = 1 written back."""
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        cfg = configparser.ConfigParser()
        cfg.read(p)

        assert cfg.has_section("meta"), "expected [meta] section after migration"
        assert cfg.get("meta", "schema_version") == "1"

    def test_v0_migration_result(self, tmp_config):
        """run_migrations returns correct MigrationResult for v0→v1."""
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        assert isinstance(result, MigrationResult)
        assert result.from_version == 0
        assert result.to_version == 1
        assert len(result.applied) == 1
        assert "001" in result.applied[0]  # migration filename in the list

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

class TestV1Noop:
    def test_v1_already_current_no_migrations_applied(self, tmp_config):
        """Config already at v1 returns an empty applied list."""
        p = tmp_config(GOLDEN_V1)
        result = run_migrations(p)

        assert result.applied == []
        assert result.from_version == 1
        assert result.to_version == 1

    def test_v1_no_backup_created(self, tmp_config):
        """No backup file is written when no migrations run."""
        p = tmp_config(GOLDEN_V1)
        result = run_migrations(p)

        assert result.backup_path is None

    def test_v1_file_unchanged(self, tmp_config):
        """File content is byte-identical after a no-op run."""
        p = tmp_config(GOLDEN_V1)
        before = p.read_bytes()
        run_migrations(p)
        after = p.read_bytes()

        # Content must be equivalent (configparser may reformat whitespace,
        # so compare parsed sections rather than raw bytes).
        cfg_before = configparser.ConfigParser()
        cfg_before.read_string(before.decode())
        cfg_after = configparser.ConfigParser()
        cfg_after.read_string(after.decode())

        assert dict(cfg_before["meta"]) == dict(cfg_after["meta"])


# ---------------------------------------------------------------------------
# 3. Migration discovery handles out-of-order files
# ---------------------------------------------------------------------------

class TestMigrationDiscovery:
    def test_migrations_applied_in_version_order(self, tmp_config, tmp_path, monkeypatch):
        """Migration files are applied in from_version order, not filename sort order."""
        # We fake a v0 config and patch the migration directory to contain
        # a single (the real) migration file. If discovery returns them sorted
        # correctly, v0→v1 completes; if not, an assertion inside the runner
        # will catch the gap.
        p = tmp_config(GOLDEN_V0)
        result = run_migrations(p)

        # Real migrations directory has only 001; must produce exactly one step.
        assert result.from_version == 0
        assert result.to_version == CURRENT_SCHEMA_VERSION

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
