"""Concurrency stress tests for shared-state components.

Marked ``@pytest.mark.slow`` so they're deselected by default (``make test``).
Run explicitly with ``pytest tests/test_concurrency_stress.py``.

These tests exercise thread-safety invariants rather than specific interleavings.
Contention patterns differ between dev laptops and CI's 2-core GitHub runners;
we assert what MUST hold (consistency, bounded wall-clock) rather than precise
ordering.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from hydra_detect.approach import ApproachConfig, ApproachController, ApproachMode
from hydra_detect.mavlink_io import MAVLinkIO


pytestmark = pytest.mark.slow


def _make_mavlink():
    mav = MagicMock()
    mav.get_vehicle_mode.return_value = "AUTO"
    mav.get_lat_lon.return_value = (34.05, -118.25, 50.0)
    mav.estimate_target_position.return_value = (34.051, -118.251)
    mav.command_guided_to.return_value = True
    mav.send_velocity_ned.return_value = True
    mav.command_do_change_speed.return_value = True
    mav.set_mode.return_value = True
    mav.get_rc_channels.return_value = [1500] * 16
    return mav


def _make_controller():
    cfg = ApproachConfig(waypoint_interval=0.0)
    return ApproachController(_make_mavlink(), cfg)


class TestApproachControllerStress:
    def test_concurrent_start_abort_leaves_consistent_state(self):
        """8 threads × N iterations of start_follow + abort — controller must
        never end in a mode other than IDLE after a final abort, and the
        final target_track_id must agree with the final mode."""
        ctrl = _make_controller()

        errors: list[Exception] = []
        stop = threading.Event()

        def hammer_start_abort():
            try:
                while not stop.is_set():
                    ctrl.start_follow(1)
                    ctrl.abort()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammer_start_abort) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        stop.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "thread failed to join — possible deadlock"

        # Final explicit abort
        ctrl.abort()
        assert ctrl.mode == ApproachMode.IDLE
        assert ctrl.target_track_id is None
        assert not errors, f"unhandled exceptions: {errors}"

    def test_competing_mode_starts_only_one_wins(self):
        """Two threads racing to start different modes — exactly one must succeed
        (since the controller serializes via ``_lock`` inside start_*)."""
        ctrl = _make_controller()
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def try_follow():
            barrier.wait()
            results.append(ctrl.start_follow(1))

        def try_drop():
            barrier.wait()
            results.append(ctrl.start_drop(2, 34.0, -118.0))

        t1 = threading.Thread(target=try_follow)
        t2 = threading.Thread(target=try_drop)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        # Exactly one True, one False
        assert results.count(True) == 1
        assert results.count(False) == 1
        assert ctrl.mode in (ApproachMode.FOLLOW, ApproachMode.DROP)


class TestMavlinkSendLockStress:
    def test_concurrent_send_raw_message_serializes(self):
        """4 threads sending raw messages in a tight loop.  The send lock in
        ``send_raw_message`` must serialize calls to the underlying transport
        so we never get concurrent send() invocations (which would corrupt
        pymavlink's encoder state)."""
        mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
        mav._mav = MagicMock()
        mav._mav.mav = MagicMock()

        # Instrument send() to detect overlapping calls
        in_flight = [0]
        max_in_flight = [0]
        instrumentation_lock = threading.Lock()

        def instrumented_send(*args, **kw):
            with instrumentation_lock:
                in_flight[0] += 1
                max_in_flight[0] = max(max_in_flight[0], in_flight[0])
            time.sleep(0.0005)  # force overlap window
            with instrumentation_lock:
                in_flight[0] -= 1

        mav._mav.mav.send.side_effect = instrumented_send

        errors: list[Exception] = []
        stop = threading.Event()

        def hammer():
            try:
                while not stop.is_set():
                    mav.send_raw_message(MagicMock())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        stop.set()
        for t in threads:
            t.join(timeout=5.0)

        assert max_in_flight[0] == 1, (
            f"send() was called concurrently (max_in_flight={max_in_flight[0]}); "
            "send lock is not serializing"
        )
        assert not errors


class TestApproachAbortRaceRegression:
    @pytest.mark.regression
    def test_abort_while_update_in_flight_does_not_deadlock(self):
        """update() acquires the lock briefly to read mode, releases it, then
        dispatches. abort() running in parallel must not deadlock against update()."""
        ctrl = _make_controller()
        ctrl.start_follow(1)

        errors: list[Exception] = []
        stop = threading.Event()

        def hammer_update():
            track = MagicMock()
            track.x1, track.y1, track.x2, track.y2 = 100, 100, 200, 200
            track.track_id = 1
            try:
                while not stop.is_set():
                    ctrl.update(track, 640, 480)
            except Exception as e:
                errors.append(e)

        def hammer_abort():
            try:
                while not stop.is_set():
                    ctrl.abort()
                    ctrl.start_follow(1)
            except Exception as e:
                errors.append(e)

        t_update = threading.Thread(target=hammer_update)
        t_abort = threading.Thread(target=hammer_abort)
        t_update.start()
        t_abort.start()
        time.sleep(0.2)
        stop.set()

        # Both threads must exit within the join window — otherwise deadlock
        t_update.join(timeout=5.0)
        t_abort.join(timeout=5.0)
        assert not t_update.is_alive()
        assert not t_abort.is_alive()
        assert not errors
