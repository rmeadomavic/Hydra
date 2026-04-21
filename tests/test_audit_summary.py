"""Tests for the audit sink and /api/audit/summary endpoint."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

from hydra_detect.audit import AuditSink, attach_to_logger, get_default_sink
from hydra_detect.audit.audit_log import _classify
from hydra_detect.web import server as server_module


@pytest.fixture
def client():
    return TestClient(server_module.app)


@pytest.fixture(autouse=True)
def _reset_default_sink():
    sink = get_default_sink()
    # Drain the default sink between tests — emissions from other modules
    # at import/startup shouldn't cross-contaminate.
    with sink._lock:
        sink._events.clear()
    yield
    with sink._lock:
        sink._events.clear()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_hmac_invalid(self):
        assert _classify("TAK_CMD_REJECTED reason=hmac_invalid text=...") \
            == "hmac_invalid_events"

    def test_tak_rejected(self):
        assert _classify("TAK_CMD_REJECTED reason=no_allowlist sender=X") \
            == "tak_rejected"

    def test_tak_accepted(self):
        assert _classify("TAK_CMD_ACCEPTED action=LOCK track_id=5 sender=A") \
            == "tak_accepted"

    def test_approach_drop(self):
        assert _classify("APPROACH DROP START: track_id=5 lat=1 lon=2") \
            == "drop_events"

    def test_approach_strike(self):
        assert _classify("APPROACH STRIKE START: track_id=3") \
            == "strike_events"

    def test_approach_abort(self):
        assert _classify("APPROACH ABORT: mode=strike track_id=3") \
            == "approach_abort_events"

    def test_approach_arm(self):
        assert _classify("APPROACH PIXEL_LOCK START: track_id=9") \
            == "approach_arm_events"

    def test_unclassified(self):
        assert _classify("something entirely unrelated") == "other"


# ---------------------------------------------------------------------------
# AuditSink direct-push API
# ---------------------------------------------------------------------------

class TestAuditSink:
    def test_empty(self):
        sink = AuditSink()
        summary = sink.summary()
        assert summary["counts"] == {
            k: 0 for k in sink.iter_kinds()
        }
        assert summary["recent_events"] == []

    def test_each_kind_tallies(self):
        sink = AuditSink()
        sink.push(kind="tak_accepted")
        sink.push(kind="tak_rejected")
        sink.push(kind="tak_rejected")
        sink.push(kind="approach_arm_events")
        sink.push(kind="approach_abort_events")
        sink.push(kind="strike_events")
        sink.push(kind="drop_events")
        sink.push(kind="hmac_invalid_events")
        summary = sink.summary()
        counts = summary["counts"]
        assert counts["tak_accepted"] == 1
        assert counts["tak_rejected"] == 2
        assert counts["approach_arm_events"] == 1
        assert counts["approach_abort_events"] == 1
        assert counts["strike_events"] == 1
        assert counts["drop_events"] == 1
        assert counts["hmac_invalid_events"] == 1

    def test_auto_classify(self):
        sink = AuditSink()
        sink.push(kind="auto", message="APPROACH STRIKE START: track_id=1")
        assert sink.summary()["counts"]["strike_events"] == 1

    def test_bounded_recent_events(self):
        sink = AuditSink(maxlen=5)
        for i in range(20):
            sink.push(kind="tak_accepted", ref=str(i))
        summary = sink.summary(recent_limit=100)
        # Only last 5 retained due to ring cap
        assert len(summary["recent_events"]) == 5
        refs = [e["ref"] for e in summary["recent_events"]]
        assert refs == ["15", "16", "17", "18", "19"]

    def test_window_filter(self):
        sink = AuditSink()
        # Backdate one event
        sink.push(kind="tak_rejected", ts=time.time() - 10_000)
        sink.push(kind="tak_rejected")
        summary = sink.summary(window_seconds=60)
        # Only the recent one counts
        assert summary["counts"]["tak_rejected"] == 1
        assert len(summary["recent_events"]) == 1

    def test_recent_limit_caps_count(self):
        sink = AuditSink()
        for _i in range(40):
            sink.push(kind="tak_accepted")
        summary = sink.summary(recent_limit=10)
        assert len(summary["recent_events"]) == 10


# ---------------------------------------------------------------------------
# Logger attachment — auto-capture of hydra.audit records
# ---------------------------------------------------------------------------

class TestLoggerAttachment:
    def test_custom_sink_receives_audit_lines(self):
        sink = AuditSink()
        logger_name = "hydra.audit.test_custom"
        attach_to_logger(logger_name, sink=sink)
        logging.getLogger(logger_name).info(
            "TAK_CMD_REJECTED reason=no_allowlist sender=X"
        )
        summary = sink.summary()
        assert summary["counts"]["tak_rejected"] == 1

    def test_default_sink_captures_hydra_audit(self):
        sink = get_default_sink()
        logging.getLogger("hydra.audit").info(
            "APPROACH DROP START: track_id=42"
        )
        summary = sink.summary()
        assert summary["counts"]["drop_events"] >= 1


# ---------------------------------------------------------------------------
# /api/audit/summary endpoint
# ---------------------------------------------------------------------------

class TestAuditSummaryEndpoint:
    def test_empty(self, client):
        r = client.get("/api/audit/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["window_seconds"] > 0
        assert body["counts"]["tak_rejected"] == 0
        assert body["recent_events"] == []

    def test_endpoint_reflects_default_sink(self, client):
        logging.getLogger("hydra.audit").info(
            "TAK_CMD_ACCEPTED action=LOCK track_id=5 sender=ALPHA-1"
        )
        logging.getLogger("hydra.audit").warning(
            "TAK_CMD_REJECTED reason=hmac_invalid text=HYDRA LOCK 6"
        )
        r = client.get("/api/audit/summary")
        body = r.json()
        assert body["counts"]["tak_accepted"] >= 1
        assert body["counts"]["hmac_invalid_events"] >= 1

    def test_recent_limit_query_param(self, client):
        for _i in range(30):
            logging.getLogger("hydra.audit").info(
                "TAK_CMD_ACCEPTED action=LOCK track_id=1 sender=ALPHA-1"
            )
        r = client.get("/api/audit/summary?recent=5")
        body = r.json()
        assert len(body["recent_events"]) == 5

    def test_window_query_param_clamped(self, client):
        # Over the cap — server clamps to 86400
        r = client.get("/api/audit/summary?window_seconds=999999")
        assert r.status_code == 200
        assert r.json()["window_seconds"] == 86400
