"""Tests for camera device detection and classification."""

from __future__ import annotations

from unittest.mock import patch

from hydra_detect.camera import _classify_device, _is_capture_device


def _mock_name(name: str):
    """Return a patcher that makes _get_device_name return *name*."""
    return patch("hydra_detect.camera._get_device_name", return_value=name)


class TestClassifyDevice:
    """Tests for _classify_device — V4L2 device name classification."""

    def test_logitech_c270(self):
        with _mock_name("UVC Camera (046d:0825)  C270"):
            assert _classify_device(0) == "webcam"

    def test_logitech_c920(self):
        with _mock_name("HD Pro Webcam C920"):
            assert _classify_device(0) == "webcam"

    def test_logitech_brio(self):
        with _mock_name("Logitech BRIO"):
            assert _classify_device(0) == "webcam"

    def test_generic_webcam(self):
        with _mock_name("USB2.0 PC CAMERA"):
            assert _classify_device(0) == "webcam"

    def test_lifecam(self):
        with _mock_name("Microsoft LifeCam HD-3000"):
            assert _classify_device(0) == "webcam"

    # -- Capture cards (CVBS/HDMI dongles for HDZero, etc.) --

    def test_usb_video_capture_card(self):
        with _mock_name("USB Video"):
            assert _classify_device(0) == "capture"

    def test_av_to_usb(self):
        with _mock_name("AV TO USB2.0"):
            assert _classify_device(0) == "capture"

    def test_hdmi_to_usb(self):
        with _mock_name("HDMI to USB dongle"):
            assert _classify_device(0) == "capture"

    def test_macrosilicon(self):
        with _mock_name("Macrosilicon USB Video"):
            assert _classify_device(0) == "capture"

    def test_easycap(self):
        with _mock_name("EasyCap DC60"):
            assert _classify_device(0) == "capture"

    def test_elgato(self):
        with _mock_name("Elgato Cam Link 4K"):
            assert _classify_device(0) == "capture"

    def test_generic_capture(self):
        with _mock_name("Video Capture Device"):
            assert _classify_device(0) == "capture"

    def test_uvc_capture(self):
        with _mock_name("UVC Capture"):
            assert _classify_device(0) == "capture"

    # -- Rejected devices (metadata, output, codec nodes) --

    def test_metadata_node(self):
        with _mock_name("UVC Camera Metadata"):
            assert _classify_device(0) == "reject"

    def test_output_device(self):
        with _mock_name("HDMI Output"):
            assert _classify_device(0) == "reject"

    def test_codec_device(self):
        with _mock_name("tegra-codec"):
            assert _classify_device(0) == "reject"

    def test_encoder_device(self):
        with _mock_name("nvenc encoder"):
            assert _classify_device(0) == "reject"

    def test_decoder_device(self):
        with _mock_name("nvdec decoder"):
            assert _classify_device(0) == "reject"

    # -- Unknown devices --

    def test_unknown_device(self):
        with _mock_name("Some Random Device"):
            assert _classify_device(0) == "unknown"

    def test_fallback_name(self):
        with _mock_name("Video 0"):
            assert _classify_device(0) == "unknown"


class TestIsCaptureDevice:
    """Tests for _is_capture_device — should accept webcams and capture cards."""

    def test_webcam_accepted(self):
        with _mock_name("HD Pro Webcam C920"):
            assert _is_capture_device(0) is True

    def test_capture_card_accepted(self):
        with _mock_name("USB Video"):
            assert _is_capture_device(0) is True

    def test_metadata_rejected(self):
        with _mock_name("UVC Camera Metadata"):
            assert _is_capture_device(0) is False

    def test_unknown_rejected(self):
        with _mock_name("Some Random Device"):
            assert _is_capture_device(0) is False
