"""Tests for the observability package + /api/health, /api/metrics, /api/client_error."""

from __future__ import annotations

import logging
import os
import shutil
import threading

import pytest
from fastapi.testclient import TestClient

from hydra_detect.observability import (
    ClientErrorSink,
    SUBSYSTEMS,
    attach_audit_counters,
    compute_disk_bytes,
    compute_disk_free,
    get_client_error_sink,
    health_snapshot,
    hydra_drop_events_total,
    hydra_fps,
    hydra_hmac_invalid_total,
    hydra_strike_events_total,
    hydra_tak_accepted_total,
    hydra_tak_rejected_total,
    render_metrics,
    reset_counters_for_test,
)
from hydra_detect.observability import health as health_module
from hydra_detect.web import server as server_module


@pytest.fixture
def client():
    return TestClient(server_module.app)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_counters_for_test()
    get_client_error_sink().clear()
    server_module._client_error_hits.clear()
    hydra_fps.set(None)
    hydra_fps.set_provider(
        lambda: server_module.stream_state.get_stats().get("fps"),
    )
    yield
    reset_counters_for_test()
    get_client_error_sink().clear()
    server_module._client_error_hits.clear()


# ---------------------------------------------------------------------------
# ClientErrorSink unit tests
# ---------------------------------------------------------------------------

class TestClientErrorSink:
    def test_push_and_snapshot_roundtrip(self):
        sink = ClientErrorSink(maxlen=10)
        sink.push(message="boom", source="main.js", lineno=42, colno=7, stack="at foo()")
        snap = sink.snapshot(limit=5)
        assert snap["total"] == 1
        assert len(snap["recent"]) == 1
        ev = snap["recent"][0]
        assert ev["message"] == "boom"
        assert ev["source"] == "main.js"
        assert ev["lineno"] == 42
        assert ev["colno"] == 7
        assert ev["stack"] == "at foo()"

    def test_bounded_ring_evicts_oldest(self):
        sink = ClientErrorSink(maxlen=5)
        for i in range(12):
            sink.push(message=f"err-{i}")
        assert len(sink) == 5
        snap = sink.snapshot(limit=5)
        msgs = [e["message"] for e in snap["recent"]]
        # Oldest evicted, newest retained in FIFO order.
        assert msgs == [f"err-{i}" for i in range(7, 12)]

    def test_clips_long_message_and_stack(self):
        sink = ClientErrorSink()
        big = "x" * 20000
        sink.push(message=big, stack=big)
        ev = sink.snapshot(limit=1)["recent"][0]
        assert len(ev["message"]) <= 512
        assert len(ev["stack"]) <= 4096

    def test_thread_safe_under_concurrent_push(self):
        sink = ClientErrorSink(maxlen=1000)

        def hammer():
            for _ in range(100):
                sink.push(message="x")

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(sink) == 800


# ---------------------------------------------------------------------------
# health_snapshot unit tests
# ---------------------------------------------------------------------------

