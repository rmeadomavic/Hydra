"""Tests for OTA PR-B verify + pull pipeline (issue #152).

PR-B extends ``scripts/platform-update.sh`` to:

* Fetch ``manifest.json`` + ``manifest.json.sig`` from GitHub Releases
  (channel-aware: ``stable`` -> latest, ``beta`` -> the literal ``beta``
  pre-release tag).
* GPG-verify the signature against a pinned public key at
  ``/etc/hydra/ota-signing.pub``.
* Validate that the manifest's channel matches the box's configured
  channel (a stable box must never pull a beta manifest).
* Skip the pull when the manifest version already matches the running
  ``HYDRA_VERSION``.
* ``docker pull ghcr.io/rmeadomavic/hydra@sha256:<digest>`` using the
  digest from the verified manifest.
* Write ``/var/lib/hydra/last-update.json`` atomically (tmp+rename) with
  the schema ``{"ts": <unix>, "status": "ok"|"failed"|"up_to_date",
  "version": str, "digest": str, "channel": str, "reason": str?}``.

External tools are mocked via PATH-prepended fake binaries that log
their invocations and obey controlled exit codes, so the tests pass on
any host that has ``bash`` available (no real gpg/curl/docker required).

PR-C will add A/B promotion + healthcheck-gated rollout; PR-D will add
the operator dashboard. Neither is in scope here.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ---------------------------------------------------------------------------
# Repo paths and skip conditions
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_UPDATE_SH = REPO_ROOT / "scripts" / "platform-update.sh"


def _bash_available() -> bool:
    return shutil.which("bash") is not None


pytestmark = pytest.mark.skipif(
    not _bash_available(),
    reason="bash not on PATH (skipping platform-update.sh subprocess tests)",
)


# ---------------------------------------------------------------------------
# Fake-binary harness
# ---------------------------------------------------------------------------


def _write_fake_bin(
    bin_dir: Path,
    name: str,
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    log_path: Path | None = None,
    extra: str = "",
) -> Path:
    """Drop a shell-script fake into ``bin_dir`` named ``name``.

    The fake logs its full argv to ``log_path`` (one JSON object per
    invocation), optionally emits ``stdout``/``stderr``, runs ``extra``
    shell snippet (for side effects like ``cp`` of a fixture file), then
    exits with ``exit_code``.
    """
    script = bin_dir / name
    body = "#!/usr/bin/env bash\n"
    if log_path is not None:
        # Use python3 to emit a valid JSON line; argv may contain spaces
        # or shell metacharacters and naive printf is brittle.
        body += textwrap.dedent(
            f"""
            python3 - "$@" <<'PY_EOF' >> "{log_path}"
            import json, sys
            print(json.dumps({{"argv": sys.argv[1:], "name": "{name}"}}))
            PY_EOF
            """
        ).strip() + "\n"
    if stdout:
        # Use printf so we can embed newlines without quoting hell.
        body += f"printf '%s' {shlex_quote(stdout)}\n"
    if stderr:
        body += f"printf '%s' {shlex_quote(stderr)} 1>&2\n"
    if extra:
        body += extra + "\n"
    body += f"exit {exit_code}\n"
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def shlex_quote(s: str) -> str:
    """Single-quote ``s`` for safe inclusion in a POSIX shell snippet."""
    # Avoid importing shlex.quote at module top so the import section
    # stays grouped with the other stdlib imports.
    import shlex
    return shlex.quote(s)


def _read_log(log_path: Path) -> List[Dict[str, Any]]:
    if not log_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _calls_for(rows: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    return [r for r in rows if r.get("name") == name]


# ---------------------------------------------------------------------------
# Manifest + signature fixture
# ---------------------------------------------------------------------------


def _write_manifest(
    path: Path,
    *,
    channel: str = "stable",
    version: str = "abc1234",
    digest: str = "sha256:" + "0" * 64,
    released_at: str = "2026-05-26T00:00:00Z",
    schema: int = 1,
    drop_field: str | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "channel": channel,
        "version": version,
        "digest": digest,
        "released_at": released_at,
        "manifest_schema_version": schema,
    }
    if drop_field is not None:
        payload.pop(drop_field, None)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Common harness fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def harness(tmp_path: Path):
    """Wires a sandbox around ``platform-update.sh``.

    Returns a dict with:
      * ``env`` — environment to pass to ``subprocess.run``
      * ``bin_dir`` — PATH-prepended dir for fake binaries
      * ``log_path`` — JSONL log file fakes append to
      * ``manifest_src`` — file the fake ``curl`` "downloads"
      * ``sig_src`` — signature file the fake ``curl`` "downloads"
      * ``last_update_path`` — where the script writes last-update.json
      * ``key_path`` — pretend GPG keyring path
      * ``state_dir`` — parent of last-update.json
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "fake-calls.log"

    state_dir = tmp_path / "var-lib-hydra"
    state_dir.mkdir()
    last_update_path = state_dir / "last-update.json"

    etc_dir = tmp_path / "etc-hydra"
    etc_dir.mkdir()
    channel_file = etc_dir / "channel"
    channel_file.write_text("stable\n", encoding="utf-8")
    update_env = etc_dir / "update.env"
    update_env.write_text(
        "GHCR_REPO=ghcr.io/rmeadomavic/hydra\n"
        f"GPG_KEY_PATH={etc_dir / 'ota-signing.pub'}\n",
        encoding="utf-8",
    )
    key_path = etc_dir / "ota-signing.pub"
    key_path.write_text(
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake\n-----END PGP PUBLIC KEY BLOCK-----\n",
        encoding="utf-8",
    )

    manifest_src = tmp_path / "manifest.json"
    sig_src = tmp_path / "manifest.json.sig"
    _write_manifest(manifest_src)
    sig_src.write_text(
        "-----BEGIN PGP SIGNATURE-----\nfake-sig\n-----END PGP SIGNATURE-----\n",
        encoding="utf-8",
    )

    # Prepend our fake-bin dir to PATH. Keep the rest of PATH so bash,
    # python3, mktemp, etc. still resolve.
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["HYDRA_CHANNEL_PATH"] = str(channel_file)
    env["HYDRA_UPDATE_ENV_PATH"] = str(update_env)
    env["HYDRA_LAST_UPDATE_PATH"] = str(last_update_path)
    env["HYDRA_MANIFEST_SRC"] = str(manifest_src)
    env["HYDRA_SIG_SRC"] = str(sig_src)
    # Unset HYDRA_VERSION so "already up to date" comparison only fires
    # in the test that opts into it.
    env.pop("HYDRA_VERSION", None)

    return {
        "env": env,
        "bin_dir": bin_dir,
        "log_path": log_path,
        "manifest_src": manifest_src,
        "sig_src": sig_src,
        "last_update_path": last_update_path,
        "key_path": key_path,
        "state_dir": state_dir,
        "channel_file": channel_file,
        "update_env": update_env,
        "etc_dir": etc_dir,
        "tmp_path": tmp_path,
    }


