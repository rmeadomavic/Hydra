"""Tests for the pytak-backed CoT emitter (issue #188).

Two layers of coverage:

1. Backend-selection tests on :mod:`hydra_detect.tak.__init__` so the
   ``HYDRA_COT_BACKEND`` env var actually flips the class returned by
   ``get_tak_output_cls``.

2. End-to-end emit tests that stand the pytak emitter up, capture the
   bytes it hands to the writer, parse the CoT XML, and assert the
   marker fields match what the legacy ``cot_builder`` produces. The
   pytak path delegates XML construction back to ``cot_builder`` so the
   parity assertions are exact on type/uid/callsign/lat/lon/HAE — the
   only difference between the two paths is the timestamp string
   (``time``/``start``/``stale`` shift by however long the build call
   took), so we assert structural equivalence on those fields rather
   than byte-equality.

The pytak writer is intercepted with a stub class — no real socket is
ever opened by these tests so they're safe under ``-k "not integration"``
and run on Windows boxes without multicast routes.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import time
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.tak.cot_builder import (
    build_detection_marker,
    build_self_sa,
)
from hydra_detect.tracker import TrackedObject, TrackingResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_track(track_id=1, label="person", confidence=0.9):
    return TrackedObject(
        track_id=track_id, x1=280, y1=200, x2=360, y2=400,
        confidence=confidence, class_id=0, label=label,
    )


def _make_tracking(*tracks):
    return TrackingResult(tracks=list(tracks), active_ids=len(tracks))


def _parse_cot(data: bytes) -> ET.Element:
    return ET.fromstring(data.decode("utf-8"))


class _StubWriter:
    """Captures bytes scheduled by ``PyTAKOutput._send_one``.

    Mirrors the duck-typed surface of an ``asyncio_dgram`` writer:
    ``send`` is a coroutine, ``close`` is a no-op. Each ``send`` call
    appends the bytes to ``self.sent`` so the test can pop them out
    later.
    """

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def pytak_emitter_factory():
    """Build a started PyTAKOutput with a stubbed writer per destination.

    Returns a tuple ``(emitter, writers)`` where ``writers`` is the list
    of ``_StubWriter`` instances bound to the open destinations. Caller
    is responsible for ``emitter.stop()``.
    """
    from hydra_detect.tak.pytak_emitter import PyTAKOutput

    created: list[_StubWriter] = []

    async def _fake_open_one(self, cot_url: str):
        w = _StubWriter()
        created.append(w)
        return w

    def _factory(**kwargs):
        mav = kwargs.pop("mavlink_io", None) or MagicMock()
        emitter = PyTAKOutput(mavlink_io=mav, **kwargs)
        return emitter

    with patch.object(
        __import__("hydra_detect.tak.pytak_emitter", fromlist=["PyTAKOutput"]).PyTAKOutput,
        "_open_one", _fake_open_one,
    ):
        yield _factory, created


# =====================================================================
# Group A: backend-selection plumbing
# =====================================================================

class TestBackendSelection:
    def _reload_pkg(self):
        # Re-import so the eager re-export at module top picks up the env.
        import hydra_detect.tak as tak_pkg
        return importlib.reload(tak_pkg)

    def test_default_is_pytak(self, monkeypatch):
        monkeypatch.delenv("HYDRA_COT_BACKEND", raising=False)
        pkg = self._reload_pkg()
        cls = pkg.get_tak_output_cls()
        assert cls.__name__ == "PyTAKOutput"

    def test_legacy_selectable(self, monkeypatch):
        monkeypatch.setenv("HYDRA_COT_BACKEND", "legacy")
        pkg = self._reload_pkg()
        cls = pkg.get_tak_output_cls()
        assert cls.__name__ == "TAKOutput"

    def test_invalid_value_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("HYDRA_COT_BACKEND", "carrier-pigeon")
        pkg = self._reload_pkg()
        cls = pkg.get_tak_output_cls()
        assert cls.__name__ == "PyTAKOutput"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("HYDRA_COT_BACKEND", "Legacy")
        pkg = self._reload_pkg()
        assert pkg.get_tak_output_cls().__name__ == "TAKOutput"

    def teardown_method(self, _method):
        # Reset env + reload so other test modules see the default state.
        os.environ.pop("HYDRA_COT_BACKEND", None)
        import hydra_detect.tak as tak_pkg
        importlib.reload(tak_pkg)


# =====================================================================
# Group B: lifecycle + status (mirrors test_tak.py expectations)
# =====================================================================

class TestPyTAKLifecycle:
    def test_start_opens_writers_and_starts_thread(self, pytak_emitter_factory):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (None, None, None)
        emitter = factory(mavlink_io=mav, sa_interval=100, emit_interval=100)
        assert emitter.start()
        try:
            # Multicast + 0 unicast => exactly one writer
            assert len(writers) == 1
            assert emitter.is_running()
        finally:
            emitter.stop()
            assert not emitter.is_running()

    def test_status_reports_pytak_backend(self, pytak_emitter_factory):
        factory, _ = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (None, None, None)
        emitter = factory(mavlink_io=mav, sa_interval=100, emit_interval=100)
        emitter.start()
        try:
            status = emitter.get_status()
            assert status["backend"] == "pytak"
            assert status["enabled"] is True
            assert status["callsign"] == "HYDRA-1"
        finally:
            emitter.stop()

    def test_unicast_targets_open_extra_writers(self, pytak_emitter_factory):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (None, None, None)
        emitter = factory(
            mavlink_io=mav,
            sa_interval=100, emit_interval=100,
            unicast_targets="10.0.0.1:4242, 10.0.0.2:6969",
        )
        emitter.start()
        try:
            # mcast + 2 unicast = 3 writers
            assert len(writers) == 3
        finally:
            emitter.stop()


# =====================================================================
# Group C: outbound parity with legacy emitter
# =====================================================================

class TestPyTAKEmissions:
    def test_self_sa_lands_on_writer_with_friendly_air_type(
        self, pytak_emitter_factory,
    ):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 100.0)
        mav.get_heading_deg.return_value = 90.0
        mav.get_telemetry.return_value = {"groundspeed": 5.0}

        emitter = factory(
            mavlink_io=mav,
            sa_interval=0.1, emit_interval=100,
        )
        emitter.start()
        try:
            time.sleep(1.0)
        finally:
            emitter.stop()

        all_sent = [b for w in writers for b in w.sent]
        assert all_sent, "no bytes captured"
        sa_events = [b for b in all_sent if b"a-f-A-M-F-Q" in b]
        assert sa_events, "no self-SA event found"

    def test_no_gps_no_send(self, pytak_emitter_factory):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (None, None, None)
        emitter = factory(mavlink_io=mav, sa_interval=0.1, emit_interval=100)
        emitter.start()
        try:
            time.sleep(0.6)
        finally:
            emitter.stop()
        assert all(not w.sent for w in writers)

    def test_detection_emit_for_alert_class(self, pytak_emitter_factory):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 50.0)
        mav.get_heading_deg.return_value = 180.0
        mav.get_telemetry.return_value = {"groundspeed": 0.0}
        mav.estimate_target_position.return_value = (34.001, -117.999)

        emitter = factory(
            mavlink_io=mav,
            sa_interval=100, emit_interval=0.1,
        )
        emitter.start()
        try:
            emitter.push(_make_tracking(_make_track(label="person")), {"person"}, None)
            time.sleep(1.0)
        finally:
            emitter.stop()

        all_sent = [b for w in writers for b in w.sent]
        # CoT type for "person" is "a-u-G-U-C-I" via type_mapping.
        assert any(b"a-u-G-U-C-I" in b for b in all_sent), \
            "no person-detection CoT found"

    def test_alert_class_filter_respected(self, pytak_emitter_factory):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 50.0)
        mav.get_heading_deg.return_value = 180.0
        mav.get_telemetry.return_value = {"groundspeed": 0.0}
        mav.estimate_target_position.return_value = (34.001, -117.999)

        emitter = factory(
            mavlink_io=mav,
            sa_interval=100, emit_interval=0.1,
        )
        emitter.start()
        try:
            emitter.push(
                _make_tracking(_make_track(label="car")), {"person"}, None,
            )
            time.sleep(0.6)
        finally:
            emitter.stop()

        all_sent = [b for w in writers for b in w.sent]
        assert not any(b"a-u-G-E-V" in b for b in all_sent), \
            "vehicle CoT leaked despite alert_classes filter"

    def test_unicast_targets_each_get_a_copy(self, pytak_emitter_factory):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 100.0)
        mav.get_heading_deg.return_value = 90.0
        mav.get_telemetry.return_value = {"groundspeed": 0.0}

        emitter = factory(
            mavlink_io=mav, sa_interval=0.1, emit_interval=100,
            unicast_targets="10.0.0.1:4242, 10.0.0.2:6969",
        )
        emitter.start()
        try:
            time.sleep(1.0)
        finally:
            emitter.stop()

        # mcast + 2 unicast = 3 writers, each should have at least 1 packet.
        assert len(writers) == 3
        for w in writers:
            assert w.sent, f"writer {w} got nothing"


# =====================================================================
# Group D: structural parity with the legacy CoT XML
# =====================================================================

class TestStructuralParity:
    """The pytak emitter routes XML construction through the same
    ``cot_builder`` helpers the legacy emitter uses, so the only
    difference between the two byte-streams is the timestamp string
    (each call rebuilds the XML so ``time``/``start``/``stale`` shift
    by a few µs). These assertions confirm structural equivalence on
    every other field that operators care about.
    """

    def test_self_sa_xml_matches_legacy_builder(self, pytak_emitter_factory):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.05, -118.25, 150.5)
        mav.get_heading_deg.return_value = 270.5
        mav.get_telemetry.return_value = {"groundspeed": 5.2}

        emitter = factory(
            mavlink_io=mav, sa_interval=0.1, emit_interval=100,
            callsign="HYDRA-PARITY",
        )
        emitter.start()
        try:
            time.sleep(0.7)
        finally:
            emitter.stop()

        all_sent = [b for w in writers for b in w.sent]
        sa = next((b for b in all_sent if b"a-f-A-M-F-Q" in b), None)
        assert sa is not None

        from_pytak = _parse_cot(sa)

        legacy_bytes = build_self_sa(
            uid="HYDRA-PARITY-SA",
            callsign="HYDRA-PARITY",
            lat=34.05, lon=-118.25, hae=150.5,
            heading=270.5, speed=5.2,
        )
        from_legacy = _parse_cot(legacy_bytes)

        # Structural fields the operator actually relies on
        for attr in ("version", "uid", "type", "how"):
            assert from_pytak.get(attr) == from_legacy.get(attr), attr

        for attr in ("lat", "lon", "hae", "ce", "le"):
            assert from_pytak.find("point").get(attr) \
                == from_legacy.find("point").get(attr), attr

        assert from_pytak.find("detail/contact").get("callsign") \
            == from_legacy.find("detail/contact").get("callsign")

        for attr in ("course", "speed"):
            assert from_pytak.find("detail/track").get(attr) \
                == from_legacy.find("detail/track").get(attr), attr

        assert from_pytak.find("detail/precisionlocation") is not None
        assert from_pytak.find("detail/remarks").text \
            == from_legacy.find("detail/remarks").text

    def test_detection_marker_xml_matches_legacy_builder(
        self, pytak_emitter_factory,
    ):
        factory, writers = pytak_emitter_factory
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 50.0)
        mav.get_heading_deg.return_value = 180.0
        mav.get_telemetry.return_value = {"groundspeed": 0.0}
        mav.estimate_target_position.return_value = (34.001, -117.999)

        emitter = factory(
            mavlink_io=mav, sa_interval=100, emit_interval=0.1,
            callsign="HYDRA-DET",
        )
        emitter.start()
        try:
            emitter.push(
                _make_tracking(_make_track(track_id=7, label="person")),
                {"person"}, None,
            )
            time.sleep(0.7)
        finally:
            emitter.stop()

        all_sent = [b for w in writers for b in w.sent]
        det = next((b for b in all_sent if b"-DET-7" in b), None)
        assert det is not None, "expected detection bytes containing -DET-7"
        from_pytak = _parse_cot(det)

        # The pipeline uses the same projection helpers, so a hand-built
        # legacy event with the same inputs is the byte-level oracle on
        # everything except timestamps + ce (see note above).
        legacy = _parse_cot(build_detection_marker(
            uid="HYDRA-DET-DET-7",
            callsign="HYDRA-DET-person-7",
            cot_type="a-u-G-U-C-I",
            lat=34.001, lon=-117.999, hae=50.0,
            confidence=0.9, label="person", track_id=7,
        ))

        assert from_pytak.get("type") == legacy.get("type")
        assert from_pytak.get("uid") == legacy.get("uid")
        assert from_pytak.get("how") == legacy.get("how")
        assert from_pytak.find("point").get("ce") == legacy.find("point").get("ce")
        assert from_pytak.find("detail/contact").get("callsign") \
            == legacy.find("detail/contact").get("callsign")
        assert from_pytak.find("detail/remarks").text \
            == legacy.find("detail/remarks").text


# =====================================================================
# Group E: misc plumbing
# =====================================================================

class TestParseUnicastTargets:
    def test_empty_string(self):
        from hydra_detect.tak.pytak_emitter import _parse_unicast_targets
        assert _parse_unicast_targets("") == []

    def test_single(self):
        from hydra_detect.tak.pytak_emitter import _parse_unicast_targets
        assert _parse_unicast_targets("192.168.1.50:4242") \
            == [("192.168.1.50", 4242)]

    def test_multiple(self):
        from hydra_detect.tak.pytak_emitter import _parse_unicast_targets
        result = _parse_unicast_targets("10.0.0.1:6969, 10.0.0.2:4242")
        assert result == [("10.0.0.1", 6969), ("10.0.0.2", 4242)]

    def test_invalid_skipped(self):
        from hydra_detect.tak.pytak_emitter import _parse_unicast_targets
        result = _parse_unicast_targets("10.0.0.1:6969, bad, 10.0.0.2:4242")
        assert len(result) == 2


class TestSendOneAcceptsBothWriterShapes:
    """``_send_one`` must work with both ``asyncio_dgram`` (``send``)
    and ``asyncio.StreamWriter`` (``write`` + ``drain``) shapes — pytak
    returns either depending on transport.
    """

    def test_send_writer_uses_send(self):
        from hydra_detect.tak.pytak_emitter import PyTAKOutput
        emitter = PyTAKOutput(mavlink_io=MagicMock())
        loop = asyncio.new_event_loop()
        try:
            stub = _StubWriter()
            loop.run_until_complete(emitter._send_one(stub, b"<event/>"))
            assert stub.sent == [b"<event/>"]
        finally:
            loop.close()

    def test_stream_writer_uses_write_and_drain(self):
        from hydra_detect.tak.pytak_emitter import PyTAKOutput
        emitter = PyTAKOutput(mavlink_io=MagicMock())

        class _StreamLike:
            def __init__(self):
                self.written: list[bytes] = []
                self.drained = False

            def write(self, data):
                self.written.append(data)

            async def drain(self):
                self.drained = True

        loop = asyncio.new_event_loop()
        try:
            sw = _StreamLike()
            loop.run_until_complete(emitter._send_one(sw, b"<event/>"))
            assert sw.written == [b"<event/>"]
            assert sw.drained
        finally:
            loop.close()
