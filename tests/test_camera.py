"""Tests for camera device detection and classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hydra_detect.camera import Camera, _classify_device, _is_capture_device


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


class TestOpenRetries:
    """Camera.open() starts a reconnect loop when device is absent (issue #122)."""

    def _make_cap(self, opened: bool) -> MagicMock:
        cap = MagicMock()
        cap.isOpened.return_value = opened
        return cap

    def test_open_absent_returns_true(self):
        """open() must not fail hard when device isn't plugged in yet."""
        unopened = self._make_cap(opened=False)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                patch("hydra_detect.camera.threading.Thread") as thread_cls:
            cam = Camera(source=0, width=640, height=480, fps=30)
            assert cam.open() is True
            # Grab thread must be started so it can reconnect in the background.
            thread_cls.assert_called_once()
            thread_cls.return_value.start.assert_called_once()

    def test_open_absent_releases_cap(self):
        """Unopened cv2.VideoCapture must be released to avoid leaking FDs."""
        unopened = self._make_cap(opened=False)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                patch("hydra_detect.camera.threading.Thread"):
            cam = Camera(source=0)
            cam.open()
            unopened.release.assert_called_once()

    def test_open_absent_marks_running(self):
        """open() must set _running=True so close() can stop the grab thread cleanly."""
        unopened = self._make_cap(opened=False)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                patch("hydra_detect.camera.threading.Thread"):
            cam = Camera(source=0)
            cam.open()
            assert cam._running is True

    def test_open_absent_has_no_frame(self):
        """has_frame must be False until the grab thread actually reads something."""
        unopened = self._make_cap(opened=False)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                patch("hydra_detect.camera.threading.Thread"):
            cam = Camera(source=0)
            cam.open()
            assert cam.has_frame is False
            assert cam.read() is None

    def test_open_success_unchanged(self):
        """Successful open still returns True and starts the grab thread."""
        opened = self._make_cap(opened=True)
        opened.get.return_value = 0
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=opened), \
                patch("hydra_detect.camera.threading.Thread") as thread_cls:
            cam = Camera(source=0, width=640, height=480, fps=30)
            assert cam.open() is True
            thread_cls.return_value.start.assert_called_once()


class TestGrabLoopInterruptible:
    """close() must wake the grab thread even mid-backoff (issue #122 follow-up)."""

    def test_close_signals_stop_event(self):
        cam = Camera(source=0)
        cam._thread = MagicMock()
        cam._cap = None
        assert cam._stop_evt.is_set() is False
        cam.close()
        assert cam._stop_evt.is_set() is True
        cam._thread.join.assert_called_once()

    def test_grab_loop_exits_when_stop_event_set(self):
        """When _stop_evt fires during backoff, the loop must exit promptly."""
        cam = Camera(source=0)
        cam._running = True
        cam._cap = None  # forces the reconnect branch
        cam._stop_evt.set()  # pre-signalled so Event.wait returns True immediately

        with patch("hydra_detect.camera.cv2.VideoCapture"):
            # Should not raise and should return quickly without consuming
            # the full backoff window.
            cam._grab_loop()

    def test_reconnect_reapplies_resolution(self):
        """After a reconnect, width/height/fps must be re-applied — otherwise
        the camera runs at driver default after every disconnect."""
        good = MagicMock()
        good.isOpened.return_value = True

        cam = Camera(source=0, width=1280, height=720, fps=15)
        cam._running = True
        cam._cap = None  # trigger reconnect branch

        def fake_wait(timeout):
            # After the first backoff, stop the loop so the test is bounded.
            cam._running = False
            return False

        cam._stop_evt.wait = fake_wait  # type: ignore[method-assign]
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=good):
            cam._grab_loop()

        # Width/height/fps must have been applied to the reconnected cap.
        applied = {call.args[0]: call.args[1] for call in good.set.call_args_list}
        assert 1280 in applied.values()
        assert 720 in applied.values()
        assert 15 in applied.values()
