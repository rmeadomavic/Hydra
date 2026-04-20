"""Retroactive safety-invariant tests — one per item in the Phase 2 handoff
safety gate (`design_handoff_hydra_alignment/README.md`).

These tests are the mechanised version of
``docs/safety-review-2026-04-20.md``. They lock in the invariants so a
future drift (e.g. someone collapsing SW+HW arm into a single field, or
weakening abort's try/except, or introducing a websocket) fails in CI
instead of in the field.
"""

from __future__ import annotations

import ast
import logging
import pathlib
import re
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest

from hydra_detect.approach import ApproachConfig, ApproachController, ApproachMode
from hydra_detect.autonomous import AutonomousController
from hydra_detect.tak.tak_input import TAKInput


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HD_ROOT = REPO_ROOT / "hydra_detect"


# ── shared fixtures ──────────────────────────────────────────────────

@pytest.fixture
def audit_records(caplog):
    """Capture hydra.audit logger output at WARNING+ for rejection assertions."""
    caplog.set_level(logging.INFO, logger="hydra.audit")
    return caplog


def _mavlink_stub(**overrides):
    mav = MagicMock()
    mav.get_vehicle_mode.return_value = "AUTO"
    mav.get_lat_lon.return_value = (34.05, -118.25, 10.0)
    mav.estimate_target_position.return_value = (34.051, -118.251)
    mav.command_guided_to.return_value = True
    mav.set_mode.return_value = True
    mav.set_servo.return_value = True
    mav.get_rc_channels.return_value = [1500] * 16
    for k, v in overrides.items():
        getattr(mav, k).return_value = v
    return mav


def _tak_input(**kwargs) -> TAKInput:
    defaults = dict(
        listen_port=0,
        multicast_group="",
        on_lock=MagicMock(return_value=True),
        on_strike=MagicMock(return_value=True),
        on_unlock=MagicMock(),
        allowed_callsigns=["ALPHA-1"],
        my_callsign="HYDRA-1",
    )
    defaults.update(kwargs)
    return TAKInput(**defaults)


def _geochat_bytes(remarks: str, sender: str = "ALPHA-1") -> bytes:
    event = ET.Element("event")
    event.set("type", "b-t-f")
    event.set("uid", f"GeoChat.{sender}.All.{id(remarks)}")
    detail = ET.SubElement(event, "detail")
    chat = ET.SubElement(detail, "__chat")
    chat.set("senderCallsign", sender)
    remarks_el = ET.SubElement(detail, "remarks")
    remarks_el.text = remarks
    return ET.tostring(event, encoding="unicode").encode("utf-8")


# ── Invariant 1: SW arm and HW arm are distinct interlocks ───────────

def test_sw_and_hw_arm_distinct_fields_in_status():
    """get_status() for STRIKE mode exposes software_arm and
    hardware_arm_status as two separate fields. Collapsing into one
    'armed' bool would hide a failure mode from the operator."""
    mav = _mavlink_stub()
    cfg = ApproachConfig(arm_channel=7, hw_arm_channel=8)
    ctrl = ApproachController(mav, cfg)
    assert ctrl.start_strike(track_id=42)

    status = ctrl.get_status()
    assert "software_arm" in status
    assert "hardware_arm_status" in status
    assert status["software_arm"] is True
    # hardware_arm_status is tri-state (True/False/None); just assert type.
    assert status["hardware_arm_status"] in (True, False, None)


def test_arm_channel_and_hw_arm_channel_are_separate_config_keys():
    """ApproachConfig dataclass keeps arm_channel (SW) and
    hw_arm_channel (HW) as independent fields."""
    cfg = ApproachConfig(arm_channel=7, hw_arm_channel=8)
    assert cfg.arm_channel == 7
    assert cfg.hw_arm_channel == 8
    # Default to None — neither is set implicitly from the other.
    blank = ApproachConfig()
    assert blank.arm_channel is None
    assert blank.hw_arm_channel is None


# ── Invariant 2: TAK ingestion fails closed with audit log ───────────

def test_tak_empty_allowlist_rejects_and_audits(audit_records):
    """Empty allowlist must fail closed AND emit TAK_CMD_REJECTED."""
    tak = _tak_input(allowed_callsigns=[])
    tak._handle_datagram(_geochat_bytes("HYDRA LOCK 5"))
    audit_lines = [
        r.getMessage() for r in audit_records.records
        if r.name == "hydra.audit"
    ]
    assert any("TAK_CMD_REJECTED" in line and "no_allowlist" in line
               for line in audit_lines), audit_lines
    assert tak._on_lock.call_count == 0


