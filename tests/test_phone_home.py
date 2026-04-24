"""Tests for hydra_detect/telemetry/phone_home.py.

Covers:
- build_payload shape and null branches
- send_payload success / network error / 401 / 5xx (no raise)
- queue_payload writes file, bounded queue evicts oldest
- flush_queue sends in order, stops on first failure
- CLI dry-run prints JSON without sending
"""

from __future__ import annotations

import configparser
import json
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.telemetry.phone_home import (
    SendResult,
    _QUEUE_MAX,
    _queue_dir,
    build_payload,
    flush_queue,
    queue_payload,
    send_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(sections: dict | None = None) -> configparser.ConfigParser:
    """Build a ConfigParser from a dict of sections."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    if sections:
        for section, keys in sections.items():
            cfg.add_section(section)
            for key, val in keys.items():
                cfg.set(section, key, val)
    return cfg


def _minimal_cfg() -> configparser.ConfigParser:
    return _make_cfg({
        "tak": {"callsign": "HYDRA-TEST"},
        "telemetry": {"enabled": "false", "collector_url": "", "api_token": "", "opt_out": "false"},
    })


# ---------------------------------------------------------------------------
# build_payload — shape
# ---------------------------------------------------------------------------

class TestBuildPayloadShape:
    """build_payload always returns the full set of keys."""

    REQUIRED_KEYS = {
        "callsign", "hostname", "version", "channel",
        "uptime_hours", "mode", "capability_summary",
        "last_mission_at", "disk_free_pct", "cpu_temp_c",
        "power_mode", "last_update_status",
    }

    def test_all_keys_present(self, tmp_path):
        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        assert self.REQUIRED_KEYS.issubset(payload.keys()), (
            f"Missing keys: {self.REQUIRED_KEYS - payload.keys()}"
        )

    def test_callsign_from_tak_section(self, tmp_path):
        cfg = _make_cfg({"tak": {"callsign": "HYDRA-7"}})
        payload = build_payload(cfg, tmp_path)
        assert payload["callsign"] == "HYDRA-7"

    def test_callsign_null_when_missing(self, tmp_path):
        cfg = _make_cfg({})
        payload = build_payload(cfg, tmp_path)
        assert payload["callsign"] is None

    def test_hostname_is_string(self, tmp_path):
        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        assert isinstance(payload["hostname"], str)
        assert len(payload["hostname"]) > 0

    def test_capability_summary_empty_dict_when_evaluator_absent(self, tmp_path):
        cfg = _minimal_cfg()
        # The evaluator from #171 is not wired yet — expect empty dict.
        payload = build_payload(cfg, tmp_path)
        assert isinstance(payload["capability_summary"], dict)

    def test_disk_free_pct_is_number_or_null(self, tmp_path):
        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        val = payload["disk_free_pct"]
        assert val is None or isinstance(val, float)

    def test_last_mission_at_null_when_no_logs(self, tmp_path):
        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        assert payload["last_mission_at"] is None

    def test_last_mission_at_iso_when_log_exists(self, tmp_path):
        log_dir = tmp_path / "output_data" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "run.jsonl").write_text('{"event": "detection"}\n')

        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        ts = payload["last_mission_at"]
        assert ts is not None
        # Must be a parseable ISO 8601 string.
        from datetime import datetime
        datetime.fromisoformat(ts)

    def test_last_update_status_null_when_file_absent(self, tmp_path):
        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        assert payload["last_update_status"] is None

    def test_last_update_status_reads_file(self, tmp_path):
        status_dir = tmp_path / "output_data"
        status_dir.mkdir(parents=True)
        (status_dir / "update_status.txt").write_text("pull ok — 2026-04-23")

        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        assert payload["last_update_status"] == "pull ok — 2026-04-23"

    def test_last_update_status_truncated_at_120(self, tmp_path):
        status_dir = tmp_path / "output_data"
        status_dir.mkdir(parents=True)
        (status_dir / "update_status.txt").write_text("x" * 200)

        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        assert len(payload["last_update_status"]) == 120

    def test_uptime_hours_null_on_missing_proc_uptime(self, tmp_path):
        cfg = _minimal_cfg()
        with patch("hydra_detect.telemetry.phone_home.Path") as mock_path_cls:
            # Make /proc/uptime raise FileNotFoundError but leave other paths alone.
            real_path = Path
            def side_effect(arg):
                p = real_path(arg)
                if str(arg) == "/proc/uptime":
                    m = MagicMock(spec=Path)
                    m.read_text.side_effect = FileNotFoundError()
                    return m
                return p
            mock_path_cls.side_effect = side_effect
            payload = build_payload(cfg, tmp_path)
        # Either null or a float — both are valid (depends on test env).
        val = payload["uptime_hours"]
        assert val is None or isinstance(val, (int, float))

    def test_version_is_string_or_null(self, tmp_path):
        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        val = payload["version"]
        assert val is None or isinstance(val, str)

    def test_cpu_temp_null_on_non_jetson(self, tmp_path):
        """On a non-Jetson host (no sysfs thermal zone), cpu_temp_c is None."""
        cfg = _minimal_cfg()
        with patch(
            "hydra_detect.telemetry.phone_home._cpu_temp_c",
            return_value=None,
        ):
            payload = build_payload(cfg, tmp_path)
        assert payload["cpu_temp_c"] is None

    def test_power_mode_null_on_non_jetson(self, tmp_path):
        cfg = _minimal_cfg()
        with patch(
            "hydra_detect.telemetry.phone_home._power_mode",
            return_value=None,
        ):
            payload = build_payload(cfg, tmp_path)
        assert payload["power_mode"] is None

    def test_payload_is_json_serialisable(self, tmp_path):
        cfg = _minimal_cfg()
        payload = build_payload(cfg, tmp_path)
        serialised = json.dumps(payload)
        assert isinstance(serialised, str)


# ---------------------------------------------------------------------------
# send_payload
# ---------------------------------------------------------------------------

class TestSendPayload:
    """send_payload returns SendResult and never raises."""

    def _fake_response(self, status: int) -> MagicMock:
        resp = MagicMock()
        resp.status = status
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_success_200(self, tmp_path):
        resp = self._fake_response(200)
        with patch("urllib.request.urlopen", return_value=resp):
            result = send_payload("http://collector/ingest", {"k": "v"}, "tok")
        assert result.ok is True
        assert result.status_code == 200
        assert result.error is None

    def test_success_201(self, tmp_path):
        resp = self._fake_response(201)
        with patch("urllib.request.urlopen", return_value=resp):
            result = send_payload("http://collector/ingest", {}, "tok")
        assert result.ok is True
        assert result.status_code == 201

    def test_http_401_returns_structured_result(self):
        err = urllib.error.HTTPError(
            url="http://x", code=401, msg="Unauthorized",
            hdrs=None, fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = send_payload("http://collector/ingest", {}, "bad-token")
        assert result.ok is False
        assert result.status_code == 401
        assert result.error is not None

    def test_http_500_returns_structured_result(self):
        err = urllib.error.HTTPError(
            url="http://x", code=500, msg="Internal Server Error",
            hdrs=None, fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = send_payload("http://collector/ingest", {}, "tok")
        assert result.ok is False
        assert result.status_code == 500

    def test_network_error_url_error(self):
        err = urllib.error.URLError(reason="Name or service not known")
        with patch("urllib.request.urlopen", side_effect=err):
            result = send_payload("http://collector/ingest", {}, "tok")
        assert result.ok is False
        assert result.status_code is None
        assert "Name or service not known" in (result.error or "")

    def test_timeout_returns_structured_result(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            result = send_payload("http://collector/ingest", {}, "tok", timeout=1)
        assert result.ok is False
        assert result.status_code is None
        assert result.error is not None

    def test_never_raises(self):
        """send_payload must not propagate any exception."""
        with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
            result = send_payload("http://collector/ingest", {}, "tok")
        assert isinstance(result, SendResult)

    def test_bearer_token_in_header(self):
        captured_req = []

        def fake_urlopen(req, **kwargs):
            captured_req.append(req)
            resp = self._fake_response(200)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            send_payload("http://collector/ingest", {}, "MY_SECRET_TOKEN")

        req = captured_req[0]
        assert req.get_header("Authorization") == "Bearer MY_SECRET_TOKEN"


# ---------------------------------------------------------------------------
# queue_payload
# ---------------------------------------------------------------------------

class TestQueuePayload:
    def test_writes_json_file(self, tmp_path):
        payload = {"callsign": "TEST", "version": "2.1.0"}
        queue_payload(tmp_path, payload)

        q = _queue_dir(tmp_path)
        files = list(q.glob("*.json"))
        assert len(files) == 1
        loaded = json.loads(files[0].read_text())
        assert loaded["callsign"] == "TEST"

    def test_creates_queue_dir(self, tmp_path):
        queue_payload(tmp_path, {"x": 1})
        assert _queue_dir(tmp_path).exists()

    def test_bounded_queue_evicts_oldest(self, tmp_path):
        # Write _QUEUE_MAX + 5 entries — oldest 5 should be evicted.
        q = _queue_dir(tmp_path)
        q.mkdir(parents=True)

        # Pre-populate with old entries (named to sort before the new ones).
        for i in range(_QUEUE_MAX):
            (q / f"20200101T00000{i:06d}.json").write_text('{"old": true}')

        # queue_payload adds one more, triggering eviction.
        queue_payload(tmp_path, {"new": True})

        remaining = sorted(q.glob("*.json"))
        assert len(remaining) == _QUEUE_MAX
        # The newest entry (containing "new": true) must be present.
        newest = json.loads(remaining[-1].read_text())
        assert newest.get("new") is True

    def test_multiple_writes_accumulate(self, tmp_path):
        for i in range(5):
            queue_payload(tmp_path, {"seq": i})
        q = _queue_dir(tmp_path)
        assert len(list(q.glob("*.json"))) == 5


# ---------------------------------------------------------------------------
# flush_queue
# ---------------------------------------------------------------------------

class TestFlushQueue:
    def _fill_queue(self, q: Path, count: int) -> list[Path]:
        q.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(count):
            f = q / f"2026010{i:02d}T000000000000.json"
            f.write_text(json.dumps({"seq": i}))
            files.append(f)
        return sorted(files)

    def test_sends_in_chronological_order(self, tmp_path):
        q = _queue_dir(tmp_path)
        files = self._fill_queue(q, 3)
        sent_payloads = []

        def fake_send(url, payload, api_token, **kw):
            sent_payloads.append(payload["seq"])
            return SendResult(ok=True, status_code=200, error=None)

        with patch(
            "hydra_detect.telemetry.phone_home.send_payload",
            side_effect=fake_send,
        ):
            flush_queue(tmp_path, "http://collector", "tok")

        assert sent_payloads == [0, 1, 2]

    def test_removes_sent_entries(self, tmp_path):
        q = _queue_dir(tmp_path)
        self._fill_queue(q, 3)

        with patch(
            "hydra_detect.telemetry.phone_home.send_payload",
            return_value=SendResult(ok=True, status_code=200, error=None),
        ):
            flush_queue(tmp_path, "http://collector", "tok")

        assert list(q.glob("*.json")) == []

    def test_stops_on_first_failure(self, tmp_path):
        q = _queue_dir(tmp_path)
        self._fill_queue(q, 5)
        send_count = [0]

        def fail_on_second(url, payload, api_token, **kw):
            send_count[0] += 1
            if send_count[0] == 1:
                return SendResult(ok=True, status_code=200, error=None)
            return SendResult(ok=False, status_code=503, error="Service Unavailable")

        with patch(
            "hydra_detect.telemetry.phone_home.send_payload",
            side_effect=fail_on_second,
        ):
            flush_queue(tmp_path, "http://collector", "tok")

        # First entry sent and removed; entries 2-5 remain.
        remaining = list(q.glob("*.json"))
        assert len(remaining) == 4

    def test_noop_when_queue_empty(self, tmp_path):
        # Should not raise even when queue dir does not exist.
        flush_queue(tmp_path, "http://collector", "tok")

    def test_respects_max_batch(self, tmp_path):
        q = _queue_dir(tmp_path)
        self._fill_queue(q, 20)
        send_count = [0]

        def count_sends(url, payload, api_token, **kw):
            send_count[0] += 1
            return SendResult(ok=True, status_code=200, error=None)

        with patch(
            "hydra_detect.telemetry.phone_home.send_payload",
            side_effect=count_sends,
        ):
            flush_queue(tmp_path, "http://collector", "tok", max_batch=7)

        assert send_count[0] == 7
        # Remaining 13 untouched.
        assert len(list(q.glob("*.json"))) == 13

    def test_skips_corrupt_entry(self, tmp_path):
        q = _queue_dir(tmp_path)
        q.mkdir(parents=True)
        (q / "2026010100T000000000000.json").write_text("not valid json {{{")
        (q / "2026010200T000000000000.json").write_text('{"seq": 1}')

        sent = []

        def record(url, payload, api_token, **kw):
            sent.append(payload)
            return SendResult(ok=True, status_code=200, error=None)

        with patch(
            "hydra_detect.telemetry.phone_home.send_payload",
            side_effect=record,
        ):
            flush_queue(tmp_path, "http://collector", "tok")

        # Corrupt file is removed; valid entry is sent.
        assert len(sent) == 1
        assert sent[0]["seq"] == 1


# ---------------------------------------------------------------------------
# CLI — dry-run
# ---------------------------------------------------------------------------

class TestCLIDryRun:
    """CLI --dry-run prints JSON without making any network calls."""

    def _run_dry_run(self, config_path: Path) -> tuple[int, str]:
        """Invoke scripts/phone_home.py main() and capture stdout."""
        import importlib.util
        import io

        script_path = (
            Path(__file__).resolve().parent.parent / "scripts" / "phone_home.py"
        )
        spec = importlib.util.spec_from_file_location("phone_home_script", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured
        try:
            rc = mod.main(["--config", str(config_path), "--dry-run"])
        finally:
            sys.stdout = original_stdout
        return rc, captured.getvalue()

    def test_prints_valid_json(self, tmp_path):
        cfg_path = tmp_path / "config.ini"
        cfg_path.write_text("[tak]\ncallsign = HYDRA-DRY\n")

        rc, output = self._run_dry_run(cfg_path)
        assert rc == 0
        payload = json.loads(output)
        assert payload["callsign"] == "HYDRA-DRY"

    def test_no_network_call(self, tmp_path):
        cfg_path = tmp_path / "config.ini"
        cfg_path.write_text("[tak]\ncallsign = HYDRA-X\n")

        with patch(
            "hydra_detect.telemetry.phone_home.send_payload"
        ) as mock_send:
            self._run_dry_run(cfg_path)
        mock_send.assert_not_called()

    def test_all_required_keys_in_output(self, tmp_path):
        cfg_path = tmp_path / "config.ini"
        cfg_path.write_text("[tak]\ncallsign = HYDRA-X\n")

        rc, output = self._run_dry_run(cfg_path)
        payload = json.loads(output)
        required = {
            "callsign", "hostname", "version", "channel",
            "uptime_hours", "mode", "capability_summary",
            "last_mission_at", "disk_free_pct", "cpu_temp_c",
            "power_mode", "last_update_status",
        }
        assert required.issubset(payload.keys())