def _install_default_fakes(
    harness: Dict[str, Any],
    *,
    curl_exit: int = 0,
    gpg_exit: int = 0,
    docker_exit: int = 0,
) -> None:
    """Install a happy-path set of fake curl/gpg/docker binaries."""
    bin_dir = harness["bin_dir"]
    log_path = harness["log_path"]
    manifest_src = harness["manifest_src"]
    sig_src = harness["sig_src"]

    # curl: when invoked with --output PATH URL, copy the matching
    # fixture file into PATH. Distinguish manifest vs. sig by URL
    # substring (".sig").
    curl_extra = textwrap.dedent(
        f"""
        out_path=""
        last_was_output=0
        for arg in "$@"; do
            if [ "$last_was_output" -eq 1 ]; then
                out_path="$arg"
                last_was_output=0
            fi
            case "$arg" in
                --output|-o) last_was_output=1 ;;
            esac
        done
        url_arg="${{!#}}"
        if [ -n "$out_path" ]; then
            case "$url_arg" in
                *.sig) cp -- "{sig_src}" "$out_path" ;;
                *)     cp -- "{manifest_src}" "$out_path" ;;
            esac
        fi
        """
    ).strip()
    _write_fake_bin(
        bin_dir, "curl",
        exit_code=curl_exit, log_path=log_path, extra=curl_extra,
    )

    # gpg: log and exit. Pull manifest path off argv to validate later.
    _write_fake_bin(bin_dir, "gpg", exit_code=gpg_exit, log_path=log_path)

    # docker: log and exit.
    _write_fake_bin(bin_dir, "docker", exit_code=docker_exit, log_path=log_path)


