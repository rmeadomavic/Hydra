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

from hydra_detect.web.config_api import (
    MAX_BODY_SIZE,
    has_backup,
    read_config,
    restore_backup,
    write_config,
)

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

TACTICAL_CATEGORIES = {
    "Personnel":        ["person", "soldier", "combatant", "civilian"],
    "Ground Vehicles":  ["car", "motorcycle", "truck", "bus", "bicycle", "train", "tank", "apc", "humvee"],
    "Watercraft/Air":   ["boat", "airplane", "drone", "uav", "helicopter", "ship"],
    "Carried Equipment":["backpack", "suitcase", "handbag", "cell phone", "laptop", "radio"],
    "Animals":          ["dog", "horse", "bird", "cow", "sheep", "cat", "elephant", "bear", "zebra", "giraffe"],
    "Potential Weapons": ["knife", "scissors", "baseball bat", "rifle", "pistol", "rpg"],
    "Concealment":      ["umbrella", "kite"],
    "Containers":       ["bottle", "cup", "bowl"],
    "Landmarks":        ["fire hydrant", "stop sign", "traffic light", "bench", "chair"],
}


def _categorize_classes(all_classes: list[str]) -> dict[str, list[str]]:
    """Group class names into tactical categories. Unmatched go to 'Other'."""
    result: Dict[str, List[str]] = {}
    categorized: set[str] = set()
    for cat_name, cat_classes in TACTICAL_CATEGORIES.items():
        matched = [c for c in all_classes if c in cat_classes]
        if matched:
            result[cat_name] = matched
            categorized.update(matched)
    other = [c for c in all_classes if c not in categorized]
    if other:
        result["Other"] = other
    return result


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


@app.post("/api/config/prompts")
async def api_set_prompts(request: Request, authorization: Optional[str] = Header(None)):
    """Update detection prompt labels at runtime.

    Body: {"prompts": ["person", "car", "dog"]}
    """
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    prompts = body.get("prompts")
    if not isinstance(prompts, list):
        return JSONResponse({"error": "prompts must be a list"}, status_code=400)
    if len(prompts) == 0:
        return JSONResponse({"error": "prompts list must not be empty"}, status_code=400)
    if len(prompts) > MAX_PROMPTS:
        return JSONResponse(
            {"error": f"max {MAX_PROMPTS} prompts allowed"}, status_code=400,
        )
    cleaned: List[str] = []
    for p in prompts:
        if not isinstance(p, str):
            return JSONResponse({"error": "each prompt must be a string"}, status_code=400)
        p = p.strip()
        if not p:
            return JSONResponse({"error": "prompts must not be empty or blank"}, status_code=400)
        cleaned.append(p[:MAX_PROMPT_LENGTH])

    cb = stream_state.get_callback("on_prompts_change")
    if cb:
        cb(cleaned)
        _audit(request, "set_prompts", target=str(len(cleaned)))
        return {"status": "ok", "prompts": cleaned}
    # Store even without callback — allows web UI to track prompts
    _audit(request, "set_prompts", target=str(len(cleaned)))
    return {"status": "ok", "prompts": cleaned}


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


@app.get("/api/config/alert-classes")
async def api_get_alert_classes():
    """Return current alert class filter and available classes."""
    cb = stream_state.get_callback("get_class_names")
    all_classes = cb() if cb else []
    config = stream_state.get_runtime_config()
    alert_classes = config.get("alert_classes", [])
    return {
        "alert_classes": alert_classes,
        "all_classes": all_classes,
        "categories": _categorize_classes(all_classes),
    }