def test_tak_hmac_invalid_rejects_and_audits(audit_records):
    """A bad HMAC must reject the command and emit an audit line."""
    tak = _tak_input(hmac_secret="shhh")
    tak._handle_datagram(_geochat_bytes("HYDRA LOCK 5|HMAC:deadbeef"))
    audit_lines = [
        r.getMessage() for r in audit_records.records
        if r.name == "hydra.audit"
    ]
    assert any("TAK_CMD_REJECTED" in line and "hmac_invalid" in line
               for line in audit_lines), audit_lines
    assert tak._on_lock.call_count == 0


def test_tak_unauthorized_sender_rejects_and_audits(audit_records):
    """Sender not on allowlist must reject and audit."""
    tak = _tak_input(allowed_callsigns=["ALPHA-1"])
    tak._handle_datagram(_geochat_bytes("HYDRA LOCK 5", sender="HOSTILE-7"))
    audit_lines = [
        r.getMessage() for r in audit_records.records
        if r.name == "hydra.audit"
    ]
    assert any("TAK_CMD_REJECTED" in line and "unauthorized" in line
               for line in audit_lines), audit_lines
    assert tak._on_lock.call_count == 0


# ── Invariant 3: SIM mode never silent ───────────────────────────────

def test_sim_gps_flag_surfaces_to_stats_and_topbar():
    """`is_sim_gps` must be both (a) readable on MAVLinkIO and
    (b) reachable by the topbar SIM pill + (SIM) suffix helper."""
    # (a) MAVLinkIO exposes the is_sim_gps property.
    from hydra_detect import mavlink_io

    src = pathlib.Path(mavlink_io.__file__).read_text()
    assert "def is_sim_gps" in src, "MAVLinkIO.is_sim_gps property missing"

    # (b) facade.py pushes is_sim_gps into the stats_update dict every frame.
    facade_src = (HD_ROOT / "pipeline" / "facade.py").read_text()
    assert 'stats_update["is_sim_gps"]' in facade_src, \
        "pipeline/facade.py must publish is_sim_gps in stats_update"

    # (c) Frontend surfaces it via SIM pill + SIM dot + (SIM) suffix.
    base_html = (HD_ROOT / "web" / "templates" / "base.html").read_text()
    assert "sim-gps-pill" in base_html, "SIM pill element missing from base.html"
    main_js = (HD_ROOT / "web" / "static" / "js" / "main.js").read_text()
    assert "is_sim_gps" in main_js, "main.js must toggle SIM pill from is_sim_gps"
    sim_js = (HD_ROOT / "web" / "static" / "js" / "ui" / "sim-gps.js").read_text()
    assert "(SIM)" in sim_js, "sim-gps.js must append (SIM) suffix"


# ── Invariant 4: No new websockets ───────────────────────────────────

def test_no_websockets_in_hydra_detect():
    """No websocket server or client anywhere under hydra_detect/.
    Stats + tracks + audit all use the polling fan-out."""
    forbidden = re.compile(r"\bwebsocket\b|@app\.websocket|wss?://", re.IGNORECASE)
    hits: list[str] = []
    for path in HD_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".py", ".html", ".js"):
            continue
        text = path.read_text(errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if forbidden.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "Unexpected websocket references:\n" + "\n".join(hits)


# ── Invariant 5: Drop/strike require SW+HW+confirm (current 2-factor) ─

def test_strike_api_requires_explicit_confirmation():
    """POST /api/approach/strike without confirm=true must be rejected."""
    from fastapi.testclient import TestClient
    from hydra_detect.web import server as web_server

    # Configure auth so we can exercise the confirm gate (401 otherwise
    # fires before body validation).
    web_server.configure_auth("test-token")
    try:
        client = TestClient(web_server.app)
        resp = client.post(
            "/api/approach/strike/5",
            json={},  # missing confirm
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 400
        assert "confirm" in resp.json().get("error", "").lower()
    finally:
        web_server.configure_auth(None)


def test_strike_fails_closed_when_hw_arm_unknown():
    """With hw_arm_channel configured, strike aborts if the RC read
    returns None (unknown). None must be treated as unsafe, not safe."""
    mav = _mavlink_stub()
    mav.get_rc_channels.return_value = None  # RC unavailable
    cfg = ApproachConfig(arm_channel=7, hw_arm_channel=8)
    ctrl = ApproachController(mav, cfg)
    assert ctrl.start_strike(track_id=42)

    # Force an update — _update_strike must call abort() because
    # hardware arm status is unknown (None).
    track = MagicMock()
    track.x1, track.y1, track.x2, track.y2 = 100, 100, 200, 200
    track.track_id = 42
    ctrl.update(track, 640, 480)
    assert ctrl.mode == ApproachMode.IDLE, \
        "Strike must abort when HW arm status is unknown"


# ── Invariant 6: /api/abort always responds ──────────────────────────

def test_abort_endpoint_source_wraps_callbacks_in_try_except():
    """Parse web/server.py and assert api_abort's callback-invocation
    block is inside a try/except. A crash in on_set_mode_command must
    never prevent the instructor from seeing a response."""
    src = (HD_ROOT / "web" / "server.py").read_text()
    tree = ast.parse(src)

    api_abort = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef)
                and node.name == "api_abort"):
            api_abort = node
            break
    assert api_abort is not None, "api_abort handler not found in server.py"

    # Walk the body looking for a Call whose func.attr == "cb" (i.e.
    # cb(mode)) and verify it is transitively inside a Try.
    def _contained_in_try(target, node):
        class _Finder(ast.NodeVisitor):
            def __init__(self):
                self.found = False
                self.stack: list[ast.AST] = []

            def generic_visit(self, n):
                self.stack.append(n)
                if n is target:
                    self.found = any(isinstance(a, ast.Try) for a in self.stack)
                    self.stack.pop()
                    return
                super().generic_visit(n)
                self.stack.pop()

        f = _Finder()
        f.visit(node)
        return f.found

    # Find the cb(mode) Call node.
    cb_calls = [
        n for n in ast.walk(api_abort)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "cb"
    ]
    assert cb_calls, "api_abort must invoke the on_set_mode_command callback"
    for call in cb_calls:
        assert _contained_in_try(call, api_abort), \
            "cb(mode) call inside api_abort must be wrapped in try/except"


