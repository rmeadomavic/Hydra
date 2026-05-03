"""Camera resilience tests — issue #122 (no-camera startup + mid-session loss).

These tests cover the contract that the pipeline must not crash when:
  * a Jetson boots with no USB camera plugged in
  * a camera disconnects mid-session
  * cv2.VideoCapture.read() raises instead of returning ok=False

The fix lives in ``hydra_detect.camera.try_open_camera`` (single-shot helper),
``Camera._grab_loop`` (state-transition logging + cv2.error tolerance), and
``Pipeline._check_camera_frame`` (read-time exception isolation).
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import cv2  # noqa: F401  — patched below; ensure import works

from hydra_detect import camera as camera_mod
from hydra_detect.camera import Camera, try_open_camera


def _mk_cap(opened: bool, frames=None) -> MagicMock:
    """Build a fake cv2.VideoCapture handle.

    ``frames`` (optional) is a list of (ok, frame) tuples returned in
    order from .read(); after exhaustion the last entry repeats.
    """
    cap = MagicMock()
    cap.isOpened.return_value = opened
    if frames is not None:
        it = iter(frames)
        last = [(False, None)]

        def _read():
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]

        cap.read.side_effect = _read
    return cap


# ---------------------------------------------------------------------------
# try_open_camera — pure helper
# ---------------------------------------------------------------------------

class TestTryOpenCamera:
    def test_open_succeeds_first_try(self):
        opened = _mk_cap(opened=True)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=opened) as vc:
            cap, ok = try_open_camera(0)
        assert ok is True
        assert cap is opened
        # Single attempt only — no retry budget used.
        assert vc.call_count == 1

    def test_open_returns_none_on_failure_no_raise(self):
        unopened = _mk_cap(opened=False)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened):
            cap, ok = try_open_camera(0)
        assert ok is False
        assert cap is None
        # Failed handle must be released so we don't leak FDs.
        unopened.release.assert_called_once()

    def test_open_retries_until_success(self):
        """Patch VideoCapture to return unopened twice then opened — assert retry count + success."""
        bad = _mk_cap(opened=False)
        good = _mk_cap(opened=True)
        # 3 attempts: bad, bad, good
        sequence = [bad, bad, good]
        call_count = {"n": 0}

        def factory(*args, **kwargs):
            i = call_count["n"]
            call_count["n"] += 1
            return sequence[i]

        with patch("hydra_detect.camera.cv2.VideoCapture", side_effect=factory), \
                patch("hydra_detect.camera.time.sleep"):  # no real sleep
            cap, ok = try_open_camera(0, retries=3, interval=0.0)
        assert ok is True
        assert cap is good
        assert call_count["n"] == 3

    def test_open_exhausts_retry_budget(self):
        unopened = _mk_cap(opened=False)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                patch("hydra_detect.camera.time.sleep"):
            cap, ok = try_open_camera(0, retries=2, interval=0.0)
        assert ok is False
        assert cap is None

    def test_stop_event_short_circuits(self):
        """When stop_event is set, retry loop bails without waiting."""
        unopened = _mk_cap(opened=False)
        evt = threading.Event()
        evt.set()
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened):
            cap, ok = try_open_camera(0, retries=5, interval=99.0, stop_event=evt)
        assert ok is False
        assert cap is None

    def test_videocapture_exception_treated_as_failure(self):
        """If cv2.VideoCapture itself raises, treat as failure, don't propagate."""
        with patch("hydra_detect.camera.cv2.VideoCapture", side_effect=RuntimeError("boom")):
            cap, ok = try_open_camera("bad-source")
        assert ok is False
        assert cap is None

    def test_analog_uses_v4l2_backend(self):
        opened = _mk_cap(opened=True)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=opened) as vc:
            cap, ok = try_open_camera(0, api_preference=cv2.CAP_V4L2)
        assert ok is True
        # cv2.VideoCapture(source, api_preference)
        vc.assert_called_once_with(0, cv2.CAP_V4L2)