def _run_script(harness: Dict[str, Any]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(PLATFORM_UPDATE_SH)],
        env=harness["env"],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_writes_ok_record(harness: Dict[str, Any]) -> None:
    """Manifest fetched -> verified -> channel matches -> digest pulled."""
    _install_default_fakes(harness)

    result = _run_script(harness)

    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # last-update.json reflects ok status with all PR-B fields populated.
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "ok"
    assert record["version"] == "abc1234"
    assert record["digest"] == "sha256:" + "0" * 64
    assert record["channel"] == "stable"
    assert isinstance(record["ts"], int)
    assert record["ts"] > 0

    # docker pull was actually invoked with the digest-pinned ref.
    rows = _read_log(harness["log_path"])
    docker_calls = _calls_for(rows, "docker")
    assert len(docker_calls) >= 1
    pull_args = docker_calls[0]["argv"]
    assert pull_args[0] == "pull"
    assert pull_args[1] == f"ghcr.io/rmeadomavic/hydra@sha256:{'0' * 64}"

    # gpg verify was called against the downloaded sig + manifest.
    gpg_calls = _calls_for(rows, "gpg")
    assert len(gpg_calls) >= 1
    assert "--verify" in gpg_calls[0]["argv"]


def test_happy_path_stable_uses_latest_release_url(harness: Dict[str, Any]) -> None:
    """The stable channel hits the ``/latest/download/manifest.json`` URL."""
    _install_default_fakes(harness)
    _run_script(harness)

    rows = _read_log(harness["log_path"])
    curl_calls = _calls_for(rows, "curl")
    assert curl_calls, "expected curl to be invoked at least once"
    urls = [c["argv"][-1] for c in curl_calls]
    assert any("/latest/download/manifest.json" in u for u in urls), urls
    assert any("/latest/download/manifest.json.sig" in u for u in urls), urls


def test_beta_channel_uses_beta_tag_url(harness: Dict[str, Any]) -> None:
    """Beta channel resolves to the literal ``download/beta`` URL."""
    harness["channel_file"].write_text("beta\n", encoding="utf-8")
    # Beta manifest must also self-declare ``channel: beta`` or the
    # channel-match guard bails.
    _write_manifest(harness["manifest_src"], channel="beta")
    _install_default_fakes(harness)

    result = _run_script(harness)
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    rows = _read_log(harness["log_path"])
    urls = [c["argv"][-1] for c in _calls_for(rows, "curl")]
    assert any("/download/beta/manifest.json" in u for u in urls), urls


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_channel_mismatch_bails(harness: Dict[str, Any]) -> None:
    """Manifest claims beta but box is on stable -> fail, no pull."""
    _write_manifest(harness["manifest_src"], channel="beta")
    _install_default_fakes(harness)

    result = _run_script(harness)

    assert result.returncode != 0, "expected non-zero exit on channel mismatch"

    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["reason"] == "channel_mismatch"

    # No docker pull on channel mismatch.
    rows = _read_log(harness["log_path"])
    assert _calls_for(rows, "docker") == []


def test_gpg_verify_failure_bails(harness: Dict[str, Any]) -> None:
    """``gpg --verify`` non-zero exit -> failed, signature_invalid reason."""
    _install_default_fakes(harness, gpg_exit=1)

    result = _run_script(harness)

    assert result.returncode != 0
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["reason"] == "signature_invalid"

    # No docker pull when signature is bad.
    rows = _read_log(harness["log_path"])
    assert _calls_for(rows, "docker") == []


def test_missing_signing_key_records_failure_but_exits_zero(
    harness: Dict[str, Any],
) -> None:
    """Missing GPG key: timer must not error, but operator must see the failure."""
    # Remove the keyring file the script will look for.
    harness["key_path"].unlink()
    _install_default_fakes(harness)

    result = _run_script(harness)

    # systemd timer policy: an error here would mark the unit failed
    # and stop further checks until the operator clears it. Better to
    # exit 0 with a clearly-marked last-update record.
    assert result.returncode == 0, (
        f"expected exit 0 to keep the timer alive; got {result.returncode}, "
        f"stderr={result.stderr!r}"
    )
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["reason"] == "missing_signing_key"

    # No gpg/docker calls should have happened.
    rows = _read_log(harness["log_path"])
    assert _calls_for(rows, "gpg") == []
    assert _calls_for(rows, "docker") == []


def test_manifest_fetch_failure_bails(harness: Dict[str, Any]) -> None:
    """curl non-zero on manifest fetch -> failed, manifest_fetch_failed."""
    _install_default_fakes(harness, curl_exit=22)  # 22 == HTTP error

    result = _run_script(harness)

    assert result.returncode != 0
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["reason"] == "manifest_fetch_failed"

    rows = _read_log(harness["log_path"])
    assert _calls_for(rows, "gpg") == []
    assert _calls_for(rows, "docker") == []


def test_already_current_skips_pull(harness: Dict[str, Any]) -> None:
    """``HYDRA_VERSION`` matches manifest -> exit 0, status=up_to_date."""
    # Manifest version (abc1234) == running version.
    harness["env"]["HYDRA_VERSION"] = "abc1234"
    _install_default_fakes(harness)

    result = _run_script(harness)

    assert result.returncode == 0
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "up_to_date"
    assert record["version"] == "abc1234"

    rows = _read_log(harness["log_path"])
    # No docker pull when we're already on the right version.
    assert _calls_for(rows, "docker") == []


