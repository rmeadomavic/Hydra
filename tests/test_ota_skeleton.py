"""Tests for OTA PR-A skeleton (issue #152).

PR-A only ships the timer + channel-file + health-surface scaffolding.
These tests verify:
  * ``scripts/platform-update.sh`` reads ``$HYDRA_CHANNEL_PATH`` and
    falls back to ``stable`` when the file is absent.
  * ``GET /api/health`` exposes ``version`` / ``channel`` / ``last_update``
    fields with the expected shape and defensive defaults.
  * ``_read_last_update()`` survives a corrupted JSON file without
    raising — the health endpoint must never 500 on a bad status file.

PR-B will add tests for actual signing + image pull; PR-C for A/B
healthcheck-driven promotion. None of that is exercised here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Repo paths — independent of cwd so the tests pass from any caller.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_UPDATE_SH = REPO_ROOT / "scripts" / "platform-update.sh"


# ---------------------------------------------------------------------------
# Bash script behaviour
# ---------------------------------------------------------------------------


def _bash_available() -> bool:
    return shutil.which("bash") is not None


pytestmark_bash = pytest.mark.skipif(
    not _bash_available(),
    reason="bash not on PATH (skipping platform-update.sh subprocess tests)",
)


@pytestmark_bash
def test_platform_update_sh_reads_channel(tmp_path: Path) -> None:
    """When the channel file is present, the script logs that channel.

    PR-B note: the script now attempts a fetch + verify when the
    signing key is missing it records ``failed`` and exits 0 so the
    timer stays armed. ``HYDRA_LAST_UPDATE_PATH`` is pinned at a
    tmp file so the script doesn't try to write to ``/var/lib/hydra``.
    """
    channel_file = tmp_path / "channel"
    channel_file.write_text("beta\n", encoding="utf-8")

    env = os.environ.copy()
    env["HYDRA_CHANNEL_PATH"] = str(channel_file)
    # Point the update.env at a path that does NOT exist — the script
    # must still succeed and fall through to defaults.
    env["HYDRA_UPDATE_ENV_PATH"] = str(tmp_path / "nope.env")
    env["HYDRA_LAST_UPDATE_PATH"] = str(tmp_path / "last-update.json")

    result = subprocess.run(
        ["bash", str(PLATFORM_UPDATE_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # PR-B: missing /etc/hydra/ota-signing.pub is the expected outcome
    # in a tmp-dir sandbox — script must exit 0 (timer-friendly) and
    # log the channel selection before the key check fails.
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert "[platform-update]" in result.stdout
    assert "channel=beta" in result.stdout


@pytestmark_bash
def test_platform_update_sh_default_stable(tmp_path: Path) -> None:
    """Absent channel file -> script logs ``channel=stable``."""
    missing = tmp_path / "does-not-exist"

    env = os.environ.copy()
    env["HYDRA_CHANNEL_PATH"] = str(missing)
    env["HYDRA_UPDATE_ENV_PATH"] = str(tmp_path / "nope.env")
    env["HYDRA_LAST_UPDATE_PATH"] = str(tmp_path / "last-update.json")

    result = subprocess.run(
        ["bash", str(PLATFORM_UPDATE_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert "channel=stable" in result.stdout


# ---------------------------------------------------------------------------
# version_surface unit tests
# ---------------------------------------------------------------------------


def test_version_surface_handles_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garbage in the last-update file returns None, never raises."""
    from hydra_detect.observability.version_surface import _read_last_update

    bad = tmp_path / "last-update.json"
    bad.write_text("{this is not valid json", encoding="utf-8")
    monkeypatch.setenv("HYDRA_LAST_UPDATE_PATH", str(bad))

    assert _read_last_update() is None


def test_version_surface_handles_non_object_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A JSON array (valid JSON, wrong shape) also returns None."""
    from hydra_detect.observability.version_surface import _read_last_update

    arr = tmp_path / "last-update.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv("HYDRA_LAST_UPDATE_PATH", str(arr))

    assert _read_last_update() is None


def test_version_surface_channel_strips_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trailing newline + whitespace must not bleed into ``body["channel"]``."""
    from hydra_detect.observability.version_surface import _read_channel_file

    chan = tmp_path / "channel"
    chan.write_text("  beta   \n", encoding="utf-8")
    monkeypatch.setenv("HYDRA_CHANNEL_PATH", str(chan))

    assert _read_channel_file() == "beta"


# ---------------------------------------------------------------------------
# /api/health surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    # Import lazily so the version_surface monkeypatches in individual
    # tests are applied before the server module evaluates anything.
    from hydra_detect.web import server as server_module
    return TestClient(server_module.app)


def test_health_includes_version_channel_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /api/health`` exposes the three new OTA fields."""
    # Pin env so we get deterministic values regardless of where the
    # test runs (no /etc/hydra access on dev hosts).
    monkeypatch.setenv("HYDRA_VERSION", "test-sha-abc123")
    monkeypatch.setenv("HYDRA_CHANNEL_PATH", "/nonexistent-channel-path")
    monkeypatch.setenv("HYDRA_LAST_UPDATE_PATH", "/nonexistent-last-update.json")

    resp = client.get("/api/health")
    body = resp.json()

    assert "version" in body
    assert "channel" in body
    assert "last_update" in body
    assert body["version"] == "test-sha-abc123"
    assert body["channel"] == "stable"  # default when channel file missing
    assert body["last_update"] is None


def test_health_last_update_null_when_absent(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing last-update.json -> ``body["last_update"]`` is JSON null."""
    monkeypatch.setenv(
        "HYDRA_LAST_UPDATE_PATH", str(tmp_path / "definitely-not-there.json")
    )

    body = client.get("/api/health").json()
    assert body["last_update"] is None


def test_health_last_update_parsed_when_present(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed last-update.json surfaces as a dict on /api/health."""
    payload = {"ts": 1717000000, "status": "ok", "version": "abc1234"}
    last_update = tmp_path / "last-update.json"
    last_update.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("HYDRA_LAST_UPDATE_PATH", str(last_update))

    body = client.get("/api/health").json()
    assert isinstance(body["last_update"], dict)
    assert body["last_update"] == payload


def test_health_channel_reads_file_when_present(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the channel file exists, its value surfaces on /api/health."""
    chan = tmp_path / "channel"
    chan.write_text("beta\n", encoding="utf-8")
    monkeypatch.setenv("HYDRA_CHANNEL_PATH", str(chan))

    body = client.get("/api/health").json()
    assert body["channel"] == "beta"