# ---------------------------------------------------------------------------
# Camera.open() — boot without a camera
# ---------------------------------------------------------------------------

class TestCameraOpenNoDevice:
    def test_open_logs_warn_only_on_first_failure(self, caplog):
        """One WARN at startup; subsequent grab-loop ticks must not re-spam."""
        unopened = _mk_cap(opened=False)
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                patch("hydra_detect.camera.threading.Thread"):
            cam = Camera(source=0)
            with caplog.at_level(logging.WARNING, logger="hydra_detect.camera"):
                cam.open()
        warn_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        # Exactly one WARN — the "No camera at <source>" line.
        assert len(warn_msgs) == 1
        assert "No camera" in warn_msgs[0].getMessage()
        assert cam.available is False

    def test_grab_loop_quiet_when_already_lost(self, caplog):
        """Once the warn has fired, repeated reconnect attempts log at DEBUG."""
        unopened = _mk_cap(opened=False)
        cam = Camera(source=0)
        cam._available = False  # simulate post-open() state
        cam._running = True
        cam._cap = None

        # Stop the loop after the first reconnect tick.
        ticks = {"n": 0}

        def fake_wait(timeout):
            ticks["n"] += 1
            if ticks["n"] >= 1:
                cam._running = False
            return False

        cam._stop_evt.wait = fake_wait  # type: ignore[method-assign]
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                caplog.at_level(logging.DEBUG, logger="hydra_detect.camera"):
            cam._grab_loop()

        warn_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        # The grab loop must not WARN while we're already in the lost state.
        assert warn_msgs == []

    def test_grab_loop_warns_on_state_transition(self, caplog):
        """If we WERE available and the device drops, log WARN exactly once."""
        unopened = _mk_cap(opened=False)
        cam = Camera(source=0)
        cam._available = True  # we just lost it
        cam._running = True
        cam._cap = unopened  # opened=False → triggers reconnect branch

        def fake_wait(timeout):
            cam._running = False
            return False

        cam._stop_evt.wait = fake_wait  # type: ignore[method-assign]
        with patch("hydra_detect.camera.cv2.VideoCapture", return_value=unopened), \
                caplog.at_level(logging.WARNING, logger="hydra_detect.camera"):
            cam._grab_loop()

        warn_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_msgs) == 1
        assert "disconnected" in warn_msgs[0].getMessage()
        assert cam.available is False


# ---------------------------------------------------------------------------
# Mid-session disconnect handling
# ---------------------------------------------------------------------------

_FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


class TestMidSessionDisconnect:
    def test_read_exception_does_not_crash_grab_loop(self):
        """cv2.error from cap.read() must not escape the grab thread."""
        bad_cap = MagicMock()
        bad_cap.isOpened.return_value = True
        bad_cap.read.side_effect = cv2.error("USB pulled")

        cam = Camera(source=0)
        cam._available = True
        cam._running = True
        cam._cap = bad_cap

        # Run one iteration: read raises, loop releases cap, then enters
        # the reconnect branch — bail out of stop_evt.wait so the test
        # is bounded.
        def fake_wait(timeout):
            cam._running = False
            return True  # signals "stopped"

        cam._stop_evt.wait = fake_wait  # type: ignore[method-assign]
        # Should not raise.
        cam._grab_loop()
        # Cap was released after the exception.
        bad_cap.release.assert_called()
        assert cam.available is False

    def test_mid_session_read_failure_recovers(self):
        """read() returns False, then True after a reconnect — pipeline doesn't crash."""
        bad_cap = _mk_cap(opened=True, frames=[(False, None)])
        good_cap = _mk_cap(opened=True, frames=[(True, _FRAME)])

        cam = Camera(source=0)
        cam._available = True
        cam._running = True
        cam._cap = bad_cap

        # Reconnect path returns the good_cap on the second VideoCapture call.
        vc_seq = [good_cap]

        def vc_factory(*args, **kwargs):
            return vc_seq.pop(0) if vc_seq else _mk_cap(opened=False)

        ticks = {"n": 0}

        def fake_wait(timeout):
            ticks["n"] += 1
            if ticks["n"] >= 1:
                # After we let the reconnect succeed, end the loop on
                # the next iteration's read.
                pass
            return False

        cam._stop_evt.wait = fake_wait  # type: ignore[method-assign]

        # Patch read on good_cap so after one good frame, _running flips
        # off and we don't loop forever.
        original_read = good_cap.read

        def wrapped_read():
            cam._running = False
            return (True, _FRAME)

        good_cap.read = wrapped_read

        with patch("hydra_detect.camera.cv2.VideoCapture", side_effect=vc_factory):
            cam._grab_loop()

        # The pipeline saw a frame come through after the disconnect.
        assert cam.has_frame is True
        assert cam.available is True