def test_abort_path_is_in_public_prefixes():
    """/api/abort must bypass auth — instructor safety exception.
    If this regresses, an unauthenticated instructor can't abort."""
    src = (HD_ROOT / "web" / "server.py").read_text()
    # _PUBLIC_PATH_PREFIXES tuple must include /api/abort.
    match = re.search(
        r"_PUBLIC_PATH_PREFIXES\s*=\s*\(([^)]*)\)", src, re.DOTALL,
    )
    assert match, "_PUBLIC_PATH_PREFIXES constant not found"
    assert "/api/abort" in match.group(1), \
        "/api/abort must be in _PUBLIC_PATH_PREFIXES so auth cannot block it"


# ── Invariant 7: Autonomy dry-run + inhibit toggleable at runtime ────

def test_autonomy_mode_and_suppressed_runtime_toggleable():
    """AutonomousController.set_mode() + .suppressed setter must both
    work at runtime (not only from config)."""
    ctrl = AutonomousController(enabled=True)

    # set_mode round-trip for every valid mode.
    for mode in ("dryrun", "shadow", "live"):
        ctrl.set_mode(mode)
        assert ctrl.get_mode() == mode

    # Invalid mode must raise — no silent downgrade to "dryrun".
    with pytest.raises(ValueError):
        ctrl.set_mode("bogus")

    # Inhibit toggle via .suppressed setter.
    assert ctrl.suppressed is False
    ctrl.suppressed = True
    assert ctrl.suppressed is True
    ctrl.suppressed = False
    assert ctrl.suppressed is False


# ── Invariant 8: MAVLink public API only ─────────────────────────────

def test_no_external_access_to_private_mav_or_send_lock():
    """No module outside mavlink_io.py may touch the pymavlink handle
    `._mav.mav` or the `_send_lock`. External modules must go through
    public helpers (send_raw_message, send_param_set, etc)."""
    mav_io_path = HD_ROOT / "mavlink_io.py"
    forbidden_patterns = [
        # Reaching into the pymavlink handle: something._mav.mav.*
        re.compile(r"\._mav\.mav\b"),
        # Reaching into the send lock of MAVLinkIO from outside.
        re.compile(r"\bmavlink\._send_lock\b"),
    ]

    violations: list[str] = []
    for path in HD_ROOT.rglob("*.py"):
        if path.resolve() == mav_io_path.resolve():
            continue  # Owning class gets to touch its own internals.
        text = path.read_text(errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            # self._mav assignments where the variable is the MAVLinkIO
            # reference (tak_output, osd, geo_tracking) are legal naming,
            # not private-access — only flag reaches through to the
            # pymavlink handle (._mav.mav.*) or the send lock.
            for pat in forbidden_patterns:
                if pat.search(line):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}"
                    )
    assert not violations, (
        "MAVLink public-API rule violated; use send_raw_message / "
        "send_param_set instead of reaching into _mav or _send_lock:\n"
        + "\n".join(violations)
    )
