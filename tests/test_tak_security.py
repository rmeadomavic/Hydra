"""Tests for TAK command authentication, callsign routing, and duplicate detection."""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

from hydra_detect.tak.tak_input import TAKInput, _callsign_matches


# ── helpers ──────────────────────────────────────────────────────────

def _build_geochat(
    remarks: str,
    sender: str = "ALPHA-1",
) -> bytes:
    """Build a minimal GeoChat CoT event."""
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", f"GeoChat.{sender}.All.{id(remarks)}")
    event.set("type", "b-t-f")
    event.set("time", "2026-03-26T12:00:00Z")
    event.set("start", "2026-03-26T12:00:00Z")
    event.set("stale", "2026-03-26T12:01:00Z")
    event.set("how", "h-g-i-g-o")
    point = ET.SubElement(event, "point")
    point.set("lat", "0")
    point.set("lon", "0")
    point.set("hae", "0")
    point.set("ce", "9999999")
    point.set("le", "9999999")
    detail = ET.SubElement(event, "detail")
    chat = ET.SubElement(detail, "__chat")
    chat.set("chatroom", "All Chat Rooms")
    chat.set("senderCallsign", sender)
    remarks_el = ET.SubElement(detail, "remarks")
    remarks_el.text = remarks
    return ET.tostring(event, encoding="unicode").encode("utf-8")


def _build_custom_cot(
    cot_type: str,
    track_id: int | None = None,
    sender_callsign: str = "ALPHA-1",
) -> bytes:
    """Build a custom a-x-hydra-* CoT event with sender callsign."""
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", "HYDRA-CMD-TEST")
    event.set("type", cot_type)
    event.set("time", "2026-03-26T12:00:00Z")
    event.set("start", "2026-03-26T12:00:00Z")
    event.set("stale", "2026-03-26T12:01:00Z")
    event.set("how", "h-e")
    point = ET.SubElement(event, "point")
    point.set("lat", "0")
    point.set("lon", "0")
    point.set("hae", "0")
    point.set("ce", "9999999")
    point.set("le", "9999999")
    detail = ET.SubElement(event, "detail")
    # Add contact element with sender callsign
    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", sender_callsign)
    if track_id is not None:
        hydra = ET.SubElement(detail, "hydra")
        hydra.set("trackId", str(track_id))
    return ET.tostring(event, encoding="unicode").encode("utf-8")


def _build_sa_event(callsign: str, uid: str = "OTHER-SA") -> bytes:
    """Build a situational awareness CoT event (friendly air)."""
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", uid)
    event.set("type", "a-f-A-M-F-Q")
    event.set("time", "2026-03-26T12:00:00Z")
    event.set("start", "2026-03-26T12:00:00Z")
    event.set("stale", "2026-03-26T12:01:00Z")
    event.set("how", "m-g")
    point = ET.SubElement(event, "point")
    point.set("lat", "34.0")
    point.set("lon", "-118.0")
    point.set("hae", "100")
    point.set("ce", "10")
    point.set("le", "10")
    detail = ET.SubElement(event, "detail")
    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", callsign)
    return ET.tostring(event, encoding="unicode").encode("utf-8")


def _make_input(**kwargs) -> TAKInput:
    """Create a TAKInput with mock callbacks (not started)."""
    return TAKInput(
        listen_port=16969,
        multicast_group="",
        on_lock=kwargs.get("on_lock", MagicMock(return_value=True)),
        on_strike=kwargs.get("on_strike", MagicMock(return_value=True)),
        on_unlock=kwargs.get("on_unlock", MagicMock()),
        allowed_callsigns=kwargs.get("allowed_callsigns"),
        hmac_secret=kwargs.get("hmac_secret"),
        my_callsign=kwargs.get("my_callsign", "HYDRA-1"),
    )