# ---------------------------------------------------------------------------
# Pipeline-side: _check_camera_frame must not crash on read exception
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_pipeline():
    """A minimal Pipeline with the camera-loss attributes wired."""
    from hydra_detect.pipeline.facade import Pipeline

    with patch.object(Pipeline, "__init__", lambda self, *a, **kw: None):
        p = Pipeline.__new__(Pipeline)

    p._camera = MagicMock()
    p._mavlink = None
    p._autonomous = None
    p._event_logger = MagicMock()
    p._cam_fail_count = 0
    p._cam_lost = False
    p._CAM_FAIL_THRESHOLD = 2
    p._callsign = "HYDRA-TEST"
    import time as _time
    p._last_frame_time = _time.monotonic()
    return p


class TestPipelineCheckFrameResilience:
    def test_check_frame_swallows_cv2_error(self, fake_pipeline):
        """cv2.error from camera.read() must be treated as no-frame, not raise."""
        fake_pipeline._camera.read.side_effect = cv2.error("device gone")
        result = fake_pipeline._check_camera_frame()
        assert result is None
        assert fake_pipeline._cam_fail_count == 1

    def test_check_frame_swallows_runtime_error(self, fake_pipeline):
        fake_pipeline._camera.read.side_effect = RuntimeError("buffer error")
        result = fake_pipeline._check_camera_frame()
        assert result is None

    def test_check_frame_swallows_oserror(self, fake_pipeline):
        fake_pipeline._camera.read.side_effect = OSError("bad fd")
        result = fake_pipeline._check_camera_frame()
        assert result is None


# ---------------------------------------------------------------------------
# Capability status: camera_ok=False surfaces #122-style block reason
# ---------------------------------------------------------------------------

class TestCapabilityCameraMissing:
    def test_camera_missing_blocks_detection_with_122_message(self):
        from hydra_detect.capability_status import (
            CapabilityStatus, SystemState, evaluate_all,
        )
        state = SystemState(camera_ok=False, camera_frame_age_sec=None)
        reports = {r.name: r for r in evaluate_all(state)}
        det = reports["Detection"]
        assert det.status == CapabilityStatus.BLOCKED
        # Message should point an operator at the right fix.
        joined = " ".join(det.reasons).lower()
        assert "camera not detected" in joined or "plug in" in joined
        assert det.fix_target == "#122"

    def test_camera_disconnect_blocks_detection_distinct_message(self):
        """Once we've seen frames, camera_ok=False means it disconnected, not missing."""
        from hydra_detect.capability_status import (
            CapabilityStatus, SystemState, evaluate_all,
        )
        state = SystemState(camera_ok=False, camera_frame_age_sec=12.5)
        reports = {r.name: r for r in evaluate_all(state)}
        det = reports["Detection"]
        assert det.status == CapabilityStatus.BLOCKED
        joined = " ".join(det.reasons).lower()
        assert "disconnect" in joined
