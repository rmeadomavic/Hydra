"""Unit tests for MAVLink video thumbnail sender."""

from __future__ import annotations

import math
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from hydra_detect.mavlink_video import MAVLinkVideoSender


@pytest.fixture
def mock_mavlink():
    """Mock MAVLinkIO with a mav object and send_lock."""
    mav_io = MagicMock()
    mav_io.connected = True
    mav_io.send_lock = threading.Lock()
    mav_io.mav = MagicMock()
    mav_io.mav.mav = MagicMock()
    return mav_io


class TestChunking:
    def test_chunk_count_for_5000_bytes(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        chunks = sender._chunk_jpeg(bytes(5000))
        assert len(chunks) == math.ceil(5000 / 253)

    def test_last_chunk_zero_padded(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        chunks = sender._chunk_jpeg(bytes(range(244)) * 2 + bytes(12))
        last = chunks[-1]
        assert len(last) == 253

    def test_handshake_fields(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120, jpeg_quality=20)
        jpeg_data = bytes(500)
        sender._send_frame(jpeg_data, 160, 120)
        mock_mavlink.mav.mav.data_transmission_handshake_send.assert_called_once_with(
            0, 500, 160, 120, math.ceil(500 / 253), 253, 20,
        )


class TestPushFrame:
    def test_push_frame_is_nonblocking(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        start = time.monotonic()
        sender.push_frame(frame)
        elapsed = time.monotonic() - start
        assert elapsed < 0.01

    def test_push_frame_noop_when_stopped(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sender.push_frame(frame)  # Should not raise


class TestLifecycle:
    def test_start_returns_false_without_mavlink(self):
        sender = MAVLinkVideoSender(None, width=160, height=120)
        assert sender.start() is False

    def test_start_returns_false_when_disconnected(self):
        mav_io = MagicMock()
        mav_io.connected = False
        sender = MAVLinkVideoSender(mav_io, width=160, height=120)
        assert sender.start() is False

    def test_start_stop_lifecycle(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        assert sender.start() is True
        assert sender.running is True
        sender.stop()
        assert sender.running is False

    def test_status_dict(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120, jpeg_quality=20)
        status = sender.get_status()
        assert status["width"] == 160
        assert status["height"] == 120
        assert status["quality"] == 20
        assert status["running"] is False


class TestStaleFrameDetection:
    def test_skips_stale_frame(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sender.push_frame(frame)
        assert sender._generation == 1


class TestAdaptiveRate:
    def test_large_jpeg_increases_interval(self, mock_mavlink):
        sender = MAVLinkVideoSender(
            mock_mavlink, width=160, height=120,
            max_fps=2.0, min_fps=0.2, link_budget_bytes_sec=8000,
        )
        interval = sender._compute_interval(8000)
        assert interval == 2.0

    def test_small_jpeg_decreases_interval(self, mock_mavlink):
        sender = MAVLinkVideoSender(
            mock_mavlink, width=160, height=120,
            max_fps=2.0, min_fps=0.2, link_budget_bytes_sec=8000,
        )
        interval = sender._compute_interval(1000)
        assert interval == 0.5  # clamped to 1/max_fps

    def test_interval_clamped_to_min_fps(self, mock_mavlink):
        sender = MAVLinkVideoSender(
            mock_mavlink, width=160, height=120,
            max_fps=2.0, min_fps=0.2, link_budget_bytes_sec=8000,
        )
        interval = sender._compute_interval(50000)
        assert interval == 5.0  # clamped to 1/min_fps


class TestSetParams:
    def test_rejects_out_of_range(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        assert sender.set_params(width=5000) is False
        assert sender.set_params(quality=100) is False
        assert sender.set_params(max_fps=10.0) is False

    def test_accepts_valid_params(self, mock_mavlink):
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        assert sender.set_params(width=80, height=60, quality=30) is True
        status = sender.get_status()
        assert status["width"] == 80
        assert status["height"] == 60
        assert status["quality"] == 30


class TestSendLock:
    def test_send_acquires_lock(self, mock_mavlink):
        """Verify _send_frame holds send_lock while transmitting."""
        sender = MAVLinkVideoSender(mock_mavlink, width=160, height=120)
        jpeg_data = bytes(500)

        # Use a MagicMock as the lock so __enter__/__exit__ are trackable.
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_mavlink.send_lock = mock_lock

        sender._send_frame(jpeg_data, 160, 120)

        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()