def _sign_hmac(text: str, secret: str) -> str:
    """Compute HMAC-SHA256 and return text with |HMAC: suffix."""
    sig = hmac_mod.new(
        secret.encode("utf-8"),
        text.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{text}|HMAC:{sig}"


# =====================================================================
# Group A: Callsign allowlist (fail-closed)
# =====================================================================

class TestCallsignAllowlist:
    def test_empty_allowlist_rejects_all(self):
        """Fail-closed: no allowed callsigns configured means all commands rejected."""
        ti = _make_input(allowed_callsigns=None)
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ti._on_lock.assert_not_called()
        assert ti._commands_rejected >= 1

    def test_empty_list_rejects_all(self):
        """Explicitly empty list rejects all commands."""
        ti = _make_input(allowed_callsigns=[])
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ti._on_lock.assert_not_called()

    def test_authorized_callsign_allowed(self):
        """An authorized sender can issue commands."""
        ti = _make_input(allowed_callsigns=["ALPHA-1"])
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ti._on_lock.assert_called_once_with(5)

    def test_unauthorized_callsign_rejected(self):
        """A sender not in the allowlist is rejected."""
        ti = _make_input(allowed_callsigns=["BRAVO-2"])
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ti._on_lock.assert_not_called()
        assert ti._commands_rejected >= 1

    def test_case_insensitive_match(self):
        """Callsign matching is case-insensitive."""
        ti = _make_input(allowed_callsigns=["alpha-1"])
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ti._on_lock.assert_called_once_with(5)

    def test_multiple_allowed_callsigns(self):
        """Multiple callsigns in allowlist all work."""
        ti = _make_input(allowed_callsigns=["ALPHA-1", "BRAVO-2"])
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="BRAVO-2"))
        ti._on_lock.assert_called_once_with(5)

    def test_custom_cot_also_checked(self):
        """Allowlist also applies to custom CoT types."""
        ti = _make_input(allowed_callsigns=["ALPHA-1"])
        ti._handle_datagram(_build_custom_cot(
            "a-x-hydra-l", track_id=5, sender_callsign="UNAUTHORIZED"
        ))
        ti._on_lock.assert_not_called()

    def test_custom_cot_authorized(self):
        """Authorized callsign works with custom CoT types."""
        ti = _make_input(allowed_callsigns=["ALPHA-1"])
        ti._handle_datagram(_build_custom_cot(
            "a-x-hydra-l", track_id=5, sender_callsign="ALPHA-1"
        ))
        ti._on_lock.assert_called_once_with(5)


# =====================================================================
# Group B: HMAC verification
# =====================================================================

class TestHMACVerification:
    def test_no_secret_allows_all(self):
        """Without HMAC secret, commands pass without signature."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            hmac_secret=None,
        )
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ti._on_lock.assert_called_once_with(5)

    def test_valid_hmac_accepted(self):
        """Valid HMAC signature is accepted."""
        secret = "my-shared-secret"
        msg = "HYDRA LOCK 5"
        signed = _sign_hmac(msg, secret)
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            hmac_secret=secret,
        )
        ti._handle_datagram(_build_geochat(signed, sender="ALPHA-1"))
        ti._on_lock.assert_called_once_with(5)

    def test_invalid_hmac_rejected(self):
        """Invalid HMAC signature is rejected."""
        secret = "my-shared-secret"
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            hmac_secret=secret,
        )
        ti._handle_datagram(
            _build_geochat("HYDRA LOCK 5|HMAC:badbadbad", sender="ALPHA-1")
        )
        ti._on_lock.assert_not_called()
        assert ti._commands_rejected >= 1

    def test_missing_hmac_when_required_rejected(self):
        """Missing HMAC when secret is configured is rejected."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            hmac_secret="my-secret",
        )
        ti._handle_datagram(_build_geochat("HYDRA LOCK 5", sender="ALPHA-1"))
        ti._on_lock.assert_not_called()
        assert ti._commands_rejected >= 1

    def test_hmac_wrong_secret(self):
        """HMAC signed with wrong secret is rejected."""
        msg = "HYDRA LOCK 5"
        signed = _sign_hmac(msg, "wrong-secret")
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            hmac_secret="correct-secret",
        )
        ti._handle_datagram(_build_geochat(signed, sender="ALPHA-1"))
        ti._on_lock.assert_not_called()


