"""Tests for the observability package + /api/health, /api/metrics, /api/client_error."""

from __future__ import annotations

import logging
import threading

import pytest
from fastapi.testclient import TestClient

from hydra_detect.observability import (
    ClientErrorSink,
    SUBSYSTEMS,
    attach_audit_counters,
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
