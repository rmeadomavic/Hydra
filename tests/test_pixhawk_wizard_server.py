"""Server-level tests for the Pixhawk wizard endpoints (#158 PR-A follow-ups).

Covers the four findings from PR #264's adversarial doc that live in
``server.py`` rather than the pure-function ``pixhawk_wizard`` module:

* **R3-1** — post-apply re-read pass: the apply endpoint replaces each row's
  ``post_value`` with the FC-reported value re-read after the apply loop
  completes, so the operator-visible field reflects the FC's actual current
  state (defeating multi-writer races against Mission Planner on a telemetry
  radio).
* **R1-4** — path-traversal anchor: ``output_data/missions`` is resolved from
  ``__file__``, not CWD, so /restore's escape-rejection holds even when
  uvicorn is launched from a different directory.
* **R3-4** — callsign fallback: ``_pixhawk_backup_path`` defaults to
  ``"HYDRA"`` when runtime_config has no callsign, matching ``MAVLinkIO`` /
  ``BatteryMonitor``.

Pure-function tests for type fidelity (R1-5) and the wizard-level
``reread_params`` helper live in ``tests/test_pixhawk_wizard.py``.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web import pixhawk_wizard, server as srv_mod
from hydra_detect.web.server import (
    _PIXHAWK_DEFAULT_CALLSIGN,
    _PIXHAWK_MISSIONS_ROOT,
    _pixhawk_backup_path,
    _pixhawk_callsign_from_runtime,
    app,
    configure_auth,
    configure_web_password,
    stream_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset auth + runtime state between tests (mirrors test_web_api)."""
    configure_auth(None)
    configure_web_password(None)
    stream_state.target_lock = {"locked": False, "track_id": None, "mode": None, "label": None}
    stream_state.runtime_config = {"prompts": ["person"], "threshold": 0.25, "auto_loiter": False}
    stream_state._callbacks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _make_param_value(name: str, value: float, ptype: int = 9) -> SimpleNamespace:
    return SimpleNamespace(
        param_id=name,
        param_value=float(value),
        param_type=int(ptype),
        get_type=lambda: "PARAM_VALUE",
    )


# ---------------------------------------------------------------------------
# R3-4 — callsign default
# ---------------------------------------------------------------------------

def test_callsign_from_runtime_returns_none_when_missing():
    """No callsign in runtime_config → returns None (caller substitutes default)."""
    stream_state.runtime_config = {"prompts": ["person"]}  # no callsign key
    assert _pixhawk_callsign_from_runtime() is None


def test_callsign_from_runtime_returns_configured_value():
    stream_state.runtime_config = {"callsign": "DRONE-7"}
    assert _pixhawk_callsign_from_runtime() == "DRONE-7"


def test_backup_path_falls_back_to_hydra_when_callsign_none(tmp_path, monkeypatch):
    """Default callsign is 'HYDRA', not 'unknown' — matches MAVLinkIO/BatteryMonitor."""
    monkeypatch.setattr(srv_mod, "_PIXHAWK_MISSIONS_ROOT", tmp_path / "output_data" / "missions")
    p = _pixhawk_backup_path(None)
    assert p.parent.name == "HYDRA"
    assert _PIXHAWK_DEFAULT_CALLSIGN == "HYDRA"  # documented constant


def test_backup_path_falls_back_to_hydra_when_callsign_blank(tmp_path, monkeypatch):
    """Whitespace-only callsign falls back to HYDRA (defensive against bad config)."""
    monkeypatch.setattr(srv_mod, "_PIXHAWK_MISSIONS_ROOT", tmp_path / "output_data" / "missions")
    p = _pixhawk_backup_path("   ")
    assert p.parent.name == "HYDRA"


def test_backup_path_preserves_configured_callsign(tmp_path, monkeypatch):
    """A real callsign is preserved (sanitized but not replaced)."""
    monkeypatch.setattr(srv_mod, "_PIXHAWK_MISSIONS_ROOT", tmp_path / "output_data" / "missions")
    p = _pixhawk_backup_path("DRONE-7")
    assert p.parent.name == "DRONE-7"


# ---------------------------------------------------------------------------
# R1-4 — path-traversal anchor (independent of CWD)
# ---------------------------------------------------------------------------

def test_missions_root_resolves_from_file_not_cwd(tmp_path, monkeypatch):
    """The missions root is the repo-anchored path, not Path('output_data')/missions
    resolved against whatever CWD the process happens to have.
    """
    # Sanity: import-time constant is the absolute, repo-anchored root.
    # It must be absolute (not a CWD-relative Path("output_data")/...) and
    # must end in output_data/missions.
    assert _PIXHAWK_MISSIONS_ROOT.is_absolute()
    assert _PIXHAWK_MISSIONS_ROOT.name == "missions"
    assert _PIXHAWK_MISSIONS_ROOT.parent.name == "output_data"
    # CWD-switch must not change where backups land
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        p = _pixhawk_backup_path("HYDRA")
        # The backup path is under the anchored missions root, not under tmp_path
        assert str(p).startswith(str(_PIXHAWK_MISSIONS_ROOT))
        assert not str(p).startswith(str(tmp_path))
    finally:
        os.chdir(original_cwd)


