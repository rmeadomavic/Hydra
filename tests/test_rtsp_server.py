"""Unit tests for RTSP server — GStreamer is mocked for CI."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _mock_gi(monkeypatch):
    """Provide a fake gi module so rtsp_server can be imported."""
    mock_gi = MagicMock()
    mock_gi.require_version = MagicMock()

    mock_gst = MagicMock()
    mock_gst.init.return_value = None
    mock_gst.Buffer.new_wrapped.return_value = MagicMock()

    mock_rtsp = MagicMock()
    mock_server = MagicMock()
    mock_factory = MagicMock()
    mock_rtsp.RTSPServer.return_value = mock_server
    mock_rtsp.RTSPMediaFactory.return_value = mock_factory

    mock_glib = MagicMock()
    mock_loop = MagicMock()
    mock_glib.MainLoop.return_value = mock_loop

    mock_gi.repository.Gst = mock_gst
    mock_gi.repository.GstRtspServer = mock_rtsp
    mock_gi.repository.GLib = mock_glib

    monkeypatch.setitem(sys.modules, 'gi', mock_gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', mock_gi.repository)

    mod_name = 'hydra_detect.rtsp_server'
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    yield {
        'gi': mock_gi,
        'Gst': mock_gst,
        'GstRtspServer': mock_rtsp,
        'GLib': mock_glib,
        'server': mock_server,
        'factory': mock_factory,
        'loop': mock_loop,
    }

    if mod_name in sys.modules:
        del sys.modules[mod_name]


class TestRTSPServerLifecycle:
    def test_start_creates_server_on_port(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        result = srv.start()
        assert result is True
        assert srv.running is True
        _mock_gi['server'].set_service.assert_called_once_with("8554")

    def test_stop_quits_mainloop(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        srv.stop()
        assert srv.running is False
        _mock_gi['loop'].quit.assert_called_once()

    def test_push_frame_when_running(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        srv.push_frame(frame)

    def test_push_frame_noop_when_stopped(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        srv.push_frame(frame)

    def test_client_count_starts_at_zero(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        assert srv.client_count == 0

    def test_url_property(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        assert srv.url == "rtsp://0.0.0.0:8554/hydra"


class TestRTSPGracefulDegradation:
    def test_start_returns_false_when_gst_unavailable(self, _mock_gi):
        """If _GST_AVAILABLE is False, start() should return False."""
        from hydra_detect.rtsp_server import RTSPServer
        import hydra_detect.rtsp_server as mod
        original = mod._GST_AVAILABLE
        mod._GST_AVAILABLE = False
        try:
            srv = RTSPServer(port=8554, mount="/hydra")
            assert srv.start() is False
            assert srv.running is False
        finally:
            mod._GST_AVAILABLE = original


class TestRTSPClientTracking:
    def test_client_connected_increments_count(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        mock_client = MagicMock()
        srv._on_client_connected(None, mock_client)
        assert srv.client_count == 1
        mock_client.connect.assert_called_once_with("closed", srv._on_client_closed)

    def test_client_closed_decrements_count(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv.start()
        mock_client = MagicMock()
        srv._on_client_connected(None, mock_client)
        srv._on_client_closed(mock_client)
        assert srv.client_count == 0

    def test_client_count_never_negative(self, _mock_gi):
        from hydra_detect.rtsp_server import RTSPServer
        srv = RTSPServer(port=8554, mount="/hydra")
        srv._on_client_closed(MagicMock())
        assert srv.client_count == 0
