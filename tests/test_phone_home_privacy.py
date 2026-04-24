"""Privacy audit for phone-home telemetry.

This test is the primary defense against future changes accidentally leaking
sensitive data.  It calls build_payload with a config and state that contains
GPS coordinates, video references, crop paths, operator names, and other PII,
then asserts that none of those values appear anywhere in the payload — not
in keys, not in values, not nested inside dicts.

If this test fails after a code change, that change is leaking data it should not.
"""

from __future__ import annotations

import configparser
import json
from pathlib import Path

import pytest

from hydra_detect.telemetry.phone_home import build_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_values(obj) -> list:
    """Recursively collect all string/numeric leaf values from a nested dict/list."""
    results = []
    if isinstance(obj, dict):
        for v in obj.values():
            results.extend(_deep_values(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            results.extend(_deep_values(item))
    elif obj is not None:
        results.append(obj)
    return results


def _deep_keys(obj, prefix: str = "") -> list[str]:
    """Recursively collect all dict keys from a nested structure."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            results.append(full)
            results.extend(_deep_keys(v, prefix=full))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            results.extend(_deep_keys(item, prefix=prefix))
    return results


def _payload_with_poisoned_config(tmp_path: Path) -> dict:
    """Return build_payload output against a config loaded with sensitive fields.

    The config itself contains GPS coordinates, operator names, and other
    sensitive data — none of which should end up in the payload.
    """
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))

    # TAK section — callsign only should appear.
    cfg.add_section("tak")
    cfg.set("tak", "callsign", "HYDRA-1")
    # These must NOT appear in the payload:
    cfg.set("tak", "multicast_group", "239.2.3.1")
    cfg.set("tak", "unicast_targets", "192.168.1.100:6969")

    # MAVLink GPS simulation — must NOT appear.
    cfg.add_section("mavlink")
    cfg.set("mavlink", "sim_gps_lat", "36.123456")
    cfg.set("mavlink", "sim_gps_lon", "-115.987654")
    cfg.set("mavlink", "connection_string", "/dev/ttyACM0")
    cfg.set("mavlink", "source_system", "42")

    # Logging — crop paths must NOT appear.
    cfg.add_section("logging")
    cfg.set("logging", "crop_dir", "/output_data/crops")
    cfg.set("logging", "image_dir", "/output_data/images")

    # Autonomous — geofence coordinates must NOT appear.
    cfg.add_section("autonomous")
    cfg.set("autonomous", "geofence_lat", "36.111111")
    cfg.set("autonomous", "geofence_lon", "-115.222222")

    # Telemetry section.
    cfg.add_section("telemetry")
    cfg.set("telemetry", "enabled", "false")
    cfg.set("telemetry", "collector_url", "")
    cfg.set("telemetry", "api_token", "SENSITIVE_API_TOKEN_12345")

    return build_payload(cfg, tmp_path)


# ---------------------------------------------------------------------------
# Sensitive values that must NEVER appear in the payload
# ---------------------------------------------------------------------------

# GPS coordinate fragments — exact matches or substrings found in any leaf value.
_FORBIDDEN_SUBSTRINGS = [
    "36.123456",    # sim_gps_lat
    "-115.987654",  # sim_gps_lon
    "36.111111",    # geofence_lat
    "-115.222222",  # geofence_lon
    "SENSITIVE_API_TOKEN_12345",  # api_token must not be echoed back
    "192.168.1.100",  # unicast target IP
    "/dev/ttyACM0",   # serial port path (hardware detail)
]

# Key names that must NOT appear anywhere in the payload (even nested).
_FORBIDDEN_KEYS = {
    "gps_lat", "gps_lon", "lat", "lon", "latitude", "longitude",
    "geofence_lat", "geofence_lon", "sim_gps_lat", "sim_gps_lon",
    "crop", "crops", "crop_dir", "image_dir",
    "frame", "video", "thumbnail",
    "api_token",        # token used to send, not echoed in body
    "connection_string",  # hardware serial path
    "source_system",    # MAVLink system ID
    "unicast_targets", "multicast_group",
    "password", "web_password", "hmac_secret", "command_hmac_secret",
}


class TestPrivacyAudit:
    """Payload must not leak GPS, video, crops, or operator-identifying info."""

    @pytest.fixture()
    def payload(self, tmp_path) -> dict:
        return _payload_with_poisoned_config(tmp_path)

    @pytest.fixture()
    def payload_json(self, payload) -> str:
        return json.dumps(payload)

    def test_no_gps_coordinates_in_values(self, payload):
        values = _deep_values(payload)
        str_values = [str(v) for v in values]
        for forbidden in ["36.123456", "-115.987654", "36.111111", "-115.222222"]:
            for sv in str_values:
                assert forbidden not in sv, (
                    f"GPS fragment '{forbidden}' found in payload value: {sv!r}"
                )

    def test_no_forbidden_substrings_in_serialised_json(self, payload_json):
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in payload_json, (
                f"Forbidden value {forbidden!r} appears in serialised payload"
            )

    def test_no_forbidden_keys(self, payload):
        all_keys = set(_deep_keys(payload))
        leaked = all_keys & _FORBIDDEN_KEYS
        assert not leaked, f"Forbidden keys found in payload: {leaked}"

    def test_api_token_not_echoed(self, payload_json):
        assert "SENSITIVE_API_TOKEN_12345" not in payload_json

    def test_serial_port_not_in_payload(self, payload_json):
        assert "/dev/ttyACM0" not in payload_json

    def test_ip_address_not_in_payload(self, payload_json):
        assert "192.168.1.100" not in payload_json

    def test_crop_dir_not_in_payload(self, payload_json):
        assert "/output_data/crops" not in payload_json
        assert "image_dir" not in payload_json
        assert "crop_dir" not in payload_json

    def test_mavlink_system_id_not_in_payload(self, payload):
        all_keys = set(_deep_keys(payload))
        assert "source_system" not in all_keys

    def test_callsign_is_only_identity_field(self, payload):
        """Only callsign and hostname (OS-level) are identity fields in the payload."""
        all_keys = set(_deep_keys(payload))
        # These are the only acceptable identity-adjacent fields.
        identity_keys = {"callsign", "hostname"}
        # No other identity-adjacent keys are allowed.
        suspicious = {
            k for k in all_keys
            if any(word in k.lower() for word in
                   ("operator", "name", "user", "email", "phone", "address", "id"))
            and k not in identity_keys
        }
        assert not suspicious, f"Suspicious identity fields in payload: {suspicious}"

    def test_payload_keys_are_exactly_the_documented_set(self, payload):
        """Snapshot test — if new keys are added, this test forces a review."""
        expected = {
            "callsign", "hostname", "version", "channel",
            "uptime_hours", "mode", "capability_summary",
            "last_mission_at", "disk_free_pct", "cpu_temp_c",
            "power_mode", "last_update_status",
        }
        actual = set(payload.keys())
        added = actual - expected
        removed = expected - actual
        assert not added, (
            f"New keys added to payload — privacy review required: {added}"
        )
        assert not removed, f"Keys removed from payload: {removed}"