def test_restore_rejects_cwd_relative_escape(tmp_path, monkeypatch, client):
    """Anchor uses ``__file__``, not CWD — a fake missions tree under the CWD
    does NOT widen the allow-list.

    Pre-fix code did ``Path("output_data") / "missions"`` resolved against
    process CWD. If an attacker could ``chdir`` the process or simply launch
    it from a temp directory, then any file under
    ``CWD/output_data/missions/`` would pass the constraint — even though
    that's not the repo's missions root. The fixed code anchors to the
    package location, so a fake tree at the CWD is correctly rejected.
    """
    # Build a fake "missions" tree under tmp_path and drop a backup file in it
    fake_missions = tmp_path / "output_data" / "missions" / "ATTACKER"
    fake_missions.mkdir(parents=True)
    bogus = fake_missions / "pre-wizard-params-fake.json"
    bogus.write_text(json.dumps({"backup": {}}), encoding="utf-8")

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        resp = client.post(
            "/api/platform/setup/pixhawk/restore",
            json={"conn": "tcp:127.0.0.1:14550", "backup_path": str(bogus)},
        )
    finally:
        os.chdir(original_cwd)

    # Anchored check rejects the fake tree even though it superficially looks
    # like output_data/missions/ relative to CWD.
    assert resp.status_code == 400
    assert "output_data/missions" in resp.json()["error"]


# ---------------------------------------------------------------------------
# R3-1 — authoritative post-apply re-read pass
# ---------------------------------------------------------------------------

def _fake_open_connection(*_args, **_kwargs) -> MagicMock:
    """Return a MagicMock that quacks like a mavutil connection."""
    conn = MagicMock()
    conn.target_system = 1
    conn.target_component = 1
    return conn