def test_docker_pull_failure_bails(harness: Dict[str, Any]) -> None:
    """``docker pull`` non-zero -> failed, docker_pull_failed reason."""
    _install_default_fakes(harness, docker_exit=1)

    result = _run_script(harness)

    assert result.returncode != 0
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["reason"] == "docker_pull_failed"
    # Digest should still be recorded for forensics even though the pull failed.
    assert record["digest"] == "sha256:" + "0" * 64


def test_malformed_manifest_bails(harness: Dict[str, Any]) -> None:
    """Manifest missing a required field -> failed, manifest_invalid."""
    _write_manifest(harness["manifest_src"], drop_field="digest")
    _install_default_fakes(harness)

    result = _run_script(harness)

    assert result.returncode != 0
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["reason"] == "manifest_invalid"

    # docker pull never happens without a valid digest.
    rows = _read_log(harness["log_path"])
    assert _calls_for(rows, "docker") == []


# ---------------------------------------------------------------------------
# Atomic-write guarantee
# ---------------------------------------------------------------------------


def test_last_update_write_is_atomic(harness: Dict[str, Any]) -> None:
    """Pre-existing last-update.json survives intact if the new write fails.

    The script writes to a tmp file in the same directory then renames
    onto last-update.json. If the rename has not happened, the reader
    sees the old contents — never a half-written file. We simulate this
    by writing a sentinel old record, forcing the script to crash mid-flow
    (via docker_exit=1), and asserting that the file content is the NEW
    failed-record, not a truncated/zero-byte file.
    """
    old_record = {
        "ts": 1000,
        "status": "ok",
        "version": "old-version",
        "digest": "sha256:" + "1" * 64,
        "channel": "stable",
    }
    harness["last_update_path"].write_text(
        json.dumps(old_record), encoding="utf-8"
    )
    _install_default_fakes(harness, docker_exit=1)

    result = _run_script(harness)
    assert result.returncode != 0

    # File must parse as valid JSON — never a zero-byte / truncated state.
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert isinstance(record, dict)
    assert record["status"] == "failed"
    assert record["reason"] == "docker_pull_failed"

    # No leftover tmp file polluting the state dir.
    leftovers = [
        p.name for p in harness["state_dir"].iterdir()
        if p.name != "last-update.json"
    ]
    assert leftovers == [], f"unexpected tmp leftovers: {leftovers}"


# ---------------------------------------------------------------------------
# Channel-allowlist guard (R3-1 from PR-A adversarial)
# ---------------------------------------------------------------------------


def test_invalid_channel_in_file_bails(harness: Dict[str, Any]) -> None:
    """``/etc/hydra/channel`` containing a non-allowlisted value never
    reaches docker pull (defends against R3-1 from PR-A's adversarial).
    """
    harness["channel_file"].write_text("evil-channel\n", encoding="utf-8")
    _install_default_fakes(harness)

    result = _run_script(harness)
    assert result.returncode != 0
    record = json.loads(harness["last_update_path"].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    # Either invalid_channel or channel_mismatch is acceptable — the
    # invariant is "no pull happens and the operator sees the reason."
    assert record["reason"] in {"invalid_channel", "channel_mismatch"}

    rows = _read_log(harness["log_path"])
    assert _calls_for(rows, "docker") == []


# ---------------------------------------------------------------------------
# Schema sanity for downstream readers
# ---------------------------------------------------------------------------


def test_last_update_schema_compatible_with_version_surface(
    harness: Dict[str, Any],
) -> None:
    """Recorded payload still parses via ``_read_last_update``.

    PR-A's ``version_surface._read_last_update`` is the reader on
    ``/api/health``. PR-B is the first writer. The reader is permissive
    (any JSON object), so this test is a smoke check that nothing in the
    PR-B write path introduces a non-JSON-serialisable value.
    """
    from hydra_detect.observability.version_surface import _read_last_update

    _install_default_fakes(harness)
    _run_script(harness)

    os.environ["HYDRA_LAST_UPDATE_PATH"] = str(harness["last_update_path"])
    try:
        record = _read_last_update()
    finally:
        os.environ.pop("HYDRA_LAST_UPDATE_PATH", None)

    assert record is not None
    assert record["status"] == "ok"
    assert record["version"] == "abc1234"
    # PR-B fields are new but must round-trip through the reader.
    assert record["digest"].startswith("sha256:")
    assert record["channel"] == "stable"
