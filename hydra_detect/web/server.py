"""FastAPI web server — MJPEG stream, operator dashboard, runtime config, and REST API."""

from __future__ import annotations

import asyncio
import datetime
import hmac
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Hydra Detect v2.0", version="2.0.0")

# CORS: restrict to same-origin by default; override via configure_cors()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # No cross-origin by default
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# API token for control endpoints — set via configure_auth()
_api_token: Optional[str] = None


def configure_auth(token: Optional[str]) -> None:
    """Set the API token for control endpoints. None or empty disables auth."""
    global _api_token
    _api_token = token if token else None
    if _api_token:
        logger.info("API token auth enabled for control endpoints.")
    else:
        logger.info("API token auth disabled (no token configured).")


def _check_auth(authorization: Optional[str]) -> Optional[JSONResponse]:
    """Validate Bearer token. Returns an error response if auth fails, None if OK."""
    if _api_token is None:
        return None  # Auth disabled
    if not authorization or not authorization.startswith("Bearer "):
        return JSONResponse({"error": "Authorization header with Bearer token required"}, status_code=401)
    provided = authorization[len("Bearer "):]
    if not hmac.compare_digest(provided, _api_token):
        return JSONResponse({"error": "Invalid API token"}, status_code=403)
    return None


# Dedicated audit logger for control actions
audit_log = logging.getLogger("hydra.audit")

# Prompt constraints
MAX_PROMPTS = 20
MAX_PROMPT_LENGTH = 200