def test_apply_re_read_overrides_post_value(tmp_path, monkeypatch, client):
    """post_value is the re-read value, not the in-flight PARAM_VALUE ack.

    Scenario: wizard applies FENCE_ENABLE=1. An interloper (Mission Planner on
    telemetry radio) immediately pushes FENCE_ENABLE=0, and the PARAM_VALUE
    the wizard sees as its 'ack' is the interloper's value. The re-read pass
    then asks the FC for the current value, which is still 0. The apply
    response surfaces 0, not 1, so the operator sees the truth.
    """
    monkeypatch.setattr(srv_mod, "_PIXHAWK_MISSIONS_ROOT", tmp_path / "output_data" / "missions")

    # Live collection: FENCE_ENABLE currently 0 (we want to change it to 1)
    monkeypatch.setattr(
        srv_mod,
        "_pixhawk_collect_live_params_with_types",
        lambda conn, timeout=None: ({"FENCE_ENABLE": 0.0}, {"FENCE_ENABLE": 1}),
    )

    # capture_backup: snapshot pre-change
    monkeypatch.setattr(
        pixhawk_wizard,
        "capture_backup",
        lambda conn, names, per_name_timeout=1.0: {n: 0.0 for n in names},
    )

    # apply_pack: returns the interloper's value as post_value
    monkeypatch.setattr(
        pixhawk_wizard,
        "apply_pack",
        lambda conn, diff, live_param_types=None, **_kw: [
            {"name": "FENCE_ENABLE", "applied": True, "error": None, "post_value": 0.0},
        ],
    )

    # reread_params: the authoritative call — FC actually reports 0.0
    monkeypatch.setattr(
        pixhawk_wizard,
        "reread_params",
        lambda conn, names, per_name_timeout=1.0: {n: 0.0 for n in names},
    )

    # Stub the param pack loader so we don't depend on a profile on disk
    monkeypatch.setattr(
        pixhawk_wizard,
        "load_param_pack",
        lambda profile: [("FENCE_ENABLE", 1.0)],
    )

    monkeypatch.setattr(srv_mod, "_pixhawk_open_connection", _fake_open_connection)

    # First compute the diff hash via /diff so the apply call passes the
    # freshness check.
    monkeypatch.setattr(
        srv_mod,
        "_pixhawk_collect_live_params",
        lambda conn, timeout=None: {"FENCE_ENABLE": 0.0},
    )
    diff_resp = client.get(
        "/api/platform/setup/pixhawk/diff",
        params={"profile": "drone_10in", "conn": "tcp:127.0.0.1:14550"},
    )
    assert diff_resp.status_code == 200
    diff_hash = diff_resp.json()["diff_hash"]

    resp = client.post(
        "/api/platform/setup/pixhawk/apply",
        json={
            "profile": "drone_10in",
            "conn": "tcp:127.0.0.1:14550",
            "confirmed_diff_hash": diff_hash,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The re-read value (0.0) overrode the apply-loop post_value
    assert body["results"][0]["post_value"] == pytest.approx(0.0)
    assert body["results"][0]["applied"] is True  # apply itself succeeded
    # And the response carries the re-read timestamp
    assert "re_read_at" in body
    # ISO8601 UTC: ends with Z, contains T separator
    assert body["re_read_at"].endswith("Z")
    assert "T" in body["re_read_at"]


def test_apply_re_read_value_replaces_ack_value(tmp_path, monkeypatch, client):
    """When the re-read returns a different value than the apply-loop saw, the
    response carries the re-read value.

    Distinct from the multi-writer scenario above: here the apply loop
    'observed' the value we wrote (1.0) but the FC's actual current value is
    something else (3.0 — what the interloper landed on after a separate
    race). The re-read pass is authoritative.
    """
    monkeypatch.setattr(srv_mod, "_PIXHAWK_MISSIONS_ROOT", tmp_path / "output_data" / "missions")
    monkeypatch.setattr(
        srv_mod,
        "_pixhawk_collect_live_params_with_types",
        lambda conn, timeout=None: ({"ARMING_CHECK": 1.0}, {"ARMING_CHECK": 6}),
    )
    monkeypatch.setattr(
        srv_mod,
        "_pixhawk_collect_live_params",
        lambda conn, timeout=None: {"ARMING_CHECK": 1.0},
    )
    monkeypatch.setattr(
        pixhawk_wizard,
        "capture_backup",
        lambda conn, names, per_name_timeout=1.0: {"ARMING_CHECK": 1.0},
    )
    monkeypatch.setattr(
        pixhawk_wizard,
        "apply_pack",
        lambda conn, diff, live_param_types=None, **_kw: [
            {"name": "ARMING_CHECK", "applied": True, "error": None, "post_value": 65535.0},
        ],
    )
    # Authoritative re-read: FC reports 3.0 (interloper landed between the
    # ack and the re-read)
    monkeypatch.setattr(
        pixhawk_wizard,
        "reread_params",
        lambda conn, names, per_name_timeout=1.0: {"ARMING_CHECK": 3.0},
    )
    monkeypatch.setattr(
        pixhawk_wizard,
        "load_param_pack",
        lambda profile: [("ARMING_CHECK", 65535.0)],
    )
    monkeypatch.setattr(srv_mod, "_pixhawk_open_connection", _fake_open_connection)

    diff_resp = client.get(
        "/api/platform/setup/pixhawk/diff",
        params={"profile": "drone_10in", "conn": "tcp:127.0.0.1:14550"},
    )
    diff_hash = diff_resp.json()["diff_hash"]

    resp = client.post(
        "/api/platform/setup/pixhawk/apply",
        json={
            "profile": "drone_10in",
            "conn": "tcp:127.0.0.1:14550",
            "confirmed_diff_hash": diff_hash,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Re-read value (3.0) replaces the apply-loop's 65535.0
    assert body["results"][0]["post_value"] == pytest.approx(3.0)


def test_apply_re_read_timeout_marks_post_value_null(tmp_path, monkeypatch, client):
    """A re-read timeout on one param yields post_value=None for that param
    but does not fail the apply response.
    """
    monkeypatch.setattr(srv_mod, "_PIXHAWK_MISSIONS_ROOT", tmp_path / "output_data" / "missions")
    monkeypatch.setattr(
        srv_mod,
        "_pixhawk_collect_live_params_with_types",
        lambda conn, timeout=None: ({"FENCE_ENABLE": 0.0}, {"FENCE_ENABLE": 1}),
    )
    monkeypatch.setattr(
        srv_mod,
        "_pixhawk_collect_live_params",
        lambda conn, timeout=None: {"FENCE_ENABLE": 0.0},
    )
    monkeypatch.setattr(
        pixhawk_wizard,
        "capture_backup",
        lambda conn, names, per_name_timeout=1.0: {n: 0.0 for n in names},
    )
    monkeypatch.setattr(
        pixhawk_wizard,
        "apply_pack",
        lambda conn, diff, live_param_types=None, **_kw: [
            {"name": "FENCE_ENABLE", "applied": True, "error": None, "post_value": 1.0},
        ],
    )
    # Re-read times out — wizard returns None for that name
    monkeypatch.setattr(
        pixhawk_wizard,
        "reread_params",
        lambda conn, names, per_name_timeout=1.0: {n: None for n in names},
    )
    monkeypatch.setattr(
        pixhawk_wizard,
        "load_param_pack",
        lambda profile: [("FENCE_ENABLE", 1.0)],
    )
    monkeypatch.setattr(srv_mod, "_pixhawk_open_connection", _fake_open_connection)

    diff_resp = client.get(
        "/api/platform/setup/pixhawk/diff",
        params={"profile": "drone_10in", "conn": "tcp:127.0.0.1:14550"},
    )
    diff_hash = diff_resp.json()["diff_hash"]

    resp = client.post(
        "/api/platform/setup/pixhawk/apply",
        json={
            "profile": "drone_10in",
            "conn": "tcp:127.0.0.1:14550",
            "confirmed_diff_hash": diff_hash,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Re-read timeout → post_value is None, apply status untouched
    assert body["results"][0]["post_value"] is None
    assert body["results"][0]["applied"] is True
    assert "re_read_at" in body