class TestHealthSnapshot:
    def test_all_subsystems_present(self):
        snap = health_snapshot(stats={"camera_ok": True, "fps": 10.0, "detector": "yolo"})
        assert set(snap["subsystems"].keys()) == set(SUBSYSTEMS)
        for name, sub in snap["subsystems"].items():
            assert sub["status"] in ("ok", "warn", "fail"), name
            assert "detail" in sub

    def test_camera_fail_when_camera_ok_false(self):
        snap = health_snapshot(stats={"camera_ok": False, "fps": 0.0})
        assert snap["subsystems"]["camera"]["status"] == "fail"
        # Overall status is fail because camera fails.
        assert snap["status"] == "fail"

    def test_overall_is_worst_subsystem(self):
        snap = health_snapshot(stats={"camera_ok": True, "fps": 5.0, "detector": "yolo"})
        # No mavlink / tak registered → warn from those, but no fails.
        assert snap["status"] in ("ok", "warn")
        assert snap["status"] != "fail"

    def test_mavlink_connected_ok(self):
        class Mav:
            connected = True
        snap = health_snapshot(
            stats={"camera_ok": True, "fps": 5.0},
            mavlink_ref=Mav(),
        )
        assert snap["subsystems"]["mavlink"]["status"] == "ok"

    def test_gps_fix_mapping(self):
        # 3D fix → ok
        snap = health_snapshot(stats={"camera_ok": True, "fps": 5.0, "gps_fix": 3})
        assert snap["subsystems"]["gps"]["status"] == "ok"
        # 2D fix → warn
        snap = health_snapshot(stats={"camera_ok": True, "fps": 5.0, "gps_fix": 2})
        assert snap["subsystems"]["gps"]["status"] == "warn"
        # No fix → warn
        snap = health_snapshot(stats={"camera_ok": True, "fps": 5.0, "gps_fix": 0})
        assert snap["subsystems"]["gps"]["status"] == "warn"


# ---------------------------------------------------------------------------
# compute_disk_free + /api/health additive disk_free_pct field (issue #154)
# ---------------------------------------------------------------------------

class TestComputeDiskFree:
    def test_returns_dict_of_floats(self):
        result = compute_disk_free()
        assert isinstance(result, dict)
        # Default labels — root must always be present on a running OS.
        assert "root" in result
        for label, pct in result.items():
            assert isinstance(pct, float), label
            assert 0.0 <= pct <= 100.0, f"{label}={pct}"

    def test_percent_rounded_to_two_decimals(self):
        result = compute_disk_free()
        for label, pct in result.items():
            # Two-decimal rounding: pct * 100 must be an integer-equivalent.
            assert round(pct, 2) == pct, f"{label}={pct} not 2dp"

    def test_custom_partition_labels(self, tmp_path):
        result = compute_disk_free({"workdir": str(tmp_path)})
        assert "workdir" in result
        assert isinstance(result["workdir"], float)
        # Unrelated default labels are not returned.
        assert "root" not in result
        assert "output_data" not in result

    def test_missing_path_falls_back_to_existing_ancestor(self, tmp_path):
        # Deeply non-existent path under a real tmpdir — falls back to tmpdir.
        nonexistent = tmp_path / "does" / "not" / "exist"
        result = compute_disk_free({"phantom": str(nonexistent)})
        # Fallback found tmp_path; we get a real number, not a missing key.
        assert "phantom" in result
        assert 0.0 <= result["phantom"] <= 100.0

    def test_unreadable_path_is_omitted_not_zeroed(self):
        # An absolute path whose parents also don't exist returns nothing.
        # Use a label so completely synthetic that no ancestor can match.
        result = compute_disk_free({"nope": "\x00invalid\x00"})
        # Either omitted (preferred) or a real number. Never a zero placeholder
        # that would falsely trigger "disk full" alarms.
        if "nope" in result:
            assert result["nope"] > 0.0


class TestHealthSnapshotDiskFreePct:
    def test_health_snapshot_includes_disk_free_pct(self):
        snap = health_snapshot(stats={"camera_ok": True, "fps": 10.0})
        assert "disk_free_pct" in snap
        assert isinstance(snap["disk_free_pct"], dict)
        # Root partition is always present.
        assert "root" in snap["disk_free_pct"]

    def test_health_snapshot_preserves_existing_keys(self):
        # The additive change MUST NOT remove status/ts/subsystems/disk subsystem.
        snap = health_snapshot(stats={"camera_ok": True, "fps": 10.0})
        assert "status" in snap
        assert "ts" in snap
        assert "subsystems" in snap
        assert "disk" in snap["subsystems"]  # existing string-status field

    def test_disk_free_pct_independent_of_subsystem_disk(self):
        # The string status and the numeric pct are computed separately.
        snap = health_snapshot(stats={"camera_ok": True, "fps": 10.0})
        assert snap["subsystems"]["disk"]["status"] in ("ok", "warn", "fail")
        # Pct is a number even if status is ok.
        assert isinstance(snap["disk_free_pct"]["root"], float)

    def test_custom_disk_partitions_overrides_defaults(self, tmp_path):
        snap = health_snapshot(
            stats={"camera_ok": True, "fps": 10.0},
            disk_partitions={"scratch": str(tmp_path)},
        )
        assert "scratch" in snap["disk_free_pct"]
        # Defaults are excluded when override supplied.
        assert "root" not in snap["disk_free_pct"]


