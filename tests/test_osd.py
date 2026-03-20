"""Tests for FPV OSD overlay module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hydra_detect.msp_displayport import (
    MspDisplayPort,
    MspOsdData,
    _msp_frame,
    heartbeat_frame,
    clear_frame,
    draw_frame,
    write_string_frame,
)
from hydra_detect.osd import FpvOsd, OSDState, build_osd_state
from hydra_detect.tracker import TrackedObject, TrackingResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink_mock() -> MagicMock:
    """Create a mock MAVLinkIO with inner _mav and _lock."""
    mav = MagicMock()
    mav._mav = MagicMock()
    mav._lock = MagicMock()
    # Make the lock usable as a context manager
    mav._lock.__enter__ = MagicMock(return_value=None)
    mav._lock.__exit__ = MagicMock(return_value=False)
    return mav


def _make_tracking_result(tracks: list[dict] | None = None) -> TrackingResult:
    """Build a TrackingResult from simple dicts."""
    if tracks is None:
        tracks = []
    objs = [
        TrackedObject(
            track_id=t.get("track_id", 1),
            x1=t.get("x1", 100.0),
            y1=t.get("y1", 100.0),
            x2=t.get("x2", 200.0),
            y2=t.get("y2", 200.0),
            confidence=t.get("confidence", 0.9),
            class_id=t.get("class_id", 0),
            label=t.get("label", "person"),
        )
        for t in tracks
    ]
    return TrackingResult(tracks=objs, active_ids=len(objs))


# ---------------------------------------------------------------------------
# build_osd_state
# ---------------------------------------------------------------------------

class TestBuildOsdState:
    def test_empty_tracking(self):
        state = build_osd_state(
            _make_tracking_result(), fps=10.0, inference_ms=50.0,
            locked_track_id=None, lock_mode=None, gps=None,
        )
        assert state.active_tracks == 0
        assert state.fps == 10.0
        assert state.inference_ms == 50.0
        assert state.locked_track_id is None
        assert state.gps_fix == 0

    def test_with_tracks_and_lock(self):
        tracks = [
            {"track_id": 5, "label": "person"},
            {"track_id": 8, "label": "vehicle"},
        ]
        state = build_osd_state(
            _make_tracking_result(tracks), fps=12.0, inference_ms=30.0,
            locked_track_id=5, lock_mode="track",
            gps={"fix": 3, "lat": 340000000, "lon": -1180000000},
        )
        assert state.active_tracks == 2
        assert state.locked_track_id == 5
        assert state.lock_mode == "track"
        assert state.locked_label == "person"
        assert state.gps_fix == 3

    def test_lock_on_missing_track(self):
        """Locked track ID not in results — label stays empty."""
        state = build_osd_state(
            _make_tracking_result([{"track_id": 1}]),
            fps=10.0, inference_ms=20.0,
            locked_track_id=99, lock_mode="strike", gps=None,
        )
        assert state.locked_track_id == 99
        assert state.locked_label == ""


# ---------------------------------------------------------------------------
# FpvOsd — statustext mode
# ---------------------------------------------------------------------------

class TestFpvOsdStatustext:
    def test_sends_statustext(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(fps=15.0, inference_ms=40.0, active_tracks=3)

        osd.update(state)

        mav.send_statustext.assert_called_once()
        text = mav.send_statustext.call_args[0][0]
        assert "T:3" in text
        assert "15fps" in text
        assert "40ms" in text

    def test_locked_target_in_statustext(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=25.0, active_tracks=1,
            locked_track_id=7, lock_mode="strike", locked_label="person",
        )

        osd.update(state)

        text = mav.send_statustext.call_args[0][0]
        assert "LK#7S:person" in text

    def test_rate_limiting(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=1.0)
        state = OSDState(fps=10.0, inference_ms=20.0, active_tracks=1)

        osd.update(state)
        osd.update(state)  # Should be throttled

        assert mav.send_statustext.call_count == 1

    def test_statustext_truncated_to_50_chars(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=25.0, active_tracks=1,
            locked_track_id=7, lock_mode="track",
            locked_label="very_long_label_name",
        )

        osd.update(state)

        text = mav.send_statustext.call_args[0][0]
        assert len(text) <= 50


# ---------------------------------------------------------------------------
# FpvOsd — named_value mode
# ---------------------------------------------------------------------------

class TestFpvOsdNamedValue:
    def test_sends_scr_user_params(self):
        """named_value mode sends PARAM_SET for SCR_USER1-6."""
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="named_value", update_interval=0.0)
        state = OSDState(
            fps=12.0, inference_ms=35.0, active_tracks=2,
            locked_track_id=None, lock_mode=None, gps_fix=3,
        )

        osd.update(state)

        inner = mav._mav.mav
        # Should send PARAM_SET for: USER1(fps), USER2(infms), USER3(trks),
        # USER4(lkid=-1), USER5(lkmod=0), USER6(top_class_id)
        assert inner.param_set_send.call_count == 6

        # Verify the param names and values from the calls
        calls = inner.param_set_send.call_args_list
        param_map = {}
        for call in calls:
            name = call[0][2].rstrip(b"\x00").decode("utf-8")
            value = call[0][3]
            param_map[name] = value

        assert abs(param_map["SCR_USER1"] - 12.0) < 0.01
        assert abs(param_map["SCR_USER2"] - 35.0) < 0.01
        assert abs(param_map["SCR_USER3"] - 2.0) < 0.01
        assert abs(param_map["SCR_USER4"] - (-1.0)) < 0.01
        assert abs(param_map["SCR_USER5"] - 0.0) < 0.01
        assert abs(param_map["SCR_USER6"] - (-1.0)) < 0.01  # top_class_id, no detections

    def test_sends_lock_data_when_locked(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="named_value", update_interval=0.0)
        state = OSDState(
            fps=10.0, inference_ms=20.0, active_tracks=1,
            locked_track_id=5, lock_mode="strike", gps_fix=0,
        )

        osd.update(state)

        inner = mav._mav.mav
        # All 6 SCR_USER params sent
        assert inner.param_set_send.call_count == 6

        # Verify lock-specific values
        calls = inner.param_set_send.call_args_list
        param_map = {}
        for call in calls:
            name = call[0][2].rstrip(b"\x00").decode("utf-8")
            value = call[0][3]
            param_map[name] = value

        assert abs(param_map["SCR_USER4"] - 5.0) < 0.01    # locked_track_id
        assert abs(param_map["SCR_USER5"] - 2.0) < 0.01    # strike mode

    def test_no_send_when_mav_disconnected(self):
        mav = _make_mavlink_mock()
        mav._mav = None  # Disconnected
        osd = FpvOsd(mav, mode="named_value", update_interval=0.0)
        state = OSDState(fps=10.0, inference_ms=20.0, active_tracks=0)

        # Should not raise
        osd.update(state)


# ---------------------------------------------------------------------------
# OSDState defaults
# ---------------------------------------------------------------------------

class TestFpvOsdValidation:
    def test_invalid_mode_falls_back_to_statustext(self):
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="bogus", update_interval=0.0)
        assert osd.mode == "statustext"

    @patch("hydra_detect.osd.MspDisplayPort")
    def test_valid_modes_accepted(self, mock_msp_cls):
        mav = _make_mavlink_mock()
        for mode in ("statustext", "named_value", "msp_displayport"):
            osd = FpvOsd(mav, mode=mode, update_interval=0.0)
            assert osd.mode == mode

    def test_min_update_interval_clamped(self):
        """Interval below 50ms should be clamped to 50ms."""
        mav = _make_mavlink_mock()
        osd = FpvOsd(mav, mode="statustext", update_interval=0.01)
        # Internal interval should be clamped to 0.05
        assert osd._interval >= 0.05


class TestOsdState:
    def test_defaults(self):
        state = OSDState()
        assert state.fps == 0.0
        assert state.inference_ms == 0.0
        assert state.active_tracks == 0
        assert state.locked_track_id is None
        assert state.lock_mode is None
        assert state.locked_label == ""
        assert state.gps_fix == 0
        assert state.gps_lat is None
        assert state.gps_lon is None
        assert state.latest_det_label == ""
        assert state.latest_det_conf == 0.0


# ---------------------------------------------------------------------------
# build_osd_state — GPS and detection fields
# ---------------------------------------------------------------------------

class TestBuildOsdStateExtended:
    def test_gps_lat_lon_extracted(self):
        """GPS lat/lon should be converted from 1e7 integer to float degrees."""
        state = build_osd_state(
            _make_tracking_result(), fps=10.0, inference_ms=20.0,
            locked_track_id=None, lock_mode=None,
            gps={"fix": 3, "lat": 340500000, "lon": -1182500000},
        )
        assert state.gps_lat is not None
        assert abs(state.gps_lat - 34.05) < 1e-6
        assert abs(state.gps_lon - (-118.25)) < 1e-6

    def test_gps_none_when_no_fix(self):
        state = build_osd_state(
            _make_tracking_result(), fps=10.0, inference_ms=20.0,
            locked_track_id=None, lock_mode=None, gps=None,
        )
        assert state.gps_lat is None
        assert state.gps_lon is None

    def test_latest_detection_picks_highest_confidence(self):
        tracks = [
            {"track_id": 1, "label": "car", "confidence": 0.7},
            {"track_id": 2, "label": "person", "confidence": 0.95},
            {"track_id": 3, "label": "dog", "confidence": 0.6},
        ]
        state = build_osd_state(
            _make_tracking_result(tracks), fps=10.0, inference_ms=20.0,
            locked_track_id=None, lock_mode=None, gps=None,
        )
        assert state.latest_det_label == "person"
        assert abs(state.latest_det_conf - 0.95) < 1e-6

    def test_no_detections_empty_label(self):
        state = build_osd_state(
            _make_tracking_result(), fps=10.0, inference_ms=20.0,
            locked_track_id=None, lock_mode=None, gps=None,
        )
        assert state.latest_det_label == ""
        assert state.latest_det_conf == 0.0


# ---------------------------------------------------------------------------
# MSP v1 frame encoding
# ---------------------------------------------------------------------------

class TestMspFrameEncoding:
    def test_frame_header_and_structure(self):
        """MSP v1 frame: $M< + size + cmd + payload + checksum."""
        frame = _msp_frame(182, bytearray([0, 18, 50, 0, 0]))
        assert frame[:3] == b"$M<"
        assert frame[3] == 5       # payload size
        assert frame[4] == 182     # command
        assert frame[5:10] == bytes([0, 18, 50, 0, 0])  # payload
        # Verify checksum: XOR of size, cmd, and all payload bytes
        expected_cksum = 5 ^ 182 ^ 0 ^ 18 ^ 50 ^ 0 ^ 0
        assert frame[10] == (expected_cksum & 0xFF)

    def test_heartbeat_frame(self):
        frame = heartbeat_frame(rows=18, cols=50)
        assert frame[:3] == b"$M<"
        assert frame[3] == 5   # payload length
        assert frame[4] == 182  # MSP_DISPLAYPORT
        assert frame[5] == 0   # sub-cmd heartbeat
        assert frame[6] == 18  # rows
        assert frame[7] == 50  # cols

    def test_clear_frame(self):
        frame = clear_frame()
        assert frame[:3] == b"$M<"
        assert frame[3] == 1   # payload length
        assert frame[5] == 2   # sub-cmd clear

    def test_draw_frame(self):
        frame = draw_frame()
        assert frame[:3] == b"$M<"
        assert frame[3] == 1   # payload length
        assert frame[5] == 4   # sub-cmd draw

    def test_write_string_frame(self):
        frame = write_string_frame(0, 5, "HELLO", attr=0)
        assert frame[:3] == b"$M<"
        payload_size = 4 + 5  # sub_cmd + row + col + attr + 5 chars
        assert frame[3] == payload_size
        assert frame[5] == 3   # sub-cmd write
        assert frame[6] == 0   # row
        assert frame[7] == 5   # col
        assert frame[8] == 0   # attr
        assert frame[9:14] == b"HELLO"

    def test_checksum_correctness(self):
        """Verify checksum is XOR of size ^ cmd ^ payload bytes."""
        payload = bytearray([3, 0, 10, 0]) + b"TEST"
        frame = _msp_frame(182, payload)
        size = len(payload)
        cksum = size ^ 182
        for b in payload:
            cksum ^= b
        cksum &= 0xFF
        assert frame[-1] == cksum


# ---------------------------------------------------------------------------
# MspDisplayPort driver
# ---------------------------------------------------------------------------

class TestMspDisplayPort:
    def test_format_status_line_basic(self):
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData(fps=12.0, inference_ms=35.0, active_tracks=3)
        line = driver._format_status_line(data)
        assert "T:3" in line
        assert "12fps" in line
        assert "35ms" in line

    def test_format_status_line_with_lock(self):
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData(
            fps=10.0, inference_ms=20.0, active_tracks=1,
            locked_track_id=5, lock_mode="track",
        )
        line = driver._format_status_line(data)
        assert "LK#5TRK" in line

    def test_format_status_line_strike_mode(self):
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData(
            fps=10.0, inference_ms=20.0, active_tracks=1,
            locked_track_id=5, lock_mode="strike",
        )
        line = driver._format_status_line(data)
        assert "LK#5STK" in line

    def test_format_status_line_truncated_to_canvas(self):
        driver = MspDisplayPort(canvas_cols=20, update_interval=0.1)
        data = MspOsdData(
            fps=10.0, inference_ms=20.0, active_tracks=99,
            locked_track_id=12345, lock_mode="track",
        )
        line = driver._format_status_line(data)
        assert len(line) <= 20

    def test_format_gps_line_no_gps(self):
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData()
        assert driver._format_gps_line(data) == "NO GPS"

    def test_format_gps_line_latlon(self):
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData(gps_lat=34.05, gps_lon=-118.25)
        line = driver._format_gps_line(data)
        assert "34.05" in line
        assert "-118.25" in line

    def test_format_det_line_empty(self):
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData()
        assert driver._format_det_line(data) == ""

    def test_format_det_line_with_detection(self):
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData(latest_det_label="person", latest_det_conf=0.92)
        line = driver._format_det_line(data)
        assert line == "person 0.92"

    @patch("hydra_detect.msp_displayport.serial.Serial")
    def test_render_frame_sends_all_commands(self, mock_serial_cls):
        """A render cycle should send heartbeat, clear, writes, and draw."""
        mock_ser = MagicMock()
        mock_serial_cls.return_value = mock_ser

        driver = MspDisplayPort(update_interval=0.1)
        driver._ser = mock_ser

        data = MspOsdData(
            fps=12.0, inference_ms=35.0, active_tracks=3,
            gps_lat=34.05, gps_lon=-118.25,
            latest_det_label="person", latest_det_conf=0.92,
        )
        driver._render_frame(data)

        # Should have written: heartbeat, clear, status row, gps row,
        # detection row, draw = at least 6 writes
        assert mock_ser.write.call_count >= 5

        # First call should be heartbeat (starts with $M<)
        first_frame = mock_ser.write.call_args_list[0][0][0]
        assert first_frame[:3] == b"$M<"

    @patch("hydra_detect.msp_displayport.serial.Serial")
    def test_serial_disconnect_does_not_crash(self, mock_serial_cls):
        """If serial write raises, the driver should handle it gracefully."""
        from serial import SerialException

        mock_ser = MagicMock()
        mock_ser.write.side_effect = SerialException("port gone")
        mock_serial_cls.return_value = mock_ser

        driver = MspDisplayPort(update_interval=0.1)
        driver._ser = mock_ser

        data = MspOsdData(fps=10.0, inference_ms=20.0, active_tracks=0)
        # Should not raise
        driver._render_frame(data)
        # Serial should be closed after failure
        assert driver._ser is None

    def test_update_is_thread_safe(self):
        """update() should safely replace the data snapshot."""
        driver = MspDisplayPort(update_interval=0.1)
        data = MspOsdData(fps=15.0, active_tracks=5)
        driver.update(data)
        assert driver._data.fps == 15.0
        assert driver._data.active_tracks == 5


# ---------------------------------------------------------------------------
# FpvOsd — msp_displayport mode integration
# ---------------------------------------------------------------------------

class TestFpvOsdMspDisplayPort:
    @patch("hydra_detect.osd.MspDisplayPort")
    def test_msp_mode_creates_and_starts_driver(self, mock_msp_cls):
        mav = _make_mavlink_mock()
        osd = FpvOsd(
            mav, mode="msp_displayport", update_interval=0.1,
            serial_port="/dev/ttyUSB0", serial_baud=115200,
        )
        assert osd.mode == "msp_displayport"
        mock_msp_cls.assert_called_once()
        mock_msp_cls.return_value.start.assert_called_once()

    @patch("hydra_detect.osd.MspDisplayPort")
    def test_msp_mode_forwards_state(self, mock_msp_cls):
        mav = _make_mavlink_mock()
        osd = FpvOsd(
            mav, mode="msp_displayport", update_interval=0.0,
        )
        state = OSDState(
            fps=12.0, inference_ms=35.0, active_tracks=3,
            gps_lat=34.05, gps_lon=-118.25,
            latest_det_label="person", latest_det_conf=0.92,
        )
        osd.update(state)
        mock_msp_cls.return_value.update.assert_called_once()

    @patch("hydra_detect.osd.MspDisplayPort")
    def test_msp_mode_does_not_use_mavlink(self, mock_msp_cls):
        """MSP mode should not send anything via MAVLink."""
        mav = _make_mavlink_mock()
        osd = FpvOsd(
            mav, mode="msp_displayport", update_interval=0.0,
        )
        state = OSDState(fps=10.0, inference_ms=20.0, active_tracks=0)
        osd.update(state)
        mav.send_statustext.assert_not_called()
        mav._mav.mav.param_set_send.assert_not_called()
