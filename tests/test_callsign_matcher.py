"""Exhaustive tests for the callsign wildcard matcher (issue #48).

The matcher decides which TAK GeoChat / CoT commands this vehicle should
process. Group commands like ``HYDRA-2-ALL UNLOCK`` or
``HYDRA-ALL-USV HOLD`` need to fan out to every vehicle whose callsign
matches the non-wildcard segments.

These tests pin the behaviour so the matcher cannot regress under future
refactors. The function under test lives in ``hydra_detect.tak.tak_input``
as a private helper; importing it directly is intentional — the matrix
is large enough that round-tripping through ``_handle_datagram`` would
swamp the signal.
"""
from __future__ import annotations

import pytest

from hydra_detect.tak.tak_input import (
    _callsign_matches,
    _classify_routing,
)


# ── exact match ─────────────────────────────────────────────────────────


class TestExactMatch:
    def test_self_matches(self):
        assert _callsign_matches("HYDRA-2-USV", "HYDRA-2-USV") is True

    def test_case_insensitive(self):
        assert _callsign_matches("hydra-2-usv", "HYDRA-2-USV") is True
        assert _callsign_matches("HYDRA-2-USV", "hydra-2-usv") is True

    def test_different_team_does_not_match(self):
        assert _callsign_matches("HYDRA-3-USV", "HYDRA-2-USV") is False

    def test_different_platform_does_not_match(self):
        assert _callsign_matches("HYDRA-2-DRONE", "HYDRA-2-USV") is False


# ── bare HYDRA legacy prefix ───────────────────────────────────────────


class TestBareHydra:
    def test_bare_hydra_matches_any_hydra(self):
        assert _callsign_matches("HYDRA", "HYDRA-2-USV") is True
        assert _callsign_matches("HYDRA", "HYDRA-99-FW") is True
        assert _callsign_matches("HYDRA", "HYDRA-1") is True

    def test_bare_hydra_rejects_non_hydra(self):
        assert _callsign_matches("HYDRA", "BANDIT-1") is False


# ── HYDRA-ALL full wildcard ────────────────────────────────────────────


class TestFullWildcard:
    @pytest.mark.parametrize("cs", [
        "HYDRA-2-USV",
        "HYDRA-3-DRONE",
        "HYDRA-99-FW",
        "HYDRA-1",
    ])
    def test_hydra_all_matches_every_hydra(self, cs):
        assert _callsign_matches("HYDRA-ALL", cs) is True


# ── segment wildcards ──────────────────────────────────────────────────


class TestSegmentWildcardTeam:
    """HYDRA-2-ALL — every vehicle on Team 2."""

    def test_matches_own_team_usv(self):
        assert _callsign_matches("HYDRA-2-ALL", "HYDRA-2-USV") is True

    def test_matches_own_team_drone(self):
        assert _callsign_matches("HYDRA-2-ALL", "HYDRA-2-DRONE") is True

    def test_rejects_other_team(self):
        assert _callsign_matches("HYDRA-2-ALL", "HYDRA-3-USV") is False

    def test_rejects_legacy_callsign_without_team(self):
        # Legacy "HYDRA-USV" has no team segment "2".
        assert _callsign_matches("HYDRA-2-ALL", "HYDRA-USV") is False


class TestSegmentWildcardPlatform:
    """HYDRA-ALL-USV — every USV across all teams."""

    def test_matches_any_team_usv(self):
        assert _callsign_matches("HYDRA-ALL-USV", "HYDRA-2-USV") is True
        assert _callsign_matches("HYDRA-ALL-USV", "HYDRA-3-USV") is True
        assert _callsign_matches("HYDRA-ALL-USV", "HYDRA-99-USV") is True

    def test_rejects_drone(self):
        assert _callsign_matches("HYDRA-ALL-USV", "HYDRA-2-DRONE") is False

    def test_rejects_fw(self):
        assert _callsign_matches("HYDRA-ALL-USV", "HYDRA-3-FW") is False


class TestSegmentWildcardBoth:
    """HYDRA-ALL-ALL — every Hydra (equivalent to HYDRA-ALL)."""

    def test_matches_everything_hydra(self):
        assert _callsign_matches("HYDRA-ALL-ALL", "HYDRA-2-USV") is True
        assert _callsign_matches("HYDRA-ALL-ALL", "HYDRA-99-FW") is True


# ── non-matches that should never fire ─────────────────────────────────


class TestNonMatch:
    def test_other_call_family_rejected(self):
        assert _callsign_matches("BANDIT-2-USV", "HYDRA-2-USV") is False

    def test_partial_substring_does_not_match(self):
        # "HYDRA-2" alone is neither an exact match nor a wildcard.
        # The matcher must NOT treat it as a prefix wildcard.
        assert _callsign_matches("HYDRA-2", "HYDRA-2-USV") is False

    def test_empty_prefix_does_not_match(self):
        assert _callsign_matches("", "HYDRA-2-USV") is False


# ── routing classification ─────────────────────────────────────────────


class TestClassifyRouting:
    def test_bare_hydra_is_fleet(self):
        assert _classify_routing("HYDRA") == "fleet"

    def test_hydra_all_is_fleet(self):
        assert _classify_routing("HYDRA-ALL") == "fleet"

    def test_exact_is_direct(self):
        assert _classify_routing("HYDRA-2-USV") == "direct"

    def test_team_wildcard_is_segment(self):
        assert _classify_routing("HYDRA-2-ALL") == "segment_wildcard"

    def test_platform_wildcard_is_segment(self):
        assert _classify_routing("HYDRA-ALL-USV") == "segment_wildcard"

    def test_case_insensitive_classification(self):
        assert _classify_routing("hydra-all-usv") == "segment_wildcard"


# ── CULEX scenario: HYDRA LOCK 5 hits every vehicle ─────────────────────


class TestCulexFanOut:
    """The motivating scenario from issue #48: a fleet broadcast hits all 20."""

    FLEET = [
        f"HYDRA-{t}-{p}"
        for t in range(1, 6)
        for p in ("USV", "UGV", "DRONE", "FW")
    ]

    def test_hydra_all_fans_out_to_every_unit(self):
        for cs in self.FLEET:
            assert _callsign_matches("HYDRA-ALL", cs) is True

    def test_platform_wildcard_only_hits_that_platform(self):
        for cs in self.FLEET:
            expected = cs.endswith("-USV")
            assert _callsign_matches("HYDRA-ALL-USV", cs) is expected

    def test_team_wildcard_only_hits_that_team(self):
        for cs in self.FLEET:
            expected = cs.startswith("HYDRA-2-")
            assert _callsign_matches("HYDRA-2-ALL", cs) is expected

    def test_direct_only_hits_that_vehicle(self):
        target = "HYDRA-2-USV"
        for cs in self.FLEET:
            expected = cs == target
            assert _callsign_matches(target, cs) is expected