# ---------------------------------------------------------------------------
# disk_bytes sibling field + partition-resolve hardening (issue #232)
# Adversarial follow-ups R3-1, R3-3, R3-4, R1-5 on PR #227.
# ---------------------------------------------------------------------------


class TestComputeDiskBytes:
    def test_returns_free_and_total_bytes(self):
        result = compute_disk_bytes()
        assert isinstance(result, dict)
        assert "root" in result
        root = result["root"]
        assert isinstance(root, dict)
        assert "free" in root and "total" in root
        assert isinstance(root["free"], int)
        assert isinstance(root["total"], int)
        # Sanity bounds — total > 0, free <= total.
        assert root["total"] > 0
        assert 0 <= root["free"] <= root["total"]

    def test_custom_partitions(self, tmp_path):
        result = compute_disk_bytes({"workdir": str(tmp_path)})
        assert "workdir" in result
        assert result["workdir"]["total"] > 0
        # Defaults excluded when override supplied.
        assert "root" not in result

    def test_unreadable_path_is_omitted(self):
        # Synthetic path with NUL bytes — no ancestor can resolve.
        result = compute_disk_bytes({"nope": "\x00invalid\x00"})
        # Omitted (preferred) or fell back; never a zero placeholder.
        if "nope" in result:
            assert result["nope"]["total"] > 0


class TestPartitionResolvesToCorrectMount:
    """R3-3: probe must resolve to the partition of the requested path, not
    walk up to the daemon's cwd when an explicit absolute path is supplied.
    """

    def test_explicit_path_uses_that_partition_not_cwd(
        self, tmp_path, monkeypatch,
    ):
        # Two distinct fake partitions: tmp_path (the requested one) and
        # "/" (what a cwd-anchored fallback would pick). The probe must
        # see the tmp_path numbers, not the root numbers.
        gb = 1024 ** 3
        # Distinct totals make "wrong partition" obvious in the assert.
        fake = {
            os.fspath(tmp_path): shutil._ntuple_diskusage(
                total=64 * gb, used=32 * gb, free=32 * gb,
            ),
            "/": shutil._ntuple_diskusage(
                total=4000 * gb, used=100 * gb, free=3900 * gb,
            ),
        }

        def fake_disk_usage(path):
            key = os.fspath(path)
            if key in fake:
                return fake[key]
            raise OSError(f"unexpected path: {key!r}")

        monkeypatch.setattr(health_module.shutil, "disk_usage", fake_disk_usage)
        result = compute_disk_bytes({"output_data": os.fspath(tmp_path)})
        assert "output_data" in result
        # The 64 GB partition, NOT the 4 TB root partition.
        assert result["output_data"]["total"] == 64 * gb
        assert result["output_data"]["free"] == 32 * gb


class TestLowDiskMonkeypatched:
    """R3-4: assert producer behaviour at the low-disk end of the range."""

    def test_one_pct_free_renders_as_float_one(self, tmp_path, monkeypatch):
        gb = 1024 ** 3
        usage = shutil._ntuple_diskusage(
            total=100 * gb, used=99 * gb, free=1 * gb,
        )
        monkeypatch.setattr(
            health_module.shutil, "disk_usage", lambda _p: usage,
        )
        result = compute_disk_free({"critical": os.fspath(tmp_path)})
        assert "critical" in result
        pct = result["critical"]
        # Strict identity to a float — not "1", not "1.00", not "1%".
        assert isinstance(pct, float)
        assert pct == 1.0