# =====================================================================
# Group C: Callsign-based command routing
# =====================================================================

class TestCallsignRouting:
    def test_exact_match(self):
        """Exact callsign match processes command."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-2-USV LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_called_once_with(5)

    def test_bare_hydra_backwards_compat(self):
        """Bare 'HYDRA' prefix matches any HYDRA-* callsign."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_called_once_with(5)

    def test_wrong_callsign_rejected(self):
        """Command addressed to different vehicle is ignored."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-3-DRONE LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_not_called()

    def test_hydra_all_wildcard(self):
        """HYDRA-ALL matches any vehicle."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-ALL LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_called_once_with(5)

    def test_team_wildcard_usv(self):
        """HYDRA-ALL-USV matches vehicles with USV in callsign."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-ALL-USV LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_called_once_with(5)

    def test_team_wildcard_no_match(self):
        """HYDRA-ALL-DRONE does not match USV vehicle."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-ALL-DRONE LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_not_called()

    def test_instance_wildcard(self):
        """HYDRA-2-ALL matches vehicles with -2- in callsign."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-2-ALL LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_called_once_with(5)

    def test_instance_wildcard_no_match(self):
        """HYDRA-3-ALL does not match vehicle with -2- in callsign."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-3-ALL LOCK 5", sender="ALPHA-1")
        )
        ti._on_lock.assert_not_called()


# =====================================================================
# Group D: _callsign_matches unit tests
# =====================================================================

class TestCallsignMatchesFunc:
    def test_exact(self):
        assert _callsign_matches("HYDRA-1", "HYDRA-1") is True

    def test_exact_case_insensitive(self):
        assert _callsign_matches("hydra-1", "HYDRA-1") is True

    def test_bare_hydra(self):
        assert _callsign_matches("HYDRA", "HYDRA-2-USV") is True

    def test_bare_hydra_exact(self):
        assert _callsign_matches("HYDRA", "HYDRA") is True

    def test_full_wildcard(self):
        assert _callsign_matches("HYDRA-ALL", "HYDRA-99-DRONE") is True

    def test_team_wildcard(self):
        assert _callsign_matches("HYDRA-ALL-USV", "HYDRA-2-USV") is True

    def test_team_wildcard_no_match(self):
        assert _callsign_matches("HYDRA-ALL-DRONE", "HYDRA-2-USV") is False

    def test_instance_wildcard(self):
        assert _callsign_matches("HYDRA-2-ALL", "HYDRA-2-USV") is True

    def test_instance_wildcard_no_match(self):
        assert _callsign_matches("HYDRA-3-ALL", "HYDRA-2-USV") is False

    def test_no_match(self):
        assert _callsign_matches("EAGLE-1", "HYDRA-1") is False

    def test_non_hydra_bare(self):
        """Bare 'HYDRA' does NOT match non-HYDRA callsigns."""
        assert _callsign_matches("HYDRA", "EAGLE-1") is False


# =====================================================================
# Group E: Duplicate callsign detection
# =====================================================================

