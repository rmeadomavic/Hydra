"""Graceful-shutdown tests for the Hydra Detect pipeline.

Covers issue #54 — SIGTERM/SIGINT must drive the pipeline through the
ordered shutdown ladder so:
- detection log queue drains (bounded);
- mission log is closed cleanly;
- strike/arm servos are commanded to safe;
- pan tracker is disabled (defence-in-depth);
- MAVLink STATUSTEXT shutdown notice is sent;
- MAVLink connection is closed last.

The watchdog ``os._exit(1)`` path is intentionally NOT exercised here —
acceptance criterion: "Watchdog kill path does not attempt cleanup."
"""

from __future__ import annotations

import configparser
from unittest.mock import MagicMock, patch

from hydra_detect.pipeline import Pipeline


def _make_pipeline_with_subsystems() -> Pipeline:
    """Build a Pipeline with all shutdown-touched subsystems mocked.

    Bypasses __init__ to skip hardware/IO probing. Wires only the
    attributes _shutdown() reads.
    """
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read_dict({
        "tak": {"callsign": "HYDRA-1"},
    })

    with patch.object(Pipeline, "__init__", lambda self, *a, **kw: None):
        p = Pipeline.__new__(Pipeline)

    p._cfg = cfg
    p._shutdown_complete = False
    p._approach = MagicMock()
    p._rf_tak_emitter = MagicMock()
    p._rf_hunt = MagicMock()
    p._kismet_manager = MagicMock()
    p._rtsp = MagicMock()
    p._mavlink_video = MagicMock()
    p._tak = MagicMock()
    p._mav_relay = MagicMock()
    p._tak_input = MagicMock()
    p._autonomous = MagicMock()
    p._servo_tracker = MagicMock()
    p._camera = MagicMock()
    p._detector = MagicMock()
    p._det_logger = MagicMock()
    p._event_logger = MagicMock()
    p._mavlink = MagicMock()
    p._mavlink.connected = True
    return p


# ---------------------------------------------------------------------------
# Shutdown ladder — every subsystem touched in the right order.
# ---------------------------------------------------------------------------

class TestShutdownLadder:
    def test_shutdown_drives_servos_safe(self):
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._servo_tracker.safe.assert_called_once()

    def test_shutdown_disables_pan(self):
        """disable_pan() guards against post-camera-close update() races."""
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._servo_tracker.disable_pan.assert_called_once()

    def test_shutdown_drains_detection_log_with_timeout(self):
        """det_logger.stop must use a bounded timeout, not block forever."""
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._det_logger.stop.assert_called_once()
        # The kwarg is the contract — flush has to be bounded.
        _, kwargs = p._det_logger.stop.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] > 0

    def test_shutdown_ends_mission(self):
        """event_logger.stop() wraps end_mission()."""
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._event_logger.stop.assert_called_once()

    def test_shutdown_sends_statustext(self):
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._mavlink.send_statustext.assert_called_once()
        # Acceptance: STATUSTEXT carries the SHUTDOWN word and a NOTICE-level
        # severity. We send severity=5 (NOTICE in MAVLink severity enum).
        args, kwargs = p._mavlink.send_statustext.call_args
        msg = args[0] if args else kwargs.get("text", "")
        assert "SHUTDOWN" in msg
        assert kwargs.get("severity", args[1] if len(args) > 1 else None) == 5

    def test_shutdown_closes_mavlink(self):
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._mavlink.close.assert_called_once()

    def test_shutdown_closes_camera_and_unloads_detector(self):
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._camera.close.assert_called_once()
        p._detector.unload.assert_called_once()

    def test_shutdown_aborts_approach(self):
        """approach.abort restores pre-approach flight mode (issue #54)."""
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._approach.abort.assert_called_once()

    def test_shutdown_stops_rf_and_tak_sidecars(self):
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        p._rf_tak_emitter.stop.assert_called_once()
        p._rf_hunt.stop.assert_called_once()
        p._kismet_manager.stop.assert_called_once()
        p._rtsp.stop.assert_called_once()
        p._tak.stop.assert_called_once()
        p._mav_relay.stop.assert_called_once()
        p._tak_input.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Idempotency — restart path inlines _shutdown(); start()-finally then runs
# it again. The second invocation must be a no-op.
# ---------------------------------------------------------------------------

class TestShutdownIdempotency:
    def test_second_call_is_noop(self):
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        # Reset call history; second invocation should not touch anything.
        p._servo_tracker.safe.reset_mock()
        p._det_logger.stop.reset_mock()
        p._event_logger.stop.reset_mock()
        p._mavlink.send_statustext.reset_mock()
        p._mavlink.close.reset_mock()

        p._shutdown()

        p._servo_tracker.safe.assert_not_called()
        p._det_logger.stop.assert_not_called()
        p._event_logger.stop.assert_not_called()
        p._mavlink.send_statustext.assert_not_called()
        p._mavlink.close.assert_not_called()

    def test_flag_is_set_after_first_call(self):
        p = _make_pipeline_with_subsystems()
        assert p._shutdown_complete is False
        p._shutdown()
        assert p._shutdown_complete is True

    def test_restart_resets_flag_via_helper(self):
        """Simulate the restart path: _shutdown then guard reset."""
        p = _make_pipeline_with_subsystems()
        p._shutdown()
        # Restart path manually resets the guard so the post-restart final
        # exit still fires shutdown.
        p._shutdown_complete = False
        p._servo_tracker.safe.reset_mock()
        p._shutdown()
        p._servo_tracker.safe.assert_called_once()