class TestProbeWarnsOnFailure:
    """R1-5: a single WARNING per failed probe call so operators have
    something to grep for when a partition vanishes from phone-home.
    """

    def test_warns_when_disk_usage_raises(self, tmp_path, monkeypatch, caplog):
        def boom(_path):
            raise OSError("simulated I/O error")

        monkeypatch.setattr(health_module.shutil, "disk_usage", boom)
        with caplog.at_level(logging.WARNING, logger="hydra_detect.observability.health"):
            result = compute_disk_free({"dead": os.fspath(tmp_path)})
        # Label is omitted (no zero placeholder)…
        assert "dead" not in result
        # …and exactly one WARNING line was emitted naming the probe.
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "disk_probe" in r.getMessage()
        ]
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]


class TestEnvVarOutputDataPath:
    """Defaults honour HYDRA_OUTPUT_DATA_PATH so the in-container probe can
    point at /data instead of resolving ./output_data from /app.
    """

    def test_env_override_changes_default_output_path(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("HYDRA_OUTPUT_DATA_PATH", os.fspath(tmp_path))
        result = compute_disk_free()
        # With the override active, output_data resolves to a real tmpdir
        # and is therefore present (not omitted).
        assert "output_data" in result
        # And root is still present too.
        assert "root" in result


# ---------------------------------------------------------------------------
# Prometheus exposition tests
# ---------------------------------------------------------------------------

class TestPrometheusFormat:
    def test_text_contains_all_expected_metrics(self):
        text = render_metrics()
        for name in (
            "hydra_fps",
            "hydra_inference_ms",
            "hydra_cpu_temp_c",
            "hydra_gpu_temp_c",
            "hydra_ram_pct",
            "hydra_tak_accepted_total",
            "hydra_tak_rejected_total",
            "hydra_strike_events_total",
            "hydra_drop_events_total",
            "hydra_hmac_invalid_total",
        ):
            assert f"# HELP {name} " in text
            assert f"# TYPE {name} " in text

    def test_counter_type_and_gauge_type_declared(self):
        text = render_metrics()
        assert "# TYPE hydra_tak_accepted_total counter" in text
        assert "# TYPE hydra_fps gauge" in text

    def test_counter_increments_via_audit_handler(self):
        attach_audit_counters()
        logger = logging.getLogger("hydra.audit")
        before_strike = hydra_strike_events_total.value()
        before_drop = hydra_drop_events_total.value()
        before_rej = hydra_tak_rejected_total.value()
        before_acc = hydra_tak_accepted_total.value()
        before_hmac = hydra_hmac_invalid_total.value()
        logger.info("APPROACH STRIKE committed")
        logger.info("APPROACH DROP committed")
        logger.info("TAK_CMD_REJECTED reason=foo")
        logger.info("TAK_CMD_ACCEPTED action=LOCK")
        logger.info("HMAC_INVALID sender=evil")
        assert hydra_strike_events_total.value() == before_strike + 1
        assert hydra_drop_events_total.value() == before_drop + 1
        assert hydra_tak_rejected_total.value() == before_rej + 1
        assert hydra_tak_accepted_total.value() == before_acc + 1
        assert hydra_hmac_invalid_total.value() == before_hmac + 1

    def test_render_is_valid_line_format(self):
        hydra_tak_accepted_total.inc(3)
        text = render_metrics()
        # Every non-HELP/TYPE line must be <name> <value>.
        for line in text.strip().splitlines():
            if line.startswith("#"):
                continue
            parts = line.split(" ", 1)
            assert len(parts) == 2, line
            assert parts[0].startswith("hydra_"), line

    def test_nan_rendered_when_provider_missing(self):
        hydra_fps.set_provider(lambda: None)
        text = render_metrics()
        # The line for hydra_fps must render a NaN value.
        assert any(
            line.startswith("hydra_fps ") and "NaN" in line
            for line in text.splitlines()
        )


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_returns_structured_shape(self, client):
        server_module.stream_state.update_stats(
            camera_ok=True, fps=10.0, detector="yolo",
        )
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "subsystems" in body
        assert set(body["subsystems"].keys()) == set(SUBSYSTEMS)
        # Back-compat fields preserved.
        assert body["healthy"] is True
        assert body["camera_ok"] is True
        assert body["fps"] == 10.0

    def test_returns_503_on_camera_fail(self, client):
        server_module.stream_state.update_stats(camera_ok=False, fps=0.0)
        resp = client.get("/api/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "fail"
        assert body["subsystems"]["camera"]["status"] == "fail"

    def test_health_endpoint_surfaces_disk_free_pct(self, client):
        server_module.stream_state.update_stats(
            camera_ok=True, fps=10.0, detector="yolo",
        )
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "disk_free_pct" in body
        assert isinstance(body["disk_free_pct"], dict)
        assert "root" in body["disk_free_pct"]
        pct = body["disk_free_pct"]["root"]
        assert isinstance(pct, (int, float))
        assert 0.0 <= pct <= 100.0

    def test_health_endpoint_surfaces_disk_bytes(self, client):
        # Issue #232 R3-1: percent-only telemetry can't distinguish 5% of a
        # 32 GB SD card from 5% of a 4 TB NVMe. Absolute byte counts must be
        # surfaced alongside disk_free_pct so the BLOCKED gate (#226) can
        # set platform-aware thresholds.
        server_module.stream_state.update_stats(
            camera_ok=True, fps=10.0, detector="yolo",
        )
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "disk_bytes" in body
        assert isinstance(body["disk_bytes"], dict)
        assert "root" in body["disk_bytes"]
        root = body["disk_bytes"]["root"]
        assert isinstance(root, dict)
        assert isinstance(root["free"], int)
        assert isinstance(root["total"], int)
        assert root["total"] > 0
        assert 0 <= root["free"] <= root["total"]


class TestMetricsEndpoint:
    def test_content_type_and_body(self, client):
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/plain" in ct
        assert "version=0.0.4" in ct
        text = resp.text
        assert "# TYPE hydra_fps gauge" in text
        assert "# TYPE hydra_tak_accepted_total counter" in text

    def test_metrics_reflect_counter_increment(self, client):
        hydra_strike_events_total.inc(5)
        resp = client.get("/api/metrics")
        assert "hydra_strike_events_total 5" in resp.text


class TestClientErrorEndpoint:
    def test_post_stores_event(self, client):
        resp = client.post("/api/client_error", json={
            "message": "TypeError: cannot read property",
            "source": "/static/js/main.js",
            "lineno": 42,
            "colno": 7,
            "stack": "at init (/static/js/main.js:42:7)",
            "url": "http://localhost/",
            "timestamp": 1234567890,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["total"] == 1

    def test_post_rejects_malformed_json(self, client):
        resp = client.post(
            "/api/client_error",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_rate_limit_returns_429(self, client):
        # Push up to the cap; the next one must be 429.
        server_module._client_error_hits.clear()
        for _ in range(server_module._CLIENT_ERROR_MAX_PER_WINDOW):
            resp = client.post("/api/client_error", json={"message": "x"})
            assert resp.status_code == 200
        resp = client.post("/api/client_error", json={"message": "over"})
        assert resp.status_code == 429

    def test_recent_endpoint_returns_payload(self, client):
        client.post("/api/client_error", json={"message": "one"})
        client.post("/api/client_error", json={"message": "two"})
        resp = client.get("/api/client_error/recent?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        msgs = [e["message"] for e in body["recent"]]
        assert "one" in msgs and "two" in msgs
