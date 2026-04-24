"""Tests for per-unit identity generation, persistence, and first-boot hook.

Covers:
- generate_identity produces correct shape
- API token has minimum entropy and valid charset
- Password hash round-trip (generate -> hash -> verify)
- maybe_generate_identity logs warning on missing identity, noop on populated
- snapshot_if_healthy writes config.ini.lkg atomically
- restore_lkg restores atomically, noop if no snapshot
- Config schema accepts empty [identity] (fresh install) and fully populated
"""

from __future__ import annotations

import configparser
import logging
import re
import string

import pytest


# ---------------------------------------------------------------------------
# identity.py
# ---------------------------------------------------------------------------

class TestGenerateIdentity:
    def test_shape(self):
        from hydra_detect.identity import generate_identity, UnitIdentity
        identity, pw = generate_identity(3, "ugv")
        assert isinstance(identity, UnitIdentity)
        assert identity.hostname == "hydra-03"
        assert identity.callsign == "HYDRA-03-UGV"
        assert identity.api_token
        assert identity.web_password_hash
        assert identity.software_version  # may be "unknown" in test env
        assert identity.commit_hash       # may be "unknown"
        assert identity.generated_at
        assert isinstance(pw, str)
        assert len(pw) > 0

    def test_callsign_format(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(7, "drone")
        assert identity.callsign == "HYDRA-07-DRONE"

    def test_hostname_format(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(12, "usv")
        assert identity.hostname == "hydra-12"

    def test_unit_number_padding(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(1, "fw")
        assert identity.callsign == "HYDRA-01-FW"
        assert identity.hostname == "hydra-01"

    def test_profile_uppercased(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(5, "UGV")
        assert identity.callsign == "HYDRA-05-UGV"

    def test_invalid_unit_number_low(self):
        from hydra_detect.identity import generate_identity
        with pytest.raises(ValueError):
            generate_identity(0, "ugv")

    def test_invalid_unit_number_high(self):
        from hydra_detect.identity import generate_identity
        with pytest.raises(ValueError):
            generate_identity(100, "ugv")

    def test_invalid_profile(self):
        from hydra_detect.identity import generate_identity
        with pytest.raises(ValueError):
            generate_identity(1, "")

    def test_generated_at_iso8601(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(1, "ugv")
        # Should look like 2026-04-23T12:34:56Z
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", identity.generated_at)

    def test_each_call_produces_unique_token(self):
        from hydra_detect.identity import generate_identity
        identity1, pw1 = generate_identity(1, "ugv")
        identity2, pw2 = generate_identity(1, "ugv")
        assert identity1.api_token != identity2.api_token
        assert pw1 != pw2


class TestApiTokenEntropy:
    def test_token_min_length(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(1, "ugv")
        # token_urlsafe(32) produces 43 base64url chars
        assert len(identity.api_token) >= 32

    def test_token_charset(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(1, "ugv")
        valid_chars = set(string.ascii_letters + string.digits + "-_")
        assert all(c in valid_chars for c in identity.api_token)

    def test_token_redacted(self):
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(1, "ugv")
        redacted = identity.token_redacted()
        assert redacted.endswith("***")
        assert identity.api_token[:4] == redacted[:4]


class TestPasswordHashRoundtrip:
    def test_verify_correct_password(self):
        from hydra_detect.identity import _hash_password, verify_password
        pw = "heron-stone-lantern-oak"
        hashed = _hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_reject_wrong_password(self):
        from hydra_detect.identity import _hash_password, verify_password
        pw = "heron-stone-lantern-oak"
        hashed = _hash_password(pw)
        assert verify_password("wrong-password", hashed) is False

    def test_hash_format(self):
        from hydra_detect.identity import _hash_password
        hashed = _hash_password("test")
        parts = hashed.split(":")
        assert len(parts) == 5
        assert parts[0] == "pbkdf2"
        assert parts[1] == "sha256"
        assert int(parts[2]) >= 100_000  # minimum iterations

    def test_two_hashes_differ(self):
        """Same password hashed twice must produce different hashes (random salt)."""
        from hydra_detect.identity import _hash_password
        h1 = _hash_password("same-password")
        h2 = _hash_password("same-password")
        assert h1 != h2

    def test_invalid_hash_returns_false(self):
        from hydra_detect.identity import verify_password
        assert verify_password("anything", "not-a-real-hash") is False
        assert verify_password("anything", "") is False

    def test_generate_identity_password_verifies(self):
        from hydra_detect.identity import generate_identity, verify_password
        identity, plaintext = generate_identity(2, "ugv")
        assert verify_password(plaintext, identity.web_password_hash) is True

    def test_wrong_password_against_identity_hash(self):
        from hydra_detect.identity import generate_identity, verify_password
        identity, _ = generate_identity(2, "ugv")
        assert verify_password("wrong", identity.web_password_hash) is False


class TestPassphraseWordlist:
    def test_passphrase_dashes(self):
        from hydra_detect.identity import _generate_passphrase
        pw = _generate_passphrase(4)
        words = pw.split("-")
        assert len(words) == 4

    def test_passphrase_words_are_alpha(self):
        from hydra_detect.identity import _generate_passphrase
        pw = _generate_passphrase(4)
        for word in pw.split("-"):
            assert word.isalpha(), f"Non-alpha word in passphrase: {word!r}"


class TestCallsignValidation:
    def test_valid_callsigns(self):
        from hydra_detect.identity import is_callsign_valid
        assert is_callsign_valid("HYDRA-03-UGV") is True
        assert is_callsign_valid("HYDRA-01-DRONE") is True
        assert is_callsign_valid("HYDRA-99-FW") is True

    def test_invalid_callsigns(self):
        from hydra_detect.identity import is_callsign_valid
        assert is_callsign_valid("HYDRA-1") is False   # old format: no leading zero, no profile
        assert is_callsign_valid("HYDRA-3-UGV") is False    # missing leading zero
        assert is_callsign_valid("") is False
        assert is_callsign_valid("SOMETHING-ELSE") is False

    def test_lowercase_callsign_valid_after_normalize(self):
        from hydra_detect.identity import is_callsign_valid
        # is_callsign_valid normalizes to upper before matching — lowercase is accepted
        assert is_callsign_valid("hydra-03-ugv") is True


class TestPersistence:
    def test_write_and_load(self, tmp_path):
        from hydra_detect.identity import (
            generate_identity, write_identity_to_config, load_identity_from_config,
        )
        config_path = tmp_path / "config.ini"
        # Minimal config to start with
        config_path.write_text("[camera]\nsource = auto\n")

        identity, _ = generate_identity(5, "ugv")
        write_identity_to_config(identity, config_path)

        loaded = load_identity_from_config(config_path)
        assert loaded is not None
        assert loaded.callsign == identity.callsign
        assert loaded.hostname == identity.hostname
        assert loaded.api_token == identity.api_token
        assert loaded.web_password_hash == identity.web_password_hash

    def test_load_returns_none_when_missing(self, tmp_path):
        from hydra_detect.identity import load_identity_from_config
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        assert load_identity_from_config(config_path) is None

    def test_load_returns_none_when_partial(self, tmp_path):
        from hydra_detect.identity import load_identity_from_config
        config_path = tmp_path / "config.ini"
        config_path.write_text("[identity]\nhostname = hydra-03\n")
        # Missing required fields -> should return None
        assert load_identity_from_config(config_path) is None

    def test_write_preserves_existing_sections(self, tmp_path):
        from hydra_detect.identity import generate_identity, write_identity_to_config
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = /dev/video0\n[detector]\nyolo_model = test.pt\n")

        identity, _ = generate_identity(1, "drone")
        write_identity_to_config(identity, config_path)

        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        assert cfg.has_section("camera")
        assert cfg.get("camera", "source") == "/dev/video0"
        assert cfg.has_section("identity")

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        from hydra_detect.identity import generate_identity, write_identity_to_config
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        identity, _ = generate_identity(1, "ugv")
        write_identity_to_config(identity, config_path)
        tmp = tmp_path / "config.ini.tmp"
        assert not tmp.exists()


# ---------------------------------------------------------------------------
# identity_boot.py
# ---------------------------------------------------------------------------

class TestMaybeGenerateIdentity:
    def test_warns_on_missing_identity(self, tmp_path, caplog):
        from hydra_detect.identity_boot import maybe_generate_identity
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        with caplog.at_level(logging.WARNING):
            maybe_generate_identity(config_path)
        assert any("not set" in r.message or "Platform Setup" in r.message
                   for r in caplog.records)

    def test_noop_on_populated_identity(self, tmp_path, caplog):
        from hydra_detect.identity import generate_identity, write_identity_to_config
        from hydra_detect.identity_boot import maybe_generate_identity
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        identity, _ = generate_identity(3, "ugv")
        write_identity_to_config(identity, config_path)

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            maybe_generate_identity(config_path)
        # Should log INFO, not WARNING
        warning_msgs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_msgs) == 0

    def test_warns_on_placeholder_callsign(self, tmp_path, caplog):
        """Callsign HYDRA-00-UGV is the golden-image placeholder — must warn."""
        from hydra_detect.identity import UnitIdentity, _hash_password, write_identity_to_config
        from hydra_detect.identity_boot import maybe_generate_identity
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        # Build the placeholder identity directly (unit 0 is rejected by
        # generate_identity, but can arrive via golden-image copy without setup).
        ident = UnitIdentity(
            hostname="hydra-00",
            callsign="HYDRA-00-UGV",
            api_token="abc" * 15,
            web_password_hash=_hash_password("test"),
            software_version="2.1.0",
            commit_hash="deadbeef" * 5,
            generated_at="2026-01-01T00:00:00Z",
        )
        write_identity_to_config(ident, config_path)
        with caplog.at_level(logging.WARNING):
            maybe_generate_identity(config_path)
        assert any("placeholder" in r.message or "HYDRA-00" in r.message
                   for r in caplog.records)

    def test_warns_on_invalid_callsign_format(self, tmp_path, caplog):
        from hydra_detect.identity import UnitIdentity, _hash_password, write_identity_to_config
        from hydra_detect.identity_boot import maybe_generate_identity
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        ident = UnitIdentity(
            hostname="hydra-01",
            callsign="HYDRA-1",  # old format — no leading zero, no profile
            api_token="a" * 43,
            web_password_hash=_hash_password("test"),
            software_version="2.1.0",
            commit_hash="abc123",
            generated_at="2026-01-01T00:00:00Z",
        )
        write_identity_to_config(ident, config_path)
        with caplog.at_level(logging.WARNING):
            maybe_generate_identity(config_path)
        assert any("invalid" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# config_lkg.py
# ---------------------------------------------------------------------------

class TestSnapshotIfHealthy:
    def test_writes_lkg_on_healthy(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy, has_lkg
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        result = snapshot_if_healthy(config_path, lambda: True)
        assert result is True
        assert has_lkg(config_path)
        lkg = tmp_path / "config.ini.lkg"
        assert lkg.exists()
        assert "camera" in lkg.read_text()

    def test_no_lkg_when_unhealthy(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy, has_lkg
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        result = snapshot_if_healthy(config_path, lambda: False)
        assert result is False
        assert not has_lkg(config_path)

    def test_no_lkg_when_health_check_raises(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy, has_lkg
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")

        def bad_check():
            raise RuntimeError("health check failed")

        result = snapshot_if_healthy(config_path, bad_check)
        assert result is False
        assert not has_lkg(config_path)

    def test_no_tmp_left_after_write(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        snapshot_if_healthy(config_path, lambda: True)
        tmp = tmp_path / "config.ini.lkg.tmp"
        assert not tmp.exists()

    def test_missing_config_returns_false(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy
        config_path = tmp_path / "nonexistent.ini"
        result = snapshot_if_healthy(config_path, lambda: True)
        assert result is False

    def test_overwrites_stale_lkg(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        snapshot_if_healthy(config_path, lambda: True)

        config_path.write_text("[camera]\nsource = /dev/video0\n")
        snapshot_if_healthy(config_path, lambda: True)

        lkg = tmp_path / "config.ini.lkg"
        assert "/dev/video0" in lkg.read_text()


class TestRestoreLkg:
    def test_restores_from_lkg(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy, restore_lkg
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        snapshot_if_healthy(config_path, lambda: True)

        config_path.write_text("[camera]\nsource = /dev/video9\n")
        result = restore_lkg(config_path)
        assert result is True
        assert "auto" in config_path.read_text()

    def test_returns_false_when_no_lkg(self, tmp_path):
        from hydra_detect.config_lkg import restore_lkg
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        result = restore_lkg(config_path)
        assert result is False

    def test_no_tmp_left_after_restore(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy, restore_lkg
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        snapshot_if_healthy(config_path, lambda: True)
        restore_lkg(config_path)
        tmp = tmp_path / "config.ini.tmp"
        assert not tmp.exists()

    def test_has_lkg(self, tmp_path):
        from hydra_detect.config_lkg import snapshot_if_healthy, has_lkg
        config_path = tmp_path / "config.ini"
        config_path.write_text("[camera]\nsource = auto\n")
        assert not has_lkg(config_path)
        snapshot_if_healthy(config_path, lambda: True)
        assert has_lkg(config_path)


# ---------------------------------------------------------------------------
# config_schema.py — identity section validation
# ---------------------------------------------------------------------------

class TestSchemaIdentitySection:
    def _read_config(self, text: str) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.read_string(text)
        return cfg

    def test_fresh_install_no_identity_section(self):
        """Fresh install: no [identity] section — schema must accept this."""
        from hydra_detect.config_schema import validate_config
        cfg = self._read_config("[camera]\nsource_type = auto\n")
        result = validate_config(cfg)
        # Absence of [identity] should produce no errors (all fields optional)
        identity_errors = [e for e in result.errors if "identity" in e]
        assert identity_errors == [], identity_errors

    def test_fully_populated_identity_valid(self):
        """Fully populated [identity] section must pass schema validation."""
        from hydra_detect.config_schema import validate_config
        from hydra_detect.identity import generate_identity
        identity, _ = generate_identity(3, "ugv")
        cfg_text = (
            "[camera]\nsource_type = auto\n"
            "[identity]\n"
            f"hostname = {identity.hostname}\n"
            f"callsign = {identity.callsign}\n"
            f"api_token = {identity.api_token}\n"
            f"web_password_hash = {identity.web_password_hash}\n"
            f"software_version = {identity.software_version}\n"
            f"commit_hash = {identity.commit_hash}\n"
            f"generated_at = {identity.generated_at}\n"
        )
        cfg = self._read_config(cfg_text)
        result = validate_config(cfg)
        identity_errors = [e for e in result.errors if "identity" in e]
        assert identity_errors == [], identity_errors

    def test_empty_identity_section_no_errors(self):
        """All-empty [identity] section (post-factory-reset stub) — no errors."""
        from hydra_detect.config_schema import validate_config
        cfg_text = (
            "[camera]\nsource_type = auto\n"
            "[identity]\n"
            "hostname = \n"
            "callsign = \n"
            "api_token = \n"
            "web_password_hash = \n"
            "software_version = \n"
            "commit_hash = \n"
            "generated_at = \n"
        )
        cfg = self._read_config(cfg_text)
        result = validate_config(cfg)
        identity_errors = [e for e in result.errors if "identity" in e]
        assert identity_errors == [], identity_errors

    def test_unknown_key_in_identity_warns(self):
        """Unknown key in [identity] generates a warning (typo detection)."""
        from hydra_detect.config_schema import validate_config
        cfg_text = (
            "[camera]\nsource_type = auto\n"
            "[identity]\n"
            "unknown_field = something\n"
        )
        cfg = self._read_config(cfg_text)
        result = validate_config(cfg)
        identity_warnings = [w for w in result.warnings if "identity" in w]
        assert len(identity_warnings) > 0