def _audit(request: Request, action: str, target: str = "", outcome: str = "ok") -> None:
    """Log a control action for accountability."""
    client = request.client.host if request.client else "unknown"
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    audit_log.info("ts=%s actor=%s action=%s target=%s outcome=%s", ts, client, action, target, outcome)


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
        self._on_target_lock: Optional[Callable] = None
        self._on_target_unlock: Optional[Callable] = None
        self._on_strike_command: Optional[Callable] = None
        self._get_recent_detections: Optional[Callable] = None
        self._get_active_tracks: Optional[Callable] = None
        self._on_stop_command: Optional[Callable] = None
        self._on_pause_command: Optional[Callable] = None
        self._on_resume_command: Optional[Callable] = None

        # Current runtime config (readable by web UI)
        self.runtime_config: Dict[str, Any] = {
            "prompts": [],
            "threshold": 0.25,
            "auto_loiter": False,
        }

        # Target lock state (readable by web UI)
        self.target_lock: Dict[str, Any] = {
            "locked": False,
            "track_id": None,
            "mode": None,  # "track" or "strike"
            "label": None,
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

    def set_target_lock(self, lock_state: Dict[str, Any]) -> None:
        with self._lock:
            self.target_lock = lock_state

    def get_target_lock(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.target_lock)

    def set_runtime_config(self, key: str, value: Any) -> None:
        with self._lock:
            self.runtime_config[key] = value

    def update_runtime_config(self, updates: Dict[str, Any]) -> None:
        with self._lock:
            self.runtime_config.update(updates)

    def get_runtime_config(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.runtime_config)

    def set_callbacks(
        self,
        on_prompts_change: Optional[Callable] = None,
        on_threshold_change: Optional[Callable] = None,
        on_loiter_command: Optional[Callable] = None,
        on_target_lock: Optional[Callable] = None,
        on_target_unlock: Optional[Callable] = None,
        on_strike_command: Optional[Callable] = None,
        get_recent_detections: Optional[Callable] = None,
        get_active_tracks: Optional[Callable] = None,
        on_stop_command: Optional[Callable] = None,
        on_pause_command: Optional[Callable] = None,
        on_resume_command: Optional[Callable] = None,
    ) -> None:
        with self._lock:
            self._on_prompts_change = on_prompts_change
            self._on_threshold_change = on_threshold_change
            self._on_loiter_command = on_loiter_command
            self._on_target_lock = on_target_lock
            self._on_target_unlock = on_target_unlock
            self._on_strike_command = on_strike_command
            self._get_recent_detections = get_recent_detections
            self._get_active_tracks = get_active_tracks
            self._on_stop_command = on_stop_command
            self._on_pause_command = on_pause_command
            self._on_resume_command = on_resume_command

    def get_callback(self, name: str) -> Optional[Callable]:
        """Safely retrieve a callback by name."""
        with self._lock:
            return getattr(self, name, None)


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
    return stream_state.get_runtime_config()


@app.post("/api/config/prompts")
async def api_set_prompts(request: Request, authorization: Optional[str] = Header(None)):
    """Update detection prompts at runtime (NanoOWL)."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    prompts = body.get("prompts", [])
    if not prompts or not isinstance(prompts, list):
        _audit(request, "set_prompts", outcome="invalid_input")
        return JSONResponse({"error": "prompts must be a non-empty list"}, status_code=400)

    if len(prompts) > MAX_PROMPTS:
        _audit(request, "set_prompts", outcome="too_many_prompts")
        return JSONResponse({"error": f"max {MAX_PROMPTS} prompts allowed"}, status_code=400)

    sanitized = []
    for p in prompts:
        if not isinstance(p, str) or not p.strip():
            _audit(request, "set_prompts", outcome="invalid_prompt_value")
            return JSONResponse({"error": "each prompt must be a non-empty string"}, status_code=400)
        s = p.strip()[:MAX_PROMPT_LENGTH]
        sanitized.append(s)

    cb = stream_state.get_callback("_on_prompts_change")
    if cb:
        cb(sanitized)
        stream_state.set_runtime_config("prompts", sanitized)
        _audit(request, "set_prompts", target=",".join(sanitized))
        return {"status": "ok", "prompts": sanitized}
    _audit(request, "set_prompts", outcome="unsupported")
    return JSONResponse({"error": "prompt change not supported for current detector"}, status_code=400)


@app.post("/api/config/threshold")
async def api_set_threshold(request: Request, authorization: Optional[str] = Header(None)):
    """Update detection confidence threshold at runtime."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    threshold = body.get("threshold")
    try:
        threshold_val = float(threshold)
    except (TypeError, ValueError):
        return JSONResponse({"error": "threshold must be a number 0.0-1.0"}, status_code=400)
    if not (0.0 <= threshold_val <= 1.0):
        return JSONResponse({"error": "threshold must be 0.0-1.0"}, status_code=400)

    cb = stream_state.get_callback("_on_threshold_change")
    if cb:
        cb(threshold_val)
        stream_state.set_runtime_config("threshold", threshold_val)
        _audit(request, "set_threshold", target=f"{threshold_val:.2f}")
        return {"status": "ok", "threshold": threshold_val}
    _audit(request, "set_threshold", outcome="unavailable")
    return JSONResponse({"error": "threshold change not available"}, status_code=400)


@app.post("/api/vehicle/loiter")
async def api_command_loiter(request: Request, authorization: Optional[str] = Header(None)):
    """Command vehicle to LOITER/HOLD at current position."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("_on_loiter_command")
    if cb:
        cb()
        _audit(request, "loiter")
        return {"status": "ok", "command": "loiter"}
    _audit(request, "loiter", outcome="mavlink_disconnected")
    return JSONResponse({"error": "MAVLink not connected"}, status_code=503)


@app.get("/api/tracks")
async def api_active_tracks():
    """Return currently active tracked objects (for target selection)."""
    cb = stream_state.get_callback("_get_active_tracks")
    if cb:
        return cb()
    return []


@app.get("/api/target")
async def api_target_status():
    """Return current target lock state."""
    return stream_state.get_target_lock()


@app.post("/api/target/lock")
async def api_target_lock(request: Request, authorization: Optional[str] = Header(None)):
    """Lock onto a tracked object for keep-in-frame tracking.

    Body: {"track_id": 5}
    """
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    track_id = body.get("track_id")
    if track_id is None:
        return JSONResponse({"error": "track_id required"}, status_code=400)
    try:
        track_id_int = int(track_id)
    except (TypeError, ValueError):
        return JSONResponse({"error": "track_id must be an integer"}, status_code=400)

    cb = stream_state.get_callback("_on_target_lock")
    if cb:
        result = cb(track_id_int, mode="track")
        if result:
            _audit(request, "target_lock", target=str(track_id))
            return {"status": "ok", "track_id": track_id, "mode": "track"}
        _audit(request, "target_lock", target=str(track_id), outcome="track_not_found")
        return JSONResponse({"error": "track_id not found in active tracks"}, status_code=404)
    _audit(request, "target_lock", outcome="unavailable")
    return JSONResponse({"error": "target lock not available"}, status_code=503)


@app.post("/api/target/unlock")
async def api_target_unlock(request: Request, authorization: Optional[str] = Header(None)):
    """Release target lock."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("_on_target_unlock")
    if cb:
        cb()
        _audit(request, "target_unlock")
        return {"status": "ok"}
    _audit(request, "target_unlock", outcome="unavailable")
    return JSONResponse({"error": "target lock not available"}, status_code=503)


@app.post("/api/target/strike")
async def api_strike_command(request: Request, authorization: Optional[str] = Header(None)):
    """Command vehicle to navigate toward the locked target.

    Body: {"track_id": 5, "confirm": true}
    The confirm field MUST be true — this is a safety check.
    """
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    track_id = body.get("track_id")
    confirm = body.get("confirm", False)

    if not confirm:
        return JSONResponse(
            {"error": "Strike requires explicit confirmation. Set confirm=true."},
            status_code=400,
        )
    if track_id is None:
        return JSONResponse({"error": "track_id required"}, status_code=400)
    try:
        track_id_int = int(track_id)
    except (TypeError, ValueError):
        return JSONResponse({"error": "track_id must be an integer"}, status_code=400)

    cb = stream_state.get_callback("_on_strike_command")
    if cb:
        result = cb(track_id_int)
        if result:
            _audit(request, "strike", target=str(track_id))
            return {"status": "ok", "track_id": track_id, "mode": "strike"}
        _audit(request, "strike", target=str(track_id), outcome="failed")
        return JSONResponse(
            {"error": "Strike failed — no GPS fix or track not found"},
            status_code=503,
        )
    _audit(request, "strike", outcome="unavailable")
    return JSONResponse({"error": "strike not available"}, status_code=503)


@app.get("/api/detections")
async def api_recent_detections():
    """Return recent detection log entries."""
    cb = stream_state.get_callback("_get_recent_detections")
    if cb:
        return cb()
    return []


@app.post("/api/pipeline/stop")
async def api_pipeline_stop(request: Request, authorization: Optional[str] = Header(None)):
    """Gracefully stop the pipeline and shut down."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("_on_stop_command")
    if cb:
        cb()
        _audit(request, "pipeline_stop")
        return {"status": "ok", "message": "Shutting down"}
    _audit(request, "pipeline_stop", outcome="unavailable")
    return JSONResponse({"error": "Stop not available"}, status_code=503)


@app.post("/api/pipeline/pause")
async def api_pipeline_pause(request: Request, authorization: Optional[str] = Header(None)):
    """Pause or resume the detection pipeline."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    paused = body.get("paused", True)
    if paused:
        cb = stream_state.get_callback("_on_pause_command")
        if cb:
            cb()
            _audit(request, "pipeline_pause")
            return {"status": "ok", "paused": True}
    else:
        cb = stream_state.get_callback("_on_resume_command")
        if cb:
            cb()
            _audit(request, "pipeline_resume")
            return {"status": "ok", "paused": False}
    return JSONResponse({"error": "Pause/resume not available"}, status_code=503)


@app.get("/stream.mjpeg")
async def mjpeg_stream():
    """MJPEG video stream endpoint."""
    return StreamingResponse(
        _generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


async def _generate_mjpeg():
    """Async generator that yields JPEG frames.

    Catches GeneratorExit / CancelledError to clean up when the client disconnects.
    """
    quality = 70
    try:
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
    except (GeneratorExit, asyncio.CancelledError):
        logger.debug("MJPEG stream client disconnected.")
        return


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
