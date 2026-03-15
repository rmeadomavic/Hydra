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

        # Runtime config callbacks (set by pipeline via set_callbacks)
        self._callbacks: Dict[str, Callable] = {}

        # Current runtime config (readable by web UI)
        self.runtime_config: Dict[str, Any] = {
            "threshold": 0.45,
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

    def set_callbacks(self, **callbacks: Optional[Callable]) -> None:
        with self._lock:
            for name, cb in callbacks.items():
                if cb is not None:
                    self._callbacks[name] = cb

    def get_callback(self, name: str) -> Optional[Callable]:
        """Safely retrieve a callback by name."""
        with self._lock:
            return self._callbacks.get(name)


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

    cb = stream_state.get_callback("on_threshold_change")
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
    cb = stream_state.get_callback("on_loiter_command")
    if cb:
        cb()
        _audit(request, "loiter")
        return {"status": "ok", "command": "loiter"}
    _audit(request, "loiter", outcome="mavlink_disconnected")
    return JSONResponse({"error": "MAVLink not connected"}, status_code=503)


@app.get("/api/tracks")
async def api_active_tracks():
    """Return currently active tracked objects (for target selection)."""
    cb = stream_state.get_callback("get_active_tracks")
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

    cb = stream_state.get_callback("on_target_lock")
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
    cb = stream_state.get_callback("on_target_unlock")
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

    cb = stream_state.get_callback("on_strike_command")
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
    cb = stream_state.get_callback("get_recent_detections")
    if cb:
        return cb()
    return []


@app.get("/api/camera/sources")
async def api_camera_sources():
    """Return available video sources."""
    cb = stream_state.get_callback("get_camera_sources")
    if cb:
        return cb()
    return []


@app.post("/api/camera/switch")
async def api_camera_switch(request: Request, authorization: Optional[str] = Header(None)):
    """Switch to a different camera source at runtime.

    Body: {"source": 2}  (device index or RTSP URL)
    """
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    source = body.get("source")
    if source is None:
        return JSONResponse({"error": "source required"}, status_code=400)

    cb = stream_state.get_callback("on_camera_switch")
    if cb:
        success = cb(source)
        if success:
            _audit(request, "camera_switch", target=str(source))
            return {"status": "ok", "source": source}
        _audit(request, "camera_switch", target=str(source), outcome="failed")
        return JSONResponse({"error": "Failed to switch camera source"}, status_code=400)
    return JSONResponse({"error": "Camera switch not available"}, status_code=503)


@app.get("/api/system/power-modes")
async def api_power_modes():
    """Return available Jetson power modes."""
    cb = stream_state.get_callback("get_power_modes")
    if cb:
        return cb()
    return []


@app.post("/api/system/power-mode")
async def api_set_power_mode(request: Request, authorization: Optional[str] = Header(None)):
    """Set Jetson power mode. Body: {"mode_id": 0}"""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    mode_id = body.get("mode_id")
    if mode_id is None:
        return JSONResponse({"error": "mode_id required"}, status_code=400)
    try:
        mode_id_int = int(mode_id)
    except (TypeError, ValueError):
        return JSONResponse({"error": "mode_id must be an integer"}, status_code=400)
    cb = stream_state.get_callback("on_set_power_mode")
    if cb:
        result = cb(mode_id_int)
        _audit(request, "set_power_mode", target=str(mode_id_int),
               outcome=result.get("status", "unknown"))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    return JSONResponse({"error": "Power mode control not available"}, status_code=503)


@app.get("/api/models")
async def api_list_models():
    """Return available YOLO model files."""
    cb = stream_state.get_callback("get_models")
    if cb:
        return cb()
    return []


@app.post("/api/models/switch")
async def api_switch_model(request: Request, authorization: Optional[str] = Header(None)):
    """Switch YOLO model at runtime. Body: {"model": "yolov8s.pt"}"""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    model = body.get("model")
    if not model:
        return JSONResponse({"error": "model name required"}, status_code=400)
    cb = stream_state.get_callback("on_model_switch")
    if cb:
        success = cb(model)
        if success:
            _audit(request, "model_switch", target=model)
            return {"status": "ok", "model": model}
        _audit(request, "model_switch", target=model, outcome="failed")
        return JSONResponse({"error": "Failed to switch model"}, status_code=400)
    return JSONResponse({"error": "Model switching not available"}, status_code=503)


@app.post("/api/pipeline/stop")
async def api_pipeline_stop(request: Request, authorization: Optional[str] = Header(None)):
    """Gracefully stop the pipeline and shut down."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_stop_command")
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
        cb = stream_state.get_callback("on_pause_command")
        if cb:
            cb()
            _audit(request, "pipeline_pause")
            return {"status": "ok", "paused": True}
    else:
        cb = stream_state.get_callback("on_resume_command")
        if cb:
            cb()
            _audit(request, "pipeline_resume")
            return {"status": "ok", "paused": False}
    return JSONResponse({"error": "Pause/resume not available"}, status_code=503)


# ── Mission Review ────────────────────────────────────────────────

@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    """Serve the post-mission review page."""
    return templates.TemplateResponse("review.html", {"request": request})


@app.get("/api/review/logs")
async def api_review_logs():
    """List available detection log files."""
    cb = stream_state.get_callback("get_log_dir")
    log_dir = cb() if cb else "/data/logs"
    image_dir_cb = stream_state.get_callback("get_image_dir")
    image_dir = image_dir_cb() if image_dir_cb else "/data/images"
    result = []
    log_path = Path(log_dir)
    if log_path.is_dir():
        for f in sorted(log_path.iterdir(), reverse=True):
            if f.suffix in (".jsonl", ".csv"):
                result.append({
                    "filename": f.name,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "modified": f.stat().st_mtime,
                })
    return {"logs": result, "image_dir": image_dir}


@app.get("/api/review/log/{filename}")
async def api_review_log(filename: str):
    """Parse and return detection data from a log file."""
    import json as _json
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    cb = stream_state.get_callback("get_log_dir")
    log_dir = cb() if cb else "/data/logs"
    path = Path(log_dir) / filename

    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "Log file not found"}, status_code=404)

    records = []
    if path.suffix == ".jsonl":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        continue
    elif path.suffix == ".csv":
        import csv as _csv
        with open(path) as f:
            reader = _csv.DictReader(f)
            for row in reader:
                # Convert numeric fields
                for key in ("confidence", "x1", "y1", "x2", "y2", "lat", "lon", "alt"):
                    if key in row and row[key]:
                        try:
                            row[key] = float(row[key])
                        except ValueError:
                            pass
                for key in ("frame", "track_id", "class_id", "fix"):
                    if key in row and row[key]:
                        try:
                            row[key] = int(row[key])
                        except ValueError:
                            pass
                records.append(row)

    return {"filename": filename, "count": len(records), "detections": records}


@app.get("/api/review/images/{filename}")
async def api_review_image(filename: str):
    """Serve a saved detection image."""
    from fastapi.responses import FileResponse
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    cb = stream_state.get_callback("get_image_dir")
    image_dir = cb() if cb else "/data/images"
    path = Path(image_dir) / filename

    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "Image not found"}, status_code=404)

    return FileResponse(str(path), media_type="image/jpeg")


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
