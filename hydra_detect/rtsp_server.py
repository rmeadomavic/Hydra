"""GStreamer RTSP server — publishes annotated detection frames as H.264 stream."""

from __future__ import annotations

import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

_GST_AVAILABLE = False
try:
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstRtspServer', '1.0')
    from gi.repository import GLib, Gst, GstRtspServer
    Gst.init(None)
    _GST_AVAILABLE = True
except (ImportError, ValueError):
    logger.info("GStreamer not available — RTSP output disabled.")


class RTSPServer:
    """Publish annotated frames as an RTSP H.264 stream."""

    def __init__(
        self,
        port: int = 8554,
        mount: str = "/hydra",
        bitrate: int = 2_000_000,
        width: int = 640,
        height: int = 480,
    ):
        self._port = port
        self._mount = mount if mount.startswith("/") else f"/{mount}"
        self._bitrate = bitrate
        self._width = width
        self._height = height

        self._server = None
        self._loop = None
        self._thread: threading.Thread | None = None
        self._appsrc = None
        self._running = False
        self._client_count = 0
        self._client_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def client_count(self) -> int:
        with self._client_lock:
            return self._client_count

    @property
    def url(self) -> str:
        return f"rtsp://0.0.0.0:{self._port}{self._mount}"

    def start(self) -> bool:
        if not _GST_AVAILABLE:
            logger.warning("GStreamer not available — cannot start RTSP server.")
            return False

        if self._running:
            return True

        try:
            self._server = GstRtspServer.RTSPServer()
            self._server.set_service(str(self._port))

            factory = GstRtspServer.RTSPMediaFactory()
            factory.set_launch(self._build_pipeline_string())
            factory.set_shared(True)
            factory.connect("media-configure", self._on_media_configure)

            mounts = self._server.get_mount_points()
            mounts.add_factory(self._mount, factory)

            self._server.connect("client-connected", self._on_client_connected)
            self._server.attach(None)

            self._loop = GLib.MainLoop()
            self._thread = threading.Thread(
                target=self._loop.run, daemon=True, name="hydra-rtsp",
            )
            self._thread.start()
            self._running = True
            logger.info("RTSP server started: %s", self.url)
            return True

        except Exception as exc:
            logger.error("Failed to start RTSP server: %s", exc)
            self._running = False
            return False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._appsrc = None
        if self._loop is not None:
            self._loop.quit()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._server = None
        logger.info("RTSP server stopped.")

    def push_frame(self, frame: np.ndarray) -> None:
        if not self._running or self._appsrc is None:
            return
        try:
            data = frame.tobytes()
            buf = Gst.Buffer.new_wrapped(data)
            self._appsrc.emit("push-buffer", buf)
        except Exception as exc:
            if not hasattr(self, '_push_err_count'):
                self._push_err_count = 0
            self._push_err_count += 1
            if self._push_err_count == 1 or self._push_err_count % 100 == 0:
                logger.warning("RTSP push_frame error (#%d): %s", self._push_err_count, exc)

    def _build_pipeline_string(self) -> str:
        caps = (
            f"video/x-raw,format=BGR,width={self._width},"
            f"height={self._height},framerate=0/1"
        )
        hw_enc = (
            f"( appsrc name=source is-live=true format=time caps={caps} ! "
            f"videoconvert ! video/x-raw,format=I420 ! "
            f"nvv4l2h264enc bitrate={self._bitrate} ! "
            f"h264parse ! rtph264pay name=pay0 pt=96 )"
        )
        if self._check_encoder("nvv4l2h264enc"):
            logger.info("RTSP using hardware encoder: nvv4l2h264enc")
            return hw_enc

        logger.info("RTSP using software encoder: x264enc")
        sw_enc = (
            f"( appsrc name=source is-live=true format=time caps={caps} ! "
            f"videoconvert ! video/x-raw,format=I420 ! "
            f"x264enc tune=zerolatency speed-preset=ultrafast "
            f"bitrate={self._bitrate // 1000} ! "
            f"h264parse ! rtph264pay name=pay0 pt=96 )"
        )
        return sw_enc

    @staticmethod
    def _check_encoder(name: str) -> bool:
        if not _GST_AVAILABLE:
            return False
        try:
            factory = Gst.ElementFactory.find(name)
            return factory is not None
        except Exception:
            return False

    def _on_media_configure(self, factory, media) -> None:
        element = media.get_element()
        self._appsrc = element.get_child_by_name("source")

    def _on_client_connected(self, server, client) -> None:
        with self._client_lock:
            self._client_count += 1
        logger.info("RTSP client connected (total: %d)", self.client_count)
        client.connect("closed", self._on_client_closed)

    def _on_client_closed(self, client) -> None:
        with self._client_lock:
            self._client_count = max(0, self._client_count - 1)
        logger.info("RTSP client disconnected (total: %d)", self.client_count)