@app.post("/api/config/alert-classes")
async def api_set_alert_classes(request: Request, authorization: Optional[str] = Header(None)):
    """Update alert class filter. Body: {"classes": ["person", "car"]} or {"classes": []} for all."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    classes = body.get("classes")
    if not isinstance(classes, list):
        return JSONResponse({"error": "classes must be a list"}, status_code=400)
    if classes:
        cb = stream_state.get_callback("get_class_names")
        valid_classes = set(cb()) if cb else set()
        for c in classes:
            if not isinstance(c, str):
                return JSONResponse({"error": "each class must be a string"}, status_code=400)
            if valid_classes and c not in valid_classes:
                return JSONResponse({"error": f"unknown class: {c}"}, status_code=400)
    cb = stream_state.get_callback("on_alert_classes_change")
    if cb:
        cb(classes)
        _audit(request, "set_alert_classes", target=str(len(classes)))
        return {"status": "ok", "classes": classes}
    _audit(request, "set_alert_classes", outcome="unavailable")
    return JSONResponse({"error": "alert class filter not available"}, status_code=503)


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


@app.post("/api/vehicle/mode")
async def api_set_vehicle_mode(request: Request, authorization: Optional[str] = Header(None)):
    """Set vehicle flight mode. Body: {"mode": "AUTO"}"""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    mode = body.get("mode")
    if not mode or not isinstance(mode, str):
        return JSONResponse({"error": "mode is required (string)"}, status_code=400)
    cb = stream_state.get_callback("on_set_mode_command")
    if cb:
        success = cb(mode)
        if success:
            _audit(request, "set_mode", target=mode)
            return {"status": "ok", "mode": mode}
        _audit(request, "set_mode", target=mode, outcome="failed")
        return JSONResponse({"error": f"Failed to set mode {mode}"}, status_code=503)
    _audit(request, "set_mode", outcome="mavlink_disconnected")
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


# ── RF Hunt ─────────────────────────────────────────────────────

@app.get("/api/rf/status")
async def api_rf_status():
    """Return current RF hunt status."""
    cb = stream_state.get_callback("get_rf_status")
    if cb:
        return cb()
    return {"state": "unavailable"}


@app.post("/api/rf/start")
async def api_rf_start(request: Request, authorization: Optional[str] = Header(None)):
    """Start an RF hunt with the given parameters.

    Body: {mode, target_bssid, target_freq_mhz, search_pattern,
           search_area_m, search_spacing_m, search_alt_m,
           rssi_threshold_dbm, rssi_converge_dbm, gradient_step_m}
    All fields optional — unset fields keep current config values.
    """
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()

    # Validate mode
    mode = body.get("mode")
    if mode and mode not in ("wifi", "sdr"):
        return JSONResponse({"error": "mode must be 'wifi' or 'sdr'"}, status_code=400)

    # Validate BSSID format if provided
    bssid = body.get("target_bssid", "").strip()
    if mode == "wifi" and not bssid:
        return JSONResponse({"error": "target_bssid required for wifi mode"}, status_code=400)
    if bssid and len(bssid) != 17:
        return JSONResponse({"error": "target_bssid must be MAC format AA:BB:CC:DD:EE:FF"}, status_code=400)

    # Validate freq if SDR
    freq = body.get("target_freq_mhz")
    if freq is not None:
        try:
            freq = float(freq)
            if not (1.0 <= freq <= 6000.0):
                return JSONResponse({"error": "target_freq_mhz must be 1-6000"}, status_code=400)
        except (TypeError, ValueError):
            return JSONResponse({"error": "target_freq_mhz must be a number"}, status_code=400)

    # Validate search pattern
    pattern = body.get("search_pattern")
    if pattern and pattern not in ("lawnmower", "spiral"):
        return JSONResponse({"error": "search_pattern must be 'lawnmower' or 'spiral'"}, status_code=400)

    # Validate numeric fields
    for field, lo, hi in [
        ("search_area_m", 10.0, 2000.0),
        ("search_spacing_m", 2.0, 200.0),
        ("search_alt_m", 3.0, 120.0),
        ("rssi_threshold_dbm", -100.0, -10.0),
        ("rssi_converge_dbm", -90.0, 0.0),
        ("gradient_step_m", 1.0, 50.0),
    ]:
        val = body.get(field)
        if val is not None:
            try:
                val = float(val)
                if not (lo <= val <= hi):
                    return JSONResponse(
                        {"error": f"{field} must be {lo}-{hi}"}, status_code=400,
                    )
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{field} must be a number"}, status_code=400)

    cb = stream_state.get_callback("on_rf_start")
    if cb:
        result = cb(body)
        if result:
            _audit(request, "rf_hunt_start", target=str(body.get("mode", "wifi")))
            return {"status": "ok", "message": "RF hunt started"}
        _audit(request, "rf_hunt_start", outcome="failed")
        return JSONResponse(
            {"error": "RF hunt failed to start — check Kismet connection and GPS fix"},
            status_code=503,
        )
    _audit(request, "rf_hunt_start", outcome="unavailable")
    return JSONResponse({"error": "RF homing not configured"}, status_code=503)


@app.post("/api/rf/stop")
async def api_rf_stop(request: Request, authorization: Optional[str] = Header(None)):
    """Stop an active RF hunt."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_rf_stop")
    if cb:
        cb()
        _audit(request, "rf_hunt_stop")
        return {"status": "ok", "message": "RF hunt stopped"}
    _audit(request, "rf_hunt_stop", outcome="unavailable")
    return JSONResponse({"error": "RF homing not configured"}, status_code=503)


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


# ── Full Config ────────────────────────────────────────────────

@app.get("/api/config/full")
async def api_get_full_config(authorization: str | None = Header(None)):
    """Return all config.ini sections as JSON. Sensitive fields are redacted."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    try:
        return read_config()
    except Exception as e:
        logger.error("Failed to read config: %s", e)
        return JSONResponse({"error": "Failed to read configuration"}, status_code=500)


@app.post("/api/config/full")
async def api_set_full_config(request: Request, authorization: str | None = Header(None)):
    """Update config.ini fields. Returns list of fields requiring restart."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    import json as _json
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_SIZE:
        return JSONResponse({"error": "Request body too large"}, status_code=413)
    try:
        body = _json.loads(body_bytes)
    except (ValueError, _json.JSONDecodeError):
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        result = write_config(body)
        _audit(request, "config_update", target=str(len(body)))
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("Failed to write config: %s", e)
        return JSONResponse({"error": f"Failed to save configuration: {e}"}, status_code=500)


@app.post("/api/config/restore-backup")
async def api_restore_config_backup(request: Request, authorization: str | None = Header(None)):
    """Restore config.ini from backup."""
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    if not has_backup():
        return JSONResponse({"error": "No backup file exists"}, status_code=404)
    try:
        restore_backup()
        _audit(request, "config_restore_backup")
        return {"status": "ok", "message": "Configuration restored from backup"}
    except Exception as e:
        logger.error("Failed to restore config backup: %s", e)
        return JSONResponse({"error": f"Failed to restore: {e}"}, status_code=500)


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
