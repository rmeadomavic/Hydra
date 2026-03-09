"""FastAPI web server — MJPEG stream, operator dashboard, runtime config, and REST API."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Hydra Detect v2.0", version="2.0.0")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


class StreamState:
    """Shared state between the pipeline and the web server."""

    def __init__(self):
        self.frame: Optional[np.ndarray] = None
        self.stats: Dict[str, Any] = {
            "fps": 0.0,
            "inference_ms": 0.0,
            "active_tracks": 0,
            "total_detections": 0,
            "detector": "n/a",
            "mavlink": False,
            "gps_fix": 0,
            "position": None,
        }
        self._lock = threading.Lock()

        # Runtime config callbacks (set by pipeline)
        self._on_prompts_change: Optional[Callable] = None
        self._on_threshold_change: Optional[Callable] = None
        self._on_loiter_command: Optional[Callable] = None
        self._get_recent_detections: Optional[Callable] = None

        # Current runtime config (readable by web UI)
        self.runtime_config: Dict[str, Any] = {
            "prompts": [],
            "threshold": 0.25,
            "auto_loiter": False,
        }

    def update_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self.frame = frame

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self.frame.copy() if self.frame is not None else None

    def update_stats(self, **kwargs: Any) -> None:
        with self._lock:
            self.stats.update(kwargs)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.stats)

    def set_callbacks(
        self,
        on_prompts_change: Optional[Callable] = None,
        on_threshold_change: Optional[Callable] = None,
        on_loiter_command: Optional[Callable] = None,
        get_recent_detections: Optional[Callable] = None,
    ) -> None:
        self._on_prompts_change = on_prompts_change
        self._on_threshold_change = on_threshold_change
        self._on_loiter_command = on_loiter_command
        self._get_recent_detections = get_recent_detections


# Global state instance — set by the pipeline before starting the server
stream_state = StreamState()


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the operator dashboard."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/stats")
async def api_stats():
    """Return current pipeline statistics as JSON."""
    return stream_state.get_stats()


@app.get("/api/config")
async def api_get_config():
    """Return current runtime configuration."""
    return stream_state.runtime_config


@app.post("/api/config/prompts")
async def api_set_prompts(request: Request):
    """Update detection prompts at runtime (NanoOWL)."""
    body = await request.json()
    prompts = body.get("prompts", [])
    if not prompts or not isinstance(prompts, list):
        return JSONResponse({"error": "prompts must be a non-empty list"}, status_code=400)

    if stream_state._on_prompts_change:
        stream_state._on_prompts_change(prompts)
        stream_state.runtime_config["prompts"] = prompts
        logger.info("Prompts updated via web UI: %s", prompts)
        return {"status": "ok", "prompts": prompts}
    return JSONResponse({"error": "prompt change not supported for current detector"}, status_code=400)


@app.post("/api/config/threshold")
async def api_set_threshold(request: Request):
    """Update detection confidence threshold at runtime."""
    body = await request.json()
    threshold = body.get("threshold")
    if threshold is None or not (0.0 <= float(threshold) <= 1.0):
        return JSONResponse({"error": "threshold must be 0.0-1.0"}, status_code=400)

    threshold = float(threshold)
    if stream_state._on_threshold_change:
        stream_state._on_threshold_change(threshold)
        stream_state.runtime_config["threshold"] = threshold
        logger.info("Threshold updated via web UI: %.2f", threshold)
        return {"status": "ok", "threshold": threshold}
    return JSONResponse({"error": "threshold change not available"}, status_code=400)


@app.post("/api/vehicle/loiter")
async def api_command_loiter():
    """Command vehicle to LOITER/HOLD at current position."""
    if stream_state._on_loiter_command:
        stream_state._on_loiter_command()
        return {"status": "ok", "command": "loiter"}
    return JSONResponse({"error": "MAVLink not connected"}, status_code=503)


@app.get("/api/detections")
async def api_recent_detections():
    """Return recent detection log entries."""
    if stream_state._get_recent_detections:
        return stream_state._get_recent_detections()
    return []


@app.get("/stream.mjpeg")
async def mjpeg_stream():
    """MJPEG video stream endpoint."""
    return StreamingResponse(
        _generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


async def _generate_mjpeg():
    """Async generator that yields JPEG frames."""
    quality = 70
    while True:
        frame = stream_state.get_frame()
        if frame is not None:
            ok, buf = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality]
            )
            if ok:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + buf.tobytes()
                    + b"\r\n"
                )
        await asyncio.sleep(0.033)  # ~30 fps cap


# ── Server launcher ──────────────────────────────────────────────────

def run_server(host: str = "0.0.0.0", port: int = 8080) -> threading.Thread:
    """Start uvicorn in a daemon thread and return the thread handle."""
    import uvicorn

    def _run():
        uvicorn.run(app, host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True, name="hydra-web")
    t.start()
    logger.info("Web UI started at http://%s:%d", host, port)
    return t
