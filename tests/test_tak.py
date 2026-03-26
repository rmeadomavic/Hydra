"""Tests for TAK/ATAK CoT output integration."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.tak.cot_builder import (
    build_detection_marker,
    build_self_sa,
    build_video_feed,
)
from hydra_detect.tak.tak_output import TAKOutput, _parse_unicast_targets
from hydra_detect.tak.type_mapping import DEFAULT_COT_TYPE, get_cot_type
from hydra_detect.tracker import TrackedObject, TrackingResult


# ── helpers ──────────────────────────────────────────────────────────

def _make_track(track_id=1, label="person", confidence=0.9):
    return TrackedObject(
        track_id=track_id, x1=280, y1=200, x2=360, y2=400,
        confidence=confidence, class_id=0, label=label,
    )


def _make_tracking(*tracks):
    return TrackingResult(tracks=list(tracks), active_ids=len(tracks))


def _parse_cot(data: bytes) -> ET.Element:
    return ET.fromstring(data.decode("utf-8"))


# =====================================================================
# Group A: cot_builder tests (pure functions)
# =====================================================================

class TestBuildSelfSA:
    def test_returns_valid_xml(self):
        data = build_self_sa("UID-1", "HYDRA-1", 34.0, -118.0, 100.0)
        root = _parse_cot(data)
        assert root.tag == "event"
        assert root.get("version") == "2.0"

    def test_type_is_friendly_air(self):
        root = _parse_cot(build_self_sa("U", "C", 0, 0, 0))
        assert root.get("type") == "a-f-A-M-F-Q"

    def test_how_is_machine_gps(self):
        root = _parse_cot(build_self_sa("U", "C", 0, 0, 0))
        assert root.get("how") == "m-g"

    def test_times_are_iso_utc(self):
        root = _parse_cot(build_self_sa("U", "C", 0, 0, 0, stale_seconds=30))
        t = datetime.fromisoformat(root.get("time").replace("Z", "+00:00"))
        s = datetime.fromisoformat(root.get("stale").replace("Z", "+00:00"))
        assert t.tzinfo is not None
        assert s > t

    def test_point_element(self):
        root = _parse_cot(build_self_sa("U", "C", 34.05, -118.25, 150.5))
        point = root.find("point")
        assert point is not None
        assert float(point.get("lat")) == pytest.approx(34.05, abs=1e-5)
        assert float(point.get("lon")) == pytest.approx(-118.25, abs=1e-5)
        assert float(point.get("hae")) == pytest.approx(150.5, abs=0.1)

    def test_contact_callsign(self):
        root = _parse_cot(build_self_sa("U", "HYDRA-1", 0, 0, 0))
        contact = root.find("detail/contact")
        assert contact is not None
        assert contact.get("callsign") == "HYDRA-1"

    def test_includes_track_element_when_heading_given(self):
        root = _parse_cot(build_self_sa("U", "C", 0, 0, 0, heading=270.5, speed=5.2))
        track = root.find("detail/track")
        assert track is not None
        assert float(track.get("course")) == pytest.approx(270.5, abs=0.1)
        assert float(track.get("speed")) == pytest.approx(5.2, abs=0.1)

    def test_uid_preserved(self):
        root = _parse_cot(build_self_sa("HYDRA-1-SA", "C", 0, 0, 0))
        assert root.get("uid") == "HYDRA-1-SA"


class TestBuildDetectionMarker:
    def test_returns_valid_xml(self):
        data = build_detection_marker(
            "DET-1", "CS", "a-u-G", 34.0, -118.0, 0, 0.9, "person", 1,
        )
        root = _parse_cot(data)
        assert root.tag == "event"

    def test_how_is_human_estimated(self):
        root = _parse_cot(build_detection_marker(
            "D", "C", "a-u-G", 0, 0, 0, 0.5, "car", 2,
        ))
        assert root.get("how") == "h-e"

    def test_uid_contains_track_id(self):
        root = _parse_cot(build_detection_marker(
            "HYDRA-DET-42", "C", "a-u-G", 0, 0, 0, 0.5, "car", 42,
        ))
        assert "42" in root.get("uid")

    def test_remarks_include_label_and_confidence(self):
        root = _parse_cot(build_detection_marker(
            "D", "C", "a-u-G", 0, 0, 0, 0.92, "person", 7,
        ))
        remarks = root.find("detail/remarks")
        assert remarks is not None
        assert "person" in remarks.text
        assert "92%" in remarks.text
        assert "#7" in remarks.text

    def test_ce_is_50(self):
        root = _parse_cot(build_detection_marker(
            "D", "C", "a-u-G", 0, 0, 0, 0.5, "car", 1,
        ))
        assert root.find("point").get("ce") == "50"

    def test_stale_default_60s(self):
        root = _parse_cot(build_detection_marker(
            "D", "C", "a-u-G", 0, 0, 0, 0.5, "car", 1,
        ))
        t = datetime.fromisoformat(root.get("time").replace("Z", "+00:00"))
        s = datetime.fromisoformat(root.get("stale").replace("Z", "+00:00"))
        delta = (s - t).total_seconds()
        assert 58 < delta < 62

    def test_custom_cot_type_preserved(self):
        root = _parse_cot(build_detection_marker(
            "D", "C", "a-u-S-X", 0, 0, 0, 0.5, "boat", 1,
        ))
        assert root.get("type") == "a-u-S-X"


class TestBuildVideoFeed:
    def test_type_b_i_v(self):
        root = _parse_cot(build_video_feed(
            "V", "C", "rtsp://1.2.3.4:8554/hydra", 0, 0, 0,
        ))
        assert root.get("type") == "b-i-v"

    def test_contains_rtsp_url(self):
        url = "rtsp://10.41.113.5:8554/hydra"
        root = _parse_cot(build_video_feed("V", "C", url, 0, 0, 0))
        video = root.find("detail/__video")
        assert video is not None
        assert video.get("url") == url

    def test_callsign_has_video_suffix(self):
        root = _parse_cot(build_video_feed("V", "HYDRA-1", "rtsp://x", 0, 0, 0))
        contact = root.find("detail/contact")
        assert "Video" in contact.get("callsign")


class TestXMLEscaping:
    def test_special_chars_in_callsign(self):
        data = build_self_sa("U", "HYDRA<>&1", 0, 0, 0)
        root = _parse_cot(data)
        assert root.find("detail/contact").get("callsign") == "HYDRA<>&1"


# =====================================================================
# Group B: type_mapping tests
# =====================================================================

class TestTypeMapping:
    def test_person_maps_to_infantry(self):
        assert get_cot_type("person") == "a-u-G-U-C-I"

    def test_car_maps_to_vehicle(self):
        assert get_cot_type("car").startswith("a-u-G-E-V")

    def test_boat_maps_to_surface(self):
        assert get_cot_type("boat") == "a-u-S-X"

    def test_airplane_maps_to_air(self):
        assert get_cot_type("airplane") == "a-u-A"

    def test_unknown_label_returns_default(self):
        assert get_cot_type("toothbrush") == DEFAULT_COT_TYPE

    def test_all_mappings_start_with_a(self):
        """All CoT type codes should start with 'a-' (atom)."""
        from hydra_detect.tak.type_mapping import YOLO_TO_COT_TYPE
        for label, cot_type in YOLO_TO_COT_TYPE.items():
            assert cot_type.startswith("a-"), f"{label} -> {cot_type}"


# =====================================================================
# Group C: tak_output tests (mock socket and MAVLink)
# =====================================================================

class TestParseUnicastTargets:
    def test_empty_string(self):
        assert _parse_unicast_targets("") == []

    def test_single_target(self):
        assert _parse_unicast_targets("192.168.1.50:4242") == [("192.168.1.50", 4242)]

    def test_multiple_targets(self):
        result = _parse_unicast_targets("10.0.0.1:6969, 10.0.0.2:4242")
        assert result == [("10.0.0.1", 6969), ("10.0.0.2", 4242)]

    def test_invalid_entry_skipped(self):
        result = _parse_unicast_targets("10.0.0.1:6969, bad, 10.0.0.2:4242")
        assert len(result) == 2


class TestTAKOutputLifecycle:
    def test_start_creates_socket_and_thread(self):
        mav = MagicMock()
        tak = TAKOutput(mav)
        assert tak.start()
        assert tak._thread is not None
        assert tak._thread.is_alive()
        tak.stop()

    def test_stop_joins_thread(self):
        mav = MagicMock()
        tak = TAKOutput(mav)
        tak.start()
        tak.stop()
        assert tak._thread is None
        assert tak._sock is None

    def test_get_status_returns_dict(self):
        mav = MagicMock()
        tak = TAKOutput(mav)
        status = tak.get_status()
        assert "enabled" in status
        assert "callsign" in status
        assert "events_sent" in status


class TestTAKOutputPush:
    def test_push_stores_data(self):
        mav = MagicMock()
        tak = TAKOutput(mav)
        tracks = _make_tracking(_make_track())
        tak.push(tracks, {"person"}, 1)
        with tak._data_lock:
            assert len(tak._latest_tracks) == 1
            assert tak._locked_track_id == 1


class TestTAKOutputSending:
    @patch("hydra_detect.tak.tak_output.socket.socket")
    def test_self_sa_sent_with_gps(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 100.0)
        mav.get_heading_deg.return_value = 90.0
        mav.get_telemetry.return_value = {"groundspeed": 5.0}

        tak = TAKOutput(mav, sa_interval=0.1)
        tak.start()
        time.sleep(1.0)
        tak.stop()

        assert mock_sock.sendto.call_count >= 1
        # Check that at least one call sent XML with self-SA type
        for call in mock_sock.sendto.call_args_list:
            data = call[0][0]
            if b"a-f-A-M-F-Q" in data:
                return
        pytest.fail("No self-SA event found in sendto calls")

    @patch("hydra_detect.tak.tak_output.socket.socket")
    def test_no_gps_no_send(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mav = MagicMock()
        mav.get_lat_lon.return_value = (None, None, None)

        tak = TAKOutput(mav, sa_interval=0.1)
        tak.start()
        time.sleep(0.8)
        tak.stop()

        mock_sock.sendto.assert_not_called()

    @patch("hydra_detect.tak.tak_output.socket.socket")
    def test_detection_sent_for_alert_class(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 50.0)
        mav.get_heading_deg.return_value = 180.0
        mav.get_telemetry.return_value = {"groundspeed": 0.0}
        mav.estimate_target_position.return_value = (34.001, -117.999)

        tak = TAKOutput(mav, emit_interval=0.1, sa_interval=100)
        tak.start()
        tak.push(_make_tracking(_make_track(label="person")), {"person"}, None)
        time.sleep(1.0)
        tak.stop()

        found_det = any(b"a-u-G-U-C-I" in c[0][0] for c in mock_sock.sendto.call_args_list)
        assert found_det, "No detection CoT event sent"

    @patch("hydra_detect.tak.tak_output.socket.socket")
    def test_alert_class_filter_respected(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 50.0)
        mav.get_heading_deg.return_value = 180.0
        mav.get_telemetry.return_value = {"groundspeed": 0.0}
        mav.estimate_target_position.return_value = (34.001, -117.999)

        tak = TAKOutput(mav, emit_interval=0.1, sa_interval=100)
        tak.start()
        # Push a "car" track but alert_classes only has "person"
        tak.push(_make_tracking(_make_track(label="car")), {"person"}, None)
        time.sleep(0.8)
        tak.stop()

        # Should NOT see vehicle detection CoT
        found_vehicle = any(b"a-u-G-E-V" in c[0][0] for c in mock_sock.sendto.call_args_list)
        assert not found_vehicle, "Vehicle detection sent despite not in alert_classes"

    @patch("hydra_detect.tak.tak_output.socket.socket")
    def test_unicast_targets_receive_data(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mav = MagicMock()
        mav.get_lat_lon.return_value = (34.0, -118.0, 100.0)
        mav.get_heading_deg.return_value = 90.0
        mav.get_telemetry.return_value = {"groundspeed": 0.0}

        tak = TAKOutput(
            mav, sa_interval=0.1,
            unicast_targets="10.0.0.1:4242, 10.0.0.2:6969",
        )
        tak.start()
        time.sleep(1.0)
        tak.stop()

        # Should have sent to multicast + 2 unicast for at least one SA event
        destinations = {c[0][1] for c in mock_sock.sendto.call_args_list}
        assert ("239.2.3.1", 6969) in destinations
        assert ("10.0.0.1", 4242) in destinations
        assert ("10.0.0.2", 6969) in destinations