class TestDuplicateCallsignDetection:
    def test_detects_duplicate_callsign(self):
        """Seeing another node with the same callsign sets the flag."""
        ti = _make_input(my_callsign="HYDRA-1")
        sa_data = _build_sa_event("HYDRA-1", uid="OTHER-NODE-SA")
        ti._handle_datagram(sa_data)
        assert ti._duplicate_callsign is True

    def test_different_callsign_no_flag(self):
        """Seeing a different callsign does not set the flag."""
        ti = _make_input(my_callsign="HYDRA-1")
        sa_data = _build_sa_event("HYDRA-2", uid="OTHER-NODE-SA")
        ti._handle_datagram(sa_data)
        assert ti._duplicate_callsign is False

    def test_own_sa_event_not_flagged(self):
        """Our own SA event (matching UID prefix) is not flagged."""
        ti = _make_input(my_callsign="HYDRA-1")
        sa_data = _build_sa_event("HYDRA-1", uid="HYDRA-1-SA")
        ti._handle_datagram(sa_data)
        assert ti._duplicate_callsign is False

    def test_status_includes_duplicate_flag(self):
        """get_status() exposes the duplicate_callsign flag."""
        ti = _make_input(my_callsign="HYDRA-1")
        status = ti.get_status()
        assert "duplicate_callsign" in status
        assert status["duplicate_callsign"] is False

    def test_duplicate_flag_persists(self):
        """Once set, the duplicate flag stays true."""
        ti = _make_input(my_callsign="HYDRA-1")
        sa_data = _build_sa_event("HYDRA-1", uid="OTHER-NODE-SA")
        ti._handle_datagram(sa_data)
        assert ti._duplicate_callsign is True
        # Receiving a different callsign doesn't clear it
        ti._handle_datagram(_build_sa_event("HYDRA-2", uid="NODE-2"))
        assert ti._duplicate_callsign is True


# =====================================================================
# Group F: Integration — security + routing combined
# =====================================================================

class TestIntegrationSecurityRouting:
    def test_authorized_and_routed(self):
        """Full path: authorized sender, correct routing, no HMAC."""
        ti = _make_input(
            allowed_callsigns=["OPS-LEAD"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-2-USV STRIKE 7", sender="OPS-LEAD")
        )
        ti._on_strike.assert_called_once_with(7)

    def test_authorized_wrong_route(self):
        """Authorized sender but wrong routing target."""
        ti = _make_input(
            allowed_callsigns=["OPS-LEAD"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-3-DRONE LOCK 5", sender="OPS-LEAD")
        )
        ti._on_lock.assert_not_called()
        # Not a rejection — just not addressed to us
        assert ti._commands_rejected == 0

    def test_unauthorized_correct_route(self):
        """Unauthorized sender even with correct routing is rejected."""
        ti = _make_input(
            allowed_callsigns=["OPS-LEAD"],
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA-2-USV LOCK 5", sender="HACKER-1")
        )
        ti._on_lock.assert_not_called()
        assert ti._commands_rejected >= 1

    def test_hmac_plus_allowlist_plus_routing(self):
        """All three security layers pass together."""
        secret = "test-secret-123"
        msg = "HYDRA-ALL UNLOCK"
        signed = _sign_hmac(msg, secret)
        ti = _make_input(
            allowed_callsigns=["OPS-LEAD"],
            hmac_secret=secret,
            my_callsign="HYDRA-2-USV",
        )
        ti._handle_datagram(_build_geochat(signed, sender="OPS-LEAD"))
        ti._on_unlock.assert_called_once()

    def test_hmac_fail_blocks_even_if_authorized(self):
        """Bad HMAC blocks even an authorized and routed command."""
        ti = _make_input(
            allowed_callsigns=["OPS-LEAD"],
            hmac_secret="correct-secret",
            my_callsign="HYDRA-1",
        )
        ti._handle_datagram(
            _build_geochat("HYDRA LOCK 5|HMAC:invalid", sender="OPS-LEAD")
        )
        ti._on_lock.assert_not_called()

    def test_commands_dispatched_counter(self):
        """Successful commands increment the dispatch counter."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-1",
        )
        ti._handle_datagram(_build_geochat("HYDRA LOCK 1", sender="ALPHA-1"))
        ti._handle_datagram(_build_geochat("HYDRA UNLOCK", sender="ALPHA-1"))
        assert ti._commands_dispatched == 2

    def test_rejected_commands_counter(self):
        """Rejected commands increment the rejection counter."""
        ti = _make_input(
            allowed_callsigns=["ALPHA-1"],
            my_callsign="HYDRA-1",
        )
        ti._handle_datagram(_build_geochat("HYDRA LOCK 1", sender="HACKER"))
        ti._handle_datagram(_build_geochat("HYDRA LOCK 2", sender="HACKER"))
        assert ti._commands_rejected == 2
        assert ti._commands_dispatched == 0