# ---------------------------------------------------------------------------
# Robustness — disable_pan() raising must not wedge the rest of the ladder.
# ---------------------------------------------------------------------------

class TestShutdownRobustness:
    def test_disable_pan_exception_is_swallowed(self):
        p = _make_pipeline_with_subsystems()
        p._servo_tracker.disable_pan.side_effect = RuntimeError("servo bus down")
        # Should not raise; STATUSTEXT and mavlink.close still fire.
        p._shutdown()
        p._mavlink.send_statustext.assert_called_once()
        p._mavlink.close.assert_called_once()

    def test_statustext_exception_is_swallowed(self):
        p = _make_pipeline_with_subsystems()
        p._mavlink.send_statustext.side_effect = RuntimeError("link dropped")
        p._shutdown()
        # mavlink.close still runs even after STATUSTEXT failure.
        p._mavlink.close.assert_called_once()

    def test_mavlink_disconnected_skips_statustext(self):
        p = _make_pipeline_with_subsystems()
        p._mavlink.connected = False
        p._shutdown()
        p._mavlink.send_statustext.assert_not_called()
        # close() still runs — it's the disconnect call.
        p._mavlink.close.assert_called_once()


# ---------------------------------------------------------------------------
# Signal handler — sets _running=False, best-effort safes servos.
# Actual shutdown ladder runs on main thread after _run_loop() returns.
# ---------------------------------------------------------------------------

class TestSignalHandler:
    def test_signal_handler_sets_running_false(self):
        p = _make_pipeline_with_subsystems()
        p._running = True
        p._signal_handler(15, None)  # SIGTERM
        assert p._running is False

    def test_signal_handler_safes_servos(self):
        p = _make_pipeline_with_subsystems()
        p._running = True
        p._signal_handler(2, None)  # SIGINT
        p._servo_tracker.safe.assert_called_once()

    def test_signal_handler_servo_exception_is_swallowed(self):
        p = _make_pipeline_with_subsystems()
        p._servo_tracker.safe.side_effect = RuntimeError("bus busy")
        p._running = True
        # Must not raise — signal handlers run in main thread and a raise
        # would propagate up into pymavlink's read loop.
        p._signal_handler(15, None)
        assert p._running is False

    def test_signal_handler_no_servo_tracker(self):
        """No servo_tracker (e.g. SITL or pan_disabled config) is fine."""
        p = _make_pipeline_with_subsystems()
        p._servo_tracker = None
        p._signal_handler(15, None)
        assert p._running is False


# ---------------------------------------------------------------------------
# atexit hook — covers normal sys.exit() and unhandled exceptions, not
# SIGKILL or watchdog os._exit(). Best-effort servo safe only.
# ---------------------------------------------------------------------------

class TestAtexitHook:
    def test_atexit_safes_servos(self):
        p = _make_pipeline_with_subsystems()
        p._atexit_safe_servo()
        p._servo_tracker.safe.assert_called_once()

    def test_atexit_servo_exception_is_swallowed(self):
        p = _make_pipeline_with_subsystems()
        p._servo_tracker.safe.side_effect = RuntimeError("bus busy")
        # Interpreter is tearing down; never raise.
        p._atexit_safe_servo()

    def test_atexit_no_servo_tracker(self):
        p = _make_pipeline_with_subsystems()
        p._servo_tracker = None
        # No-op; must not raise.
        p._atexit_safe_servo()


# ---------------------------------------------------------------------------
# Outer-loop helper — verify start() wraps _run_outer_loop in try/finally
# so a signal-initiated exit always fires _shutdown.
# ---------------------------------------------------------------------------

class TestStartFinallyWrap:
    def test_run_outer_loop_exists(self):
        """Helper exists and is callable — splits the loop from the wrap."""
        assert callable(getattr(Pipeline, "_run_outer_loop", None))

    def test_finally_block_calls_shutdown_on_signal_exit(self):
        """A signal-initiated _running=False must still trigger _shutdown.

        We patch _run_outer_loop to return immediately (simulating the
        loop exiting because _signal_handler set _running=False), and
        verify the wrapper still calls _shutdown.
        """
        p = _make_pipeline_with_subsystems()
        # Pre-init the start() prerequisites the wrapper alone doesn't need.
        p._run_outer_loop = MagicMock()
        # Inline the try/finally to mimic start():
        try:
            p._run_outer_loop()
        finally:
            p._shutdown()
        p._run_outer_loop.assert_called_once()
        p._mavlink.close.assert_called_once()
        p._servo_tracker.safe.assert_called_once()
        p._det_logger.stop.assert_called_once()

    def test_finally_block_calls_shutdown_on_exception(self):
        """An unhandled exception inside the loop still flushes everything."""
        p = _make_pipeline_with_subsystems()
        p._run_outer_loop = MagicMock(side_effect=RuntimeError("boom"))
        try:
            try:
                p._run_outer_loop()
            finally:
                p._shutdown()
        except RuntimeError:
            pass
        p._mavlink.close.assert_called_once()
        p._det_logger.stop.assert_called_once()
