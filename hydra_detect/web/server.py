"""FastAPI web server — MJPEG stream, operator dashboard, runtime config, and REST API."""

from __future__ import annotations

import asyncio
import collections
import datetime
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import cv2
import numpy as np
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.datastructures import MutableHeaders
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from hydra_detect.web.config_api import (
    MAX_BODY_SIZE,
    has_backup,
    has_factory,
    read_config,
    restore_backup,
    restore_factory,
    write_config,
    validate_config_updates,
)
from hydra_detect.config_schema import SCHEMA as CONFIG_SCHEMA
from hydra_detect.audit import (
    attach_to_logger as _attach_audit,
    get_default_sink as _get_audit_sink,
)
from hydra_detect.observability import (
    attach_audit_counters as _attach_metrics_counters,
    get_client_error_sink as _get_client_error_sink,
    health_snapshot as _health_snapshot,
    hydra_fps as _m_fps,
    hydra_inference_ms as _m_inference_ms,
    render_metrics as _render_metrics,
)

# Attach the audit ring handler to the `hydra.audit` logger exactly once.
# Safe to import-time — idempotent + non-blocking.
_attach_audit()
_attach_metrics_counters()
_audit_sink = _get_audit_sink()
_client_error_sink = _get_client_error_sink()

# Wire gauge providers to read live values from ``stream_state.stats`` on
# every Prometheus scrape. Set once at import — provider closures are stable.
_m_fps.set_provider(lambda: stream_state.get_stats().get("fps"))
_m_inference_ms.set_provider(lambda: stream_state.get_stats().get("inference_ms"))

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Hydra Detect v2.0", version="2.0.0")

# CORS: restrict cross-origin to instructor-relevant paths only.
# The instructor page polls /api/stats and /api/abort on peer Hydra
# instances, so those endpoints need permissive CORS.  All other
# endpoints stay same-origin.
_CORS_ALLOWED_PATHS = {"/api/stats", "/api/abort"}


class _InstructorCORSMiddleware:
    """Pure ASGI middleware — add CORS headers for instructor endpoints."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and path in _CORS_ALLOWED_PATHS:
                headers = MutableHeaders(scope=message)
                headers.append("Access-Control-Allow-Origin", "*")
                headers.append("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                headers.append("Access-Control-Allow-Headers", "Authorization, Content-Type")
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(_InstructorCORSMiddleware)

# Standard CSP for the SPA and standalone pages
_CSP_DEFAULT = (
    "default-src 'self'; "
    "img-src 'self' data: https://*.tile.openstreetmap.org; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com; "
    "frame-src https://www.youtube-nocookie.com; "
    "connect-src 'self'"
)

# Relaxed CSP for the instructor page — it fetches from other Jetsons
_CSP_INSTRUCTOR = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src *"
)


class _SecurityHeadersMiddleware:
    """Pure ASGI middleware — inject security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                csp = _CSP_INSTRUCTOR if path == "/instructor" else _CSP_DEFAULT
                headers["Content-Security-Policy"] = csp
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(_SecurityHeadersMiddleware)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# API token for control endpoints — set via configure_auth()
_api_token: Optional[str] = None
_require_auth_for_control: bool = False

# Rate limiting for auth failures — per-IP, sliding window
_AUTH_FAIL_WINDOW = 60  # seconds
_AUTH_FAIL_MAX = 50  # max failures per window before lockout
_auth_failures: Dict[str, list] = collections.defaultdict(list)


def _recent_auth_failures(client_ip: str, now: float) -> list[float]:
    """Return recent auth failures for an IP and prune expired entries."""
    failures = [t for t in _auth_failures.get(client_ip, []) if now - t < _AUTH_FAIL_WINDOW]
    if failures:
        _auth_failures[client_ip] = failures
    elif client_ip in _auth_failures:
        del _auth_failures[client_ip]
    return failures


def _record_auth_failure(client_ip: str, now: float) -> None:
    """Record one failed auth attempt for an IP."""
    _auth_failures.setdefault(client_ip, []).append(now)


def configure_auth(
    token: Optional[str],
    require_auth_for_control: bool = False,
) -> None:
    """Set the API token for control endpoints. None or empty disables auth."""
    global _api_token, _require_auth_for_control
    _api_token = token if token else None
    _require_auth_for_control = require_auth_for_control
    if _api_token:
        logger.info("API token auth enabled for control endpoints.")
    else:
        logger.info("API token auth disabled (no token configured).")


def _check_auth(
    authorization: Optional[str],
    request: Optional[Request] = None,
) -> Optional[JSONResponse]:
    """Validate Bearer token. Returns an error response if auth fails, None if OK."""
    if _api_token is None:
        if _require_auth_for_control:
            return JSONResponse(
                {"error": (
                    "Control endpoint requires api_token. Set it in"
                    " config.ini or set require_auth_for_control = false."
                )},
                status_code=401,
            )
        return None  # Auth disabled

    if request is not None:
        origin = request.headers.get("origin", "")
        if origin and _origin_matches_request(origin, request):
            return None
        # Password-authenticated sessions also bypass Bearer token check
        cookie_header = request.headers.get("cookie", "")
        if cookie_header:
            cookies = _parse_cookies(cookie_header)
            session = cookies.get("hydra_session", "")
            if session and _validate_session_cookie(session):
                return None

    # Rate limit check — reject if too many recent failures from this IP
    client_ip = request.client.host if request and request.client else "unknown"
    now = time.monotonic()
    failures = _recent_auth_failures(client_ip, now)
    if len(failures) >= _AUTH_FAIL_MAX:
        return JSONResponse({"error": "Too many failed attempts, try again later"}, status_code=429)

    if not authorization or not authorization.startswith("Bearer "):
        _record_auth_failure(client_ip, now)
        return JSONResponse(
            {"error": "Authorization header with Bearer token required"},
            status_code=401,
        )
    provided = authorization[len("Bearer "):]
    if not hmac.compare_digest(provided, _api_token):
        _record_auth_failure(client_ip, now)
        return JSONResponse({"error": "Invalid API token"}, status_code=403)
    return None


def _origin_matches_request(origin: str, request: Request) -> bool:
    """Return True when Origin exactly matches request scheme + host + port."""
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc or parsed.hostname is None:
        return False
    if parsed.username or parsed.password:
        return False

    req_scheme = request.url.scheme
    req_host = request.url.hostname
    if not req_scheme or not req_host:
        return False

    origin_port = parsed.port or (
        443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
    )
    req_port = request.url.port or (
        443 if req_scheme == "https" else 80 if req_scheme == "http" else None
    )
    if origin_port is None or req_port is None:
        return False

    return parsed.scheme == req_scheme and parsed.hostname == req_host and origin_port == req_port


# ── Web password session auth ────────────────────────────────────────
_web_password: str | None = None
_session_secret: bytes = secrets.token_bytes(32)
_session_timeout_sec: int = 8 * 3600
_tls_active: bool = False


def configure_web_password(
    password: str | None,
    timeout_min: int = 480,
    tls_enabled: bool = False,
) -> None:
    """Enable password-based browser access. None or empty disables it."""
    global _web_password, _session_timeout_sec, _tls_active
    _web_password = password if password else None
    _session_timeout_sec = timeout_min * 60
    _tls_active = tls_enabled
    if _web_password:
        logger.info("Web password auth enabled (session timeout: %d min).", timeout_min)
        if not tls_enabled:
            logger.warning(
                "web_password is set but TLS is disabled — "
                "password will be sent in cleartext over HTTP."
            )
    else:
        logger.info("Web password auth disabled (no password configured).")


def _make_session_cookie() -> str:
    """Create a signed session cookie: nonce:expires:signature."""
    nonce = secrets.token_hex(16)
    expires = int(time.time()) + _session_timeout_sec
    payload = f"{nonce}:{expires}"
    sig = hmac.new(_session_secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _validate_session_cookie(cookie: str) -> bool:
    """Verify HMAC signature and check expiry of a session cookie."""
    parts = cookie.split(":")
    if len(parts) != 3:
        return False
    payload = f"{parts[0]}:{parts[1]}"
    expected = hmac.new(_session_secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(parts[2], expected):
        return False
    try:
        return int(parts[1]) > time.time()
    except ValueError:
        return False


def _parse_cookies(cookie_header: str) -> dict[str, str]:
    """Parse a Cookie header into a dict."""
    cookies: dict[str, str] = {}
    for pair in cookie_header.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


# Paths that are always accessible without password login
_PUBLIC_PATH_PREFIXES = (
    "/login", "/auth/", "/static/",
    "/api/health", "/api/preflight", "/api/abort",
    "/api/stats",      # instructor page polls peers cross-origin
    "/api/tracks",     # read-only dashboard data
    "/api/metrics",    # Prometheus scrape
    "/api/client_error",  # frontend error sink (same-origin, rate-limited)
    "/stream.jpg",     # snapshot polling (img.src, no cookie in some contexts)
    "/stream.mjpeg",   # MJPEG fallback
)


class _SessionAuthMiddleware:
    """Pure ASGI middleware — gate all access behind password login.

    Skips entirely when no web_password is configured (default).
    Allows through requests with a valid Bearer token or session cookie.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _web_password is None:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Public paths — always accessible
        if any(path.startswith(p) for p in _PUBLIC_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Extract headers from raw ASGI scope
        headers = dict(scope.get("headers", []))

        # Allow requests with a valid Bearer token (API clients)
        auth_header = headers.get(b"authorization", b"").decode()
        if (
            _api_token
            and auth_header.startswith("Bearer ")
            and hmac.compare_digest(auth_header[7:], _api_token)
        ):
            await self.app(scope, receive, send)
            return

        # Check session cookie
        cookie_header = headers.get(b"cookie", b"").decode()
        if cookie_header:
            cookies = _parse_cookies(cookie_header)
            session = cookies.get("hydra_session", "")
            if session and _validate_session_cookie(session):
                await self.app(scope, receive, send)
                return

        # Not authenticated — decide response based on request type
        accept = headers.get(b"accept", b"").decode()
        if "text/html" in accept:
            # Browser page request — redirect to login
            redirect_body = b""
            await send({
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    [b"location", b"/login"],
                    [b"content-length", b"0"],
                ],
            })
            await send({
                "type": "http.response.body",
                "body": redirect_body,
            })
        else:
            # API request — return 401 JSON
            import json as _json
            body = _json.dumps({"error": "Login required"}).encode()
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                    [b"x-login-required", b"true"],
                ],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })


app.add_middleware(_SessionAuthMiddleware)


# Dedicated audit logger for control actions
audit_log = logging.getLogger("hydra.audit")


async def _parse_json(request: Request) -> dict | None:
    """Safely parse JSON body, returning None on malformed input."""
    try:
        return await request.json()
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

# Prompt constraints
MAX_PROMPTS = 20
MAX_PROMPT_LENGTH = 200
BSSID_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")

TACTICAL_CATEGORIES = {
    "People": [
        "person", "pedestrian", "people", "soldier", "combatant", "civilian",
    ],
    "Ground Vehicles": [
        "car", "truck", "bus", "van", "motorcycle", "bicycle", "tricycle",
        "awning-tricycle", "motor", "train", "tank", "apc", "afv", "mev",
        "lav", "humvee",
    ],
    "Aircraft": [
        "airplane", "helicopter", "drone", "fighter jet", "fighter plane",
        "light aircraft", "commercial aircraft", "cargo aircraft",
    ],
    "Watercraft": [
        "boat", "ship", "warship", "cargo ship", "cruise ship", "yacht",
        "sailboat",
    ],
    "Weapons/Threats": [
        "gun", "knife", "grenade", "explosion", "missile", "scissors",
        "baseball bat", "rifle", "pistol", "rpg",
    ],
    "Equipment": [
        "backpack", "suitcase", "handbag", "cell phone", "laptop", "radio",
        "bottle", "umbrella",
    ],
    "Animals": [
        "dog", "horse", "bird", "cow", "sheep", "cat", "bear", "elephant",
        "zebra", "giraffe",
    ],
    "Infrastructure": [
        "fire hydrant", "stop sign", "traffic light", "bench", "parking meter",
    ],
}

# Pre-built lowercase lookup: maps lowercase class name -> category name.
# Rebuilt once at import time (and after any hot-reload).
_CATEGORY_LOOKUP: Dict[str, str] = {}
for _cat, _members in TACTICAL_CATEGORIES.items():
    for _m in _members:
        _CATEGORY_LOOKUP[_m.lower()] = _cat


def _categorize_classes(all_classes: list[str]) -> dict[str, list[str]]:
    """Group class names into tactical categories (case-insensitive).

    Unmatched classes fall into 'Other'.
    """
    result: Dict[str, List[str]] = {}
    for c in all_classes:
        cat = _CATEGORY_LOOKUP.get(c.lower(), "Other")
        result.setdefault(cat, []).append(c)
    return result


def _audit(request: Request, action: str, target: str = "", outcome: str = "ok") -> None:
    """Log a control action for accountability."""
    client = request.client.host if request.client else "unknown"
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    audit_log.info(
        "ts=%s actor=%s action=%s target=%s outcome=%s",
        ts, client, action, target, outcome,
    )


# ── Response cache with stale-data fallback ──────────────────────────

_response_cache: dict[str, tuple[float, Any]] = {}
_RESPONSE_CACHE_TTL = 30.0


def _cached_callback(key: str, callback: Callable | None, *args: Any) -> Any | None:
    """Call a pipeline callback with stale-data fallback.

    If the callback succeeds, cache the result. If it raises or returns None
    and we have cached data within TTL, return the cached data instead.
    """
    now = time.monotonic()
    try:
        if callback is None:
            raise RuntimeError("no callback")
        result = callback(*args)
        if result is not None:
            _response_cache[key] = (now, result)
        return result
    except Exception:
        cached = _response_cache.get(key)
        if cached and (now - cached[0]) < _RESPONSE_CACHE_TTL:
            logger.warning("Serving stale %s (age %.1fs)", key, now - cached[0])
            return cached[1]
        return None


class StreamState:
    """Shared state between the pipeline and the web server."""

    def __init__(self):
        self.frame: Optional[np.ndarray] = None
        self.raw_frame: Optional[np.ndarray] = None
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

        # Adaptive MJPEG quality (1-100)
        self._mjpeg_quality: int = 70

    def update_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self.frame = frame

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self.frame.copy() if self.frame is not None else None

    def update_raw_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self.raw_frame = frame

    def get_raw_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self.raw_frame.copy() if self.raw_frame is not None else None

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

    def set_mjpeg_quality(self, quality: int) -> None:
        with self._lock:
            self._mjpeg_quality = max(1, min(100, quality))

    def get_mjpeg_quality(self) -> int:
        with self._lock:
            return self._mjpeg_quality


# Global state instance — set by the pipeline before starting the server
stream_state = StreamState()


# TAK command listener handle — set by the pipeline via set_tak_input().
# Powers GET /api/tak/commands (inbound GeoChat feed for dashboard).
_tak_input_ref: Any = None

# TAK output handle — powers /api/tak/peers (unicast targets) and
# /api/tak/type_counts side-channels. Set via set_tak_output().
_tak_output_ref: Any = None

# Servo tracker handle — powers /api/servo/status. Set via
# set_servo_tracker(). None => dashboard renders the idle state.
_servo_tracker_ref: Any = None

# RF ambient scan sink handle — powers /api/rf/ambient_scan. Set via
# set_rf_ambient_scan(). None => dashboard renders the idle state.
_rf_ambient_ref: Any = None

# Autonomous controller handle — powers /api/autonomy/status and
# /api/autonomy/mode. Set via set_autonomous_controller(). None =>
# endpoint returns the idle/default shape so the dashboard renders.
_autonomous_ref: Any = None

# MAVLink I/O handle — powers flight-instrument fields (heading, airspeed,
# altitude, vertical_speed) on /api/stats. Set via set_mavlink(). None =>
# those fields default to None so the dashboard renders a dash.
_mavlink_ref: Any = None


def set_tak_input(tak_input: Any) -> None:
    """Register the TAKInput instance for the /api/tak/commands feed.

    Called from the pipeline facade after the listener is constructed.
    Pass None to detach.
    """
    global _tak_input_ref
    _tak_input_ref = tak_input


def set_tak_output(tak_output: Any) -> None:
    """Register the TAKOutput instance for peer/unicast roll-ups."""
    global _tak_output_ref
    _tak_output_ref = tak_output


def set_mavlink(mav: Any) -> None:
    """Register the MAVLinkIO instance that powers flight-instrument
    fields on /api/stats. Pass None to detach."""
    global _mavlink_ref
    _mavlink_ref = mav


def set_servo_tracker(servo: Any) -> None:
    """Register a servo-state provider for /api/servo/status.

    The provided object must expose ``get_api_status() -> dict``. Pass
    None to detach (the endpoint will return the idle/disabled shape).
    """
    global _servo_tracker_ref
    _servo_tracker_ref = servo


def set_rf_ambient_scan(scanner: Any) -> None:
    """Register an ambient-scan sink for /api/rf/ambient_scan.

    The provided object must expose ``get_samples()`` returning a dict
    with keys ``samples``, ``window_seconds``, ``max_rssi``.
    """
    global _rf_ambient_ref
    _rf_ambient_ref = scanner


def set_autonomous_controller(controller: Any) -> None:
    """Register an AutonomousController for /api/autonomy/* endpoints.

    The provided object must expose ``get_dashboard_snapshot(callsign=...)``
    and ``set_mode(mode)``. Pass None to detach (endpoint falls back to
    an idle-default shape so the dashboard still renders).
    """
    global _autonomous_ref
    _autonomous_ref = controller


# ── Routes ────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the login page (or redirect to dashboard if already logged in)."""
    if _web_password is None:
        return Response(status_code=302, headers={"location": "/"})
    cookie = request.cookies.get("hydra_session", "")
    if cookie and _validate_session_cookie(cookie):
        return Response(status_code=302, headers={"location": "/"})
    return templates.TemplateResponse(request, "login.html")


@app.post("/auth/login")
async def auth_login(request: Request):
    """Validate password and set session cookie."""
    if _web_password is None:
        return JSONResponse({"status": "ok"})

    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()

    # Rate limit check (reuse same tracking as Bearer token auth)
    failures = _recent_auth_failures(client_ip, now)
    if len(failures) >= _AUTH_FAIL_MAX:
        return JSONResponse(
            {"error": "Too many failed attempts, try again later"}, status_code=429,
        )

    body = await _parse_json(request)
    if not body or "password" not in body:
        return JSONResponse({"error": "Missing password"}, status_code=400)

    password = str(body["password"])
    if not hmac.compare_digest(password, _web_password):
        _record_auth_failure(client_ip, now)
        return JSONResponse({"error": "Wrong password"}, status_code=401)

    cookie_value = _make_session_cookie()
    cookie_flags = (
        f"hydra_session={cookie_value}; "
        f"HttpOnly; SameSite=Lax; Path=/; Max-Age={_session_timeout_sec}"
    )
    if _tls_active:
        cookie_flags += "; Secure"

    return JSONResponse(
        {"status": "ok"},
        headers={"set-cookie": cookie_flags},
    )


@app.post("/auth/logout")
async def auth_logout():
    """Clear session cookie."""
    return JSONResponse(
        {"status": "ok"},
        headers={
            "set-cookie": "hydra_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
        },
    )


@app.get("/auth/status")
async def auth_status(request: Request):
    """Return whether web password auth is enabled and this request is authenticated."""
    if _web_password is None:
        return {"password_enabled": False, "authenticated": True}

    cookie = request.cookies.get("hydra_session", "")
    authenticated = bool(cookie and _validate_session_cookie(cookie))
    return {"password_enabled": True, "authenticated": authenticated}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the operator dashboard SPA."""
    return templates.TemplateResponse(request, "base.html")


@app.get("/api/health")
async def api_health():
    """Structured subsystem health.

    Returns::

        {
          "status": "ok"|"warn"|"fail",
          "ts": <unix>,
          "subsystems": { camera, mavlink, gps, detector, rtsp, tak, audit, disk:
                          {"status": "ok"|"warn"|"fail", "detail": str} },

          # Back-compat fields for Docker HEALTHCHECK + load balancers + older
          # clients that checked ``healthy`` / ``fps`` / ``camera_ok``:
          "healthy": bool, "camera_ok": bool, "fps": float,
        }

    HTTP 200 when overall ``status`` is ok or warn, 503 when ``fail``. (warn
    is deliberately not a 5xx — it should not take a Jetson out of rotation.)
    """
    stats = stream_state.get_stats()
    snapshot = _health_snapshot(
        stats=stats,
        mavlink_ref=_mavlink_ref,
        tak_output_ref=_tak_output_ref,
        audit_sink=_audit_sink,
    )
    status = snapshot.get("status", "ok")
    camera_ok = bool(stats.get("camera_ok", True))
    fps = float(stats.get("fps", 0.0))
    legacy_healthy = camera_ok and fps > 0
    body = dict(snapshot)
    body["healthy"] = legacy_healthy
    body["camera_ok"] = camera_ok
    body["fps"] = fps
    status_code = 503 if status == "fail" else (200 if legacy_healthy else 503)
    return JSONResponse(body, status_code=status_code)


@app.get("/api/metrics")
async def api_metrics():
    """Prometheus exposition — counters + gauges in 0.0.4 text format.

    Auth-free by design — Prometheus scrapers are expected to fetch this
    from the same subnet on a 15–60s interval. No sensitive data is
    exposed; values are aggregates already surfaced elsewhere.
    """
    body = _render_metrics()
    return Response(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# Rate limit for /api/client_error — per-remote-IP sliding window so a
# runaway JS handler on one tablet can't drown the Jetson.
_CLIENT_ERROR_WINDOW_SEC = 60.0
_CLIENT_ERROR_MAX_PER_WINDOW = 50
_client_error_hits: Dict[str, list[float]] = collections.defaultdict(list)
_client_error_lock = threading.Lock()


def _client_error_rate_limited(client_ip: str, now: float) -> bool:
    """Return True when ``client_ip`` has exceeded the per-window cap."""
    with _client_error_lock:
        hits = [t for t in _client_error_hits.get(client_ip, [])
                if now - t < _CLIENT_ERROR_WINDOW_SEC]
        if len(hits) >= _CLIENT_ERROR_MAX_PER_WINDOW:
            _client_error_hits[client_ip] = hits
            return True
        hits.append(now)
        _client_error_hits[client_ip] = hits
        return False


@app.post("/api/client_error")
async def api_client_error(request: Request):
    """Frontend error sink.

    Expected body (all fields optional, all coerced + clipped defensively)::

        {"message": str, "source": str, "lineno": int, "colno": int,
         "stack": str, "url": str, "timestamp": number}

    Auth-free, same-origin only. Rate-limited to 50 reports / 60 s / IP.
    """
    body = await _parse_json(request)
    if body is None or not isinstance(body, dict):
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)

    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    if _client_error_rate_limited(client_ip, now):
        return JSONResponse({"error": "rate limited"}, status_code=429)

    user_agent = request.headers.get("user-agent", "")[:512]
    _client_error_sink.push(
        message=body.get("message", ""),
        source=body.get("source", ""),
        lineno=body.get("lineno"),
        colno=body.get("colno"),
        stack=body.get("stack", ""),
        url=body.get("url", ""),
        client_ts=body.get("timestamp"),
        remote_addr=client_ip,
        user_agent=user_agent,
    )
    return {"status": "ok", "total": len(_client_error_sink)}


@app.get("/api/client_error/recent")
async def api_client_error_recent(limit: int = 50):
    """Read back recent client errors (dashboard diagnostic use)."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 50
    return _client_error_sink.snapshot(limit=max(1, min(200, n)))


@app.get("/api/preflight")
async def api_preflight():
    """Run pre-flight checks and return structured results.

    Returns a JSON object with a list of checks (camera, mavlink, config,
    models, disk) and an overall status (pass/warn/fail).  Designed for
    student-facing error reporting before a mission.
    """
    cb = stream_state.get_callback("get_preflight")
    if cb:
        return cb()
    return {"checks": [], "overall": "fail"}


@app.get("/api/stats")
async def api_stats():
    """Return current pipeline statistics as JSON."""
    result = _cached_callback("stats", stream_state.get_stats)
    if result is None:
        result = stream_state.stats.copy()  # fallback: return raw defaults
    # Always project flight-instrument fields onto the response so the
    # FlightHUD tapes render even before the pipeline plumbs them through.
    return dict(result, **_flight_fields())


def _flight_fields() -> Dict[str, Any]:
    """Read heading/airspeed/altitude/vertical_speed from the MAVLink
    handle, plus lat/lon/alt_agl for map rendering, falling back to
    None when MAVLink is not registered or has no fix."""
    defaults: Dict[str, Any] = {
        "heading": None,
        "airspeed": None,
        "altitude": None,
        "vertical_speed": None,
        "lat": None,
        "lon": None,
        "alt_msl_m": None,
    }
    mav = _mavlink_ref
    if mav is None:
        return defaults
    try:
        data = mav.get_flight_data()
    except Exception:
        data = {}
    if isinstance(data, dict):
        for key in ("heading", "airspeed", "altitude", "vertical_speed"):
            defaults[key] = data.get(key)
    try:
        lat, lon, alt = mav.get_lat_lon()
    except Exception:
        lat = lon = alt = None
    if lat is not None and lon is not None:
        defaults["lat"] = lat
        defaults["lon"] = lon
        defaults["alt_msl_m"] = alt
    return defaults


@app.get("/api/config")
async def api_get_config():
    """Return current runtime configuration."""
    return stream_state.get_runtime_config()


@app.post("/api/config/prompts")
async def api_set_prompts(request: Request, authorization: Optional[str] = Header(None)):
    """Update detection prompt labels at runtime.

    Body: {"prompts": ["person", "car", "dog"]}
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
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

    stream_state.set_runtime_config("prompts", cleaned)
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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
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
    """Update alert class filter.

    Body: {"classes": ["person", "car"]} or {"classes": []} for all.
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
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
    auth_err = _check_auth(authorization, request)
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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    _ALLOWED_MODES = {"AUTO", "RTL", "LOITER", "HOLD", "GUIDED"}
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    mode = body.get("mode")
    if not mode or not isinstance(mode, str):
        return JSONResponse({"error": "mode is required (string)"}, status_code=400)
    if mode not in _ALLOWED_MODES:
        allowed = ', '.join(sorted(_ALLOWED_MODES))
        return JSONResponse(
            {"error": f"mode must be one of: {allowed}"},
            status_code=400,
        )
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
    result = _cached_callback("tracks", cb)
    if result is not None:
        return result
    return []


@app.get("/api/tak/commands")
async def api_tak_commands(request: Request):
    """Return the most recent inbound TAK command events.

    Auth-free read (same-origin bypass list alongside /api/stats,
    /api/tracks, /api/config/full, /api/stream/quality). Powers the
    GeoChat inbound panel on the dashboard.

    Query params:
        limit (int, default 100): max events to return, capped at the
            TAKInput ring buffer size (500).
    """
    tak_in = _tak_input_ref

    # Parse and clamp limit
    try:
        limit = int(request.query_params.get("limit", "100"))
    except (TypeError, ValueError):
        limit = 100
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    if tak_in is None:
        return JSONResponse({
            "enabled": False,
            "commands": [],
            "allowed_callsigns": [],
            "hmac_enforced": False,
            "duplicate_callsign_alarm": False,
            "limit": limit,
        })

    return JSONResponse({
        "enabled": True,
        "commands": tak_in.get_recent_commands(limit),
        "allowed_callsigns": sorted(tak_in._allowed_callsigns),
        "hmac_enforced": tak_in._hmac_secret is not None,
        "duplicate_callsign_alarm": bool(tak_in._duplicate_callsign),
        "limit": limit,
    })


@app.get("/api/tak/type_counts")
async def api_tak_type_counts(request: Request):
    """Return an inbound CoT-type histogram over a bounded time window.

    Auth-free read (same-origin bypass list alongside /api/tak/commands,
    /api/stats, /api/tracks, /api/config/full, /api/stream/quality).

    Query params:
        window_seconds (int, default 900, capped at 3600): window over
            which to aggregate the histogram.
    """
    tak_in = _tak_input_ref
    try:
        window = int(request.query_params.get("window_seconds", "900"))
    except (TypeError, ValueError):
        window = 900
    window = max(1, min(3600, window))

    if tak_in is None:
        return JSONResponse({
            "enabled": False,
            "counts": {},
            "total": 0,
            "window_seconds": window,
        })

    hist = tak_in.get_type_counts(window_seconds=window)
    return JSONResponse({"enabled": True, **hist})


@app.get("/api/tak/peers")
async def api_tak_peers():
    """Return the current inbound TAK peer roster with security flags.

    Auth-free read. Surfaces allowed_callsigns / hmac_enforced /
    duplicate_callsign_alarm from TAKInput alongside the peer list and the
    current unicast target set from TAKOutput — a single roll-up for the
    TAK map panel and security chip.
    """
    tak_in = _tak_input_ref
    tak_out = _tak_output_ref

    if tak_out is not None:
        unicast_targets = [
            f"{t['host']}:{t['port']}" for t in tak_out.get_unicast_targets()
        ]
    else:
        unicast_targets = []

    if tak_in is None:
        return JSONResponse({
            "enabled": False,
            "peers": [],
            "unicast_targets": unicast_targets,
            "hmac_enforced": False,
            "duplicate_callsign_alarm": False,
            "allowed_callsigns": [],
        })

    return JSONResponse({
        "enabled": True,
        "peers": tak_in.get_peers(),
        "unicast_targets": unicast_targets,
        "hmac_enforced": tak_in._hmac_secret is not None,
        "duplicate_callsign_alarm": bool(tak_in._duplicate_callsign),
        "allowed_callsigns": sorted(tak_in._allowed_callsigns),
    })


@app.get("/api/audit/summary")
async def api_audit_summary(request: Request):
    """Roll-up of recent audit events for the security panel.

    Merges TAK command log, HMAC rejections, approach arm/abort, and
    strike/drop events that are emitted through the ``hydra.audit``
    logger into one windowed summary.

    Auth-free read. Bounded ring of 500 most recent events.

    Query params:
        window_seconds (int, default 3600, capped at 86400): roll-up
            window for the counts block.
        recent (int, default 50, capped at 200): cap on recent_events.
    """
    try:
        window = int(request.query_params.get("window_seconds", "3600"))
    except (TypeError, ValueError):
        window = 3600
    window = max(1, min(86400, window))
    try:
        recent_limit = int(request.query_params.get("recent", "50"))
    except (TypeError, ValueError):
        recent_limit = 50
    recent_limit = max(0, min(200, recent_limit))

    return JSONResponse(
        _audit_sink.summary(window_seconds=window, recent_limit=recent_limit)
    )


@app.get("/api/rf/ambient_scan")
async def api_rf_ambient_scan():
    """Return the most recent ambient RF samples for the SDR ticker.

    Auth-free read. When no buffer is registered the response has
    ``enabled=False`` and an empty sample set.
    """
    scanner = _rf_ambient_ref
    if scanner is None:
        return JSONResponse({
            "enabled": False,
            "samples": [],
            "window_seconds": 60,
            "max_rssi": None,
        })
    try:
        snapshot = scanner.get_samples()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("rf_ambient.get_samples() failed: %s", exc)
        return JSONResponse({
            "enabled": False,
            "samples": [],
            "window_seconds": 60,
            "max_rssi": None,
        })
    return JSONResponse({"enabled": True, **snapshot})


@app.get("/api/servo/status")
async def api_servo_status():
    """Return the current pan/tilt servo state for the cockpit dial.

    Auth-free read (same-origin bypass). When no controller is registered
    the response has ``enabled=False`` and zeroed angles — the dashboard
    renders that as an idle/off panel.
    """
    servo = _servo_tracker_ref
    if servo is None:
        return JSONResponse({
            "enabled": False,
            "pan_deg": 0.0,
            "tilt_deg": 0.0,
            "pan_limit_min": -90.0,
            "pan_limit_max": 90.0,
            "tilt_limit_min": -30.0,
            "tilt_limit_max": 60.0,
            "scanning": False,
            "locked_track_id": None,
        })
    try:
        return JSONResponse(servo.get_api_status())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("servo.get_api_status() failed: %s", exc)
        return JSONResponse({
            "enabled": False,
            "pan_deg": 0.0,
            "tilt_deg": 0.0,
            "pan_limit_min": -90.0,
            "pan_limit_max": 90.0,
            "tilt_limit_min": -30.0,
            "tilt_limit_max": 60.0,
            "scanning": False,
            "locked_track_id": None,
        })


@app.get("/api/target")
async def api_target_status():
    """Return current target lock state."""
    return stream_state.get_target_lock()


@app.post("/api/target/lock")
async def api_target_lock(request: Request, authorization: Optional[str] = Header(None)):
    """Lock onto a tracked object for keep-in-frame tracking.

    Body: {"track_id": 5}
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
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
    auth_err = _check_auth(authorization, request)
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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
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


# -- Approach mode endpoints (Follow / Drop / Strike continuous) -----------

@app.get("/api/approach/status")
async def api_approach_status():
    """Return current approach controller status."""
    cb = stream_state.get_callback("get_approach_status")
    if cb:
        return cb()
    return {"mode": "idle", "active": False}


@app.post("/api/approach/follow/{track_id}")
async def api_approach_follow(
    track_id: int, request: Request, authorization: Optional[str] = Header(None),
):
    """Start follow mode for a tracked target."""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_follow_command")
    if cb:
        result = cb(track_id)
        if result:
            _audit(request, "approach_follow", target=str(track_id))
            return {"status": "ok", "track_id": track_id, "mode": "follow"}
        _audit(request, "approach_follow", target=str(track_id), outcome="failed")
        return JSONResponse(
            {"error": "Follow failed — track not found or approach already active"},
            status_code=503,
        )
    _audit(request, "approach_follow", outcome="unavailable")
    return JSONResponse({"error": "approach controller not available"}, status_code=503)


@app.post("/api/approach/drop/{track_id}")
async def api_approach_drop(
    track_id: int, request: Request, authorization: Optional[str] = Header(None),
):
    """Start drop approach for a tracked target.

    Body (optional): {"confirm": true}
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    confirm = body.get("confirm", False)
    if not confirm:
        return JSONResponse(
            {"error": "Drop requires explicit confirmation. Set confirm=true."},
            status_code=400,
        )
    cb = stream_state.get_callback("on_drop_command")
    if cb:
        result = cb(track_id)
        if result:
            _audit(request, "approach_drop", target=str(track_id))
            return {"status": "ok", "track_id": track_id, "mode": "drop"}
        _audit(request, "approach_drop", target=str(track_id), outcome="failed")
        return JSONResponse(
            {"error": "Drop failed — track not found, no GPS, or approach already active"},
            status_code=503,
        )
    _audit(request, "approach_drop", outcome="unavailable")
    return JSONResponse({"error": "approach controller not available"}, status_code=503)


@app.post("/api/approach/strike/{track_id}")
async def api_approach_strike(
    track_id: int, request: Request, authorization: Optional[str] = Header(None),
):
    """Start continuous strike approach for a tracked target.

    Body: {"confirm": true}
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    confirm = body.get("confirm", False)
    if not confirm:
        return JSONResponse(
            {"error": "Strike requires explicit confirmation. Set confirm=true."},
            status_code=400,
        )
    cb = stream_state.get_callback("on_approach_strike_command")
    if cb:
        result = cb(track_id)
        if result:
            _audit(request, "approach_strike", target=str(track_id))
            return {"status": "ok", "track_id": track_id, "mode": "strike"}
        _audit(request, "approach_strike", target=str(track_id), outcome="failed")
        return JSONResponse(
            {"error": "Strike failed — track not found or approach already active"},
            status_code=503,
        )
    _audit(request, "approach_strike", outcome="unavailable")
    return JSONResponse({"error": "approach controller not available"}, status_code=503)


@app.post("/api/approach/pixel_lock/{track_id}")
async def api_approach_pixel_lock(
    track_id: int, request: Request, authorization: Optional[str] = Header(None),
):
    """Start pixel-lock visual servoing for a tracked target."""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_pixel_lock_command")
    if cb:
        result = cb(track_id)
        if result:
            _audit(request, "approach_pixel_lock", target=str(track_id))
            return {"status": "ok", "track_id": track_id, "mode": "pixel_lock"}
        _audit(request, "approach_pixel_lock", target=str(track_id), outcome="failed")
        return JSONResponse(
            {"error": "Pixel-lock failed — track not found or approach already active"},
            status_code=503,
        )
    _audit(request, "approach_pixel_lock", outcome="unavailable")
    return JSONResponse({"error": "approach controller not available"}, status_code=503)


@app.post("/api/approach/abort")
async def api_approach_abort(
    request: Request, authorization: Optional[str] = Header(None),
):
    """Abort the current approach mode and safe all channels."""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_approach_abort")
    if cb:
        cb()
        _audit(request, "approach_abort")
        return {"status": "ok"}
    _audit(request, "approach_abort", outcome="unavailable")
    return JSONResponse({"error": "approach controller not available"}, status_code=503)


@app.get("/api/detections")
async def api_recent_detections():
    """Return recent detection log entries."""
    cb = stream_state.get_callback("get_recent_detections")
    if cb:
        return cb()
    return []


@app.get("/api/events")
async def api_events():
    """Get event timeline for the current or most recent mission."""
    cb = stream_state.get_callback("get_events")
    if cb:
        return cb()
    return {"events": []}


@app.get("/api/events/status")
async def api_events_status():
    """Get event logger mission status."""
    cb = stream_state.get_callback("get_event_status")
    if cb:
        return cb()
    return {"mission_active": False, "mission_name": None}


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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    source = body.get("source")
    if source is None:
        return JSONResponse({"error": "source required"}, status_code=400)
    source = str(source)
    if len(source) > 1024:
        return JSONResponse({"error": "source too long"}, status_code=400)

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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    model = body.get("model")
    if not model:
        return JSONResponse({"error": "model name required"}, status_code=400)
    model = str(model)
    if len(model) > 256 or "/" in model or ".." in model:
        return JSONResponse({"error": "invalid model name"}, status_code=400)
    cb = stream_state.get_callback("on_model_switch")
    if cb:
        success = cb(model)
        if success:
            _audit(request, "model_switch", target=model)
            return {"status": "ok", "model": model}
        _audit(request, "model_switch", target=model, outcome="failed")
        return JSONResponse({"error": "Failed to switch model"}, status_code=400)
    return JSONResponse({"error": "Model switching not available"}, status_code=503)


# ── Mission Profiles ──────────────────────────────────────────

@app.get("/api/profiles")
async def api_list_profiles():
    """Return available mission profiles."""
    cb = stream_state.get_callback("get_profiles")
    if cb:
        return cb()
    return {"profiles": [], "active_profile": None}


@app.post("/api/profiles/switch")
async def api_switch_profile(request: Request, authorization: Optional[str] = Header(None)):
    """Switch to a mission profile. Body: {"profile": "counter-uas"}"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    profile_id = body.get("profile")
    if not profile_id:
        return JSONResponse({"error": "profile ID required"}, status_code=400)
    if not isinstance(profile_id, str):
        return JSONResponse({"error": "profile must be a string"}, status_code=400)
    profile_id = profile_id.strip()[:100]
    cb = stream_state.get_callback("on_profile_switch")
    if cb:
        success = cb(profile_id)
        if success:
            _audit(request, "profile_switch", target=profile_id)
            return {"status": "ok", "profile": profile_id}
        _audit(request, "profile_switch", target=profile_id, outcome="failed")
        return JSONResponse(
            {"error": f"Failed to switch to profile '{profile_id}'"},
            status_code=400,
        )
    return JSONResponse({"error": "Profile switching not available"}, status_code=503)


# ── Mission Profile Presets ───────────────────────────────────

@app.get("/api/mission-profiles")
async def api_list_mission_profiles():
    """List available mission profile presets."""
    from hydra_detect.mission_profiles import get_profiles
    profiles = get_profiles()
    return {
        name: {
            "display_name": p.display_name,
            "description": p.description,
            "behavior": p.behavior,
            "approach_method": p.approach_method,
            "post_action": p.post_action,
            "icon": p.icon,
        }
        for name, p in profiles.items()
    }


# ── RF Hunt ─────────────────────────────────────────────────────

@app.get("/api/rf/status")
async def api_rf_status():
    """Return current RF hunt status."""
    cb = stream_state.get_callback("get_rf_status")
    if cb:
        return cb()
    return {"state": "unavailable"}


@app.get("/api/rf/rssi_history")
async def api_rf_rssi_history():
    """Return RSSI history for visualization."""
    cb = stream_state.get_callback("get_rf_rssi_history")
    if cb:
        return cb()
    return []


@app.get("/api/rf/devices")
async def api_rf_devices():
    """Return the current Kismet device feed with ``is_target`` flags.

    Auth-free read — powers the ops dashboard device table. Payload::

        {"mode": "live"|"replay"|"unavailable",
         "devices": [{bssid, ssid, rssi, channel, freq_mhz, manuf,
                      first_seen, last_seen, lat, lon, is_target}, ...]}
    """
    cb = stream_state.get_callback("get_rf_devices")
    if cb:
        try:
            return cb()
        except Exception as exc:  # defensive — keep dashboard alive
            logger.warning("get_rf_devices callback failed: %s", exc)
    return {"mode": "unavailable", "devices": []}


@app.get("/api/rf/events")
async def api_rf_events():
    """Return the RF hunt state-transition ring (last 50)."""
    cb = stream_state.get_callback("get_rf_events")
    if cb:
        try:
            return cb()
        except Exception as exc:
            logger.warning("get_rf_events callback failed: %s", exc)
    return []


@app.post("/api/rf/target")
async def api_rf_target(
    request: Request, authorization: Optional[str] = Header(None),
):
    """Set the hunt target from the device feed — one-click targeting.

    Body: ``{mode?: "wifi"|"sdr", bssid?: str, freq_mhz?: float, confirm: bool}``

    Either ``bssid`` or ``freq_mhz`` must be supplied. ``confirm`` must be
    true — dashboard requires an explicit operator confirmation step.
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse(
            {"error": "Invalid or missing JSON body"}, status_code=400,
        )
    if not body.get("confirm"):
        return JSONResponse(
            {"error": "confirm=true required to set hunt target"},
            status_code=400,
        )
    bssid = (body.get("bssid") or "").strip()
    freq_mhz = body.get("freq_mhz")
    mode = body.get("mode")
    if not bssid and freq_mhz is None:
        return JSONResponse(
            {"error": "bssid or freq_mhz required"}, status_code=400,
        )
    if mode and mode not in ("wifi", "sdr"):
        return JSONResponse(
            {"error": "mode must be 'wifi' or 'sdr'"}, status_code=400,
        )
    if bssid and not BSSID_RE.fullmatch(bssid):
        return JSONResponse(
            {"error": "bssid must be MAC format AA:BB:CC:DD:EE:FF"},
            status_code=400,
        )
    if freq_mhz is not None:
        try:
            freq_mhz = float(freq_mhz)
            if not (1.0 <= freq_mhz <= 6000.0):
                return JSONResponse(
                    {"error": "freq_mhz must be 1-6000"}, status_code=400,
                )
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "freq_mhz must be a number"}, status_code=400,
            )
    params: dict = {}
    if mode:
        params["mode"] = mode
    if bssid:
        params["bssid"] = bssid.upper()
    if freq_mhz is not None:
        params["freq_mhz"] = freq_mhz
    cb = stream_state.get_callback("on_rf_target")
    if cb:
        ok = cb(params)
        target_label = bssid or (f"{freq_mhz}MHz" if freq_mhz else "?")
        if ok:
            _audit(request, "rf_hunt_target", target=target_label)
            return {"status": "ok", "message": "RF hunt target set"}
        _audit(request, "rf_hunt_target", target=target_label, outcome="failed")
        return JSONResponse(
            {"error": "Failed to set RF hunt target"}, status_code=503,
        )
    _audit(request, "rf_hunt_target", outcome="unavailable")
    return JSONResponse(
        {"error": "RF homing not configured"}, status_code=503,
    )


@app.post("/api/rf/start")
async def api_rf_start(request: Request, authorization: Optional[str] = Header(None)):
    """Start an RF hunt with the given parameters.

    Body: {mode, target_bssid, target_freq_mhz, search_pattern,
           search_area_m, search_spacing_m, search_alt_m,
           rssi_threshold_dbm, rssi_converge_dbm, gradient_step_m}
    All fields optional — unset fields keep current config values.
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)

    # Validate mode
    mode = body.get("mode")
    if mode and mode not in ("wifi", "sdr"):
        return JSONResponse({"error": "mode must be 'wifi' or 'sdr'"}, status_code=400)

    # Validate BSSID format if provided
    bssid = body.get("target_bssid", "").strip()
    if mode == "wifi" and not bssid:
        return JSONResponse({"error": "target_bssid required for wifi mode"}, status_code=400)
    if bssid and not BSSID_RE.fullmatch(bssid):
        return JSONResponse(
            {"error": "target_bssid must be MAC format AA:BB:CC:DD:EE:FF"},
            status_code=400,
        )

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
        return JSONResponse(
            {"error": "search_pattern must be 'lawnmower' or 'spiral'"},
            status_code=400,
        )

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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_rf_stop")
    if cb:
        cb()
        _audit(request, "rf_hunt_stop")
        return {"status": "ok", "message": "RF hunt stopped"}
    _audit(request, "rf_hunt_stop", outcome="unavailable")
    return JSONResponse({"error": "RF homing not configured"}, status_code=503)


# ── RTSP ─────────────────────────────────────────────────────

@app.get("/api/rtsp/status")
async def api_rtsp_status():
    """Return RTSP server status."""
    cb = stream_state.get_callback("get_rtsp_status")
    if cb:
        return cb()
    return {"enabled": False, "running": False, "url": "", "clients": 0}


@app.post("/api/rtsp/toggle")
async def api_rtsp_toggle(request: Request, authorization: Optional[str] = Header(None)):
    """Start or stop the RTSP server at runtime. Body: {"enabled": true/false}"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "enabled field required (true/false)"}, status_code=400)
    cb = stream_state.get_callback("on_rtsp_toggle")
    if cb:
        result = cb(bool(enabled))
        _audit(request, "rtsp_toggle", target=str(enabled))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    _audit(request, "rtsp_toggle", outcome="unavailable")
    return JSONResponse({"error": "RTSP toggle not available"}, status_code=503)


# ── MAVLink Video ────────────────────────────────────────────

@app.get("/api/mavlink-video/status")
async def api_mavlink_video_status():
    """Return MAVLink video thumbnail stream status."""
    cb = stream_state.get_callback("get_mavlink_video_status")
    if cb:
        return cb()
    return {"enabled": False, "running": False, "width": 0, "height": 0,
            "quality": 0, "current_fps": 0, "bytes_per_sec": 0}


@app.post("/api/mavlink-video/toggle")
async def api_mavlink_video_toggle(request: Request, authorization: Optional[str] = Header(None)):
    """Start or stop MAVLink video. Body: {"enabled": true/false}"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "enabled field required"}, status_code=400)
    cb = stream_state.get_callback("on_mavlink_video_toggle")
    if cb:
        result = cb(bool(enabled))
        _audit(request, "mavlink_video_toggle", target=str(enabled))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    return JSONResponse({"error": "MAVLink video not available"}, status_code=503)


# ── TAK/ATAK CoT Output ─────────────────────────────────────

@app.get("/api/tak/status")
async def api_tak_status():
    """Return TAK CoT output status."""
    cb = stream_state.get_callback("get_tak_status")
    if cb:
        return cb()
    return {"enabled": False, "running": False, "callsign": "", "events_sent": 0}


@app.post("/api/tak/toggle")
async def api_tak_toggle(request: Request, authorization: Optional[str] = Header(None)):
    """Start or stop TAK CoT output. Body: {"enabled": true/false}"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "enabled field required"}, status_code=400)
    cb = stream_state.get_callback("on_tak_toggle")
    if cb:
        result = cb(bool(enabled))
        _audit(request, "tak_toggle", target=str(enabled))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    return JSONResponse({"error": "TAK output not available"}, status_code=503)


@app.get("/api/stream/quality")
async def get_stream_quality():
    """Return current MJPEG stream quality."""
    return {"quality": stream_state.get_mjpeg_quality()}


@app.post("/api/stream/quality")
async def set_stream_quality(request: Request):
    """Set stream JPEG quality at runtime. Body: {"quality": 70}

    No auth required — this is a display preference, not a control action.
    """
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    quality = body.get("quality", 70)
    try:
        quality = int(quality)
    except (TypeError, ValueError):
        return JSONResponse({"error": "quality must be an integer 1-100"}, status_code=400)
    quality = max(1, min(100, quality))
    stream_state.set_mjpeg_quality(quality)
    _audit(request, "set_stream_quality", target=str(quality))
    return {"quality": quality}


@app.post("/api/restart")
async def restart_pipeline(request: Request, authorization: Optional[str] = Header(None)):
    """Request a pipeline restart. Briefly interrupts detection."""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_restart_command")
    if cb:
        cb()
        _audit(request, "pipeline_restart")
        return {"status": "restarting"}
    _audit(request, "pipeline_restart", outcome="unavailable")
    return JSONResponse({"error": "restart not available"}, status_code=503)


# ── Autonomy dashboard ────────────────────────────────────────────────

_AUTONOMY_MODES = ("dryrun", "shadow", "live")


def _autonomy_default_snapshot(callsign: str) -> dict:
    """Idle/default status shape returned when no controller is registered."""
    return {
        "mode": "dryrun",
        "enabled": False,
        "callsign": callsign,
        "geofence": {
            "shape": "CIRCLE",
            "radius_m": 0.0,
            "center_lat": 0.0,
            "center_lon": 0.0,
            "polygon": "",
        },
        "self_position": None,
        "criteria": {
            "min_confidence": 0.85,
            "min_track_frames": 5,
            "strike_cooldown_sec": 30.0,
            "gps_max_stale_sec": 2.0,
            "require_operator_lock": True,
            "allowed_vehicle_modes": "AUTO",
            "allowed_classes": [],
        },
        "gates": [
            {"id": "geofence", "state": "N/A", "detail": ""},
            {"id": "vehicle_mode", "state": "N/A", "detail": ""},
            {"id": "operator_lock", "state": "N/A", "detail": ""},
            {"id": "gps_fresh", "state": "N/A", "detail": ""},
            {"id": "cooldown", "state": "N/A", "detail": ""},
        ],
        "log": [],
    }


@app.get("/api/autonomy/status")
async def api_autonomy_status():
    """Return autonomy gate + explainability snapshot.

    Auth-free read (same precedent as /api/stats). Powers the autonomy
    dashboard (now embedded inside #config): mode picker, gate panel,
    and the rolling decision log.
    Returns an idle default shape when no controller is registered so the
    dashboard can render on a cold boot.
    """
    callsign = str(stream_state.get_stats().get("callsign") or "HYDRA-1")
    ctrl = _autonomous_ref
    if ctrl is None:
        return _autonomy_default_snapshot(callsign)
    try:
        return ctrl.get_dashboard_snapshot(callsign=callsign)
    except Exception as exc:
        logger.warning("autonomy snapshot failed: %s", exc)
        return _autonomy_default_snapshot(callsign)


@app.post("/api/autonomy/mode")
async def api_autonomy_mode(
    request: Request, authorization: Optional[str] = Header(None),
):
    """Set the autonomy mode. Body: {"mode": "dryrun" | "shadow" | "live"}.

    Bearer auth required — this is a safety-critical write.
    """
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    mode = body.get("mode")
    if not isinstance(mode, str) or mode not in _AUTONOMY_MODES:
        return JSONResponse(
            {"error": f"mode must be one of {list(_AUTONOMY_MODES)}"},
            status_code=400,
        )
    ctrl = _autonomous_ref
    if ctrl is None:
        _audit(request, "autonomy_mode", target=mode, outcome="unavailable")
        return JSONResponse(
            {"error": "autonomous controller not available"}, status_code=503,
        )
    try:
        ctrl.set_mode(mode)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(request, "autonomy_mode", target=mode)
    return {"status": "ok", "mode": mode}


@app.post("/api/vehicle/beep")
async def api_vehicle_beep(request: Request):
    """Play a tune on the Pixhawk buzzer. Body: {"tune": "alert"}

    No auth required — this is a fun/debug feature, not a control action.
    Valid tune names: alert, success, warning, error, charles, startup.
    Or pass a raw QBASIC tune string.
    """
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    tune = str(body.get("tune", "alert"))
    if len(tune) > 100:
        return JSONResponse({"error": "tune too long"}, status_code=400)
    cb = stream_state.get_callback("play_tune")
    if cb:
        result = cb(tune)
        return {"status": "ok" if result else "failed", "tune": tune}
    return JSONResponse({"error": "MAVLink not connected"}, status_code=503)


@app.get("/api/tak/targets")
async def api_get_tak_targets():
    """List current TAK unicast targets."""
    cb = stream_state.get_callback("get_tak_targets")
    if cb:
        return {"targets": cb()}
    return {"targets": []}


@app.post("/api/tak/targets")
async def api_add_tak_target(
    request: Request, authorization: Optional[str] = Header(None),
):
    """Add a TAK unicast target. Body: {"host": "ip", "port": 6969}"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    host = str(body.get("host", "")).strip()
    if not host or len(host) > 256:
        return JSONResponse({"error": "valid host required"}, status_code=400)
    try:
        port = int(body.get("port", 6969))
    except (TypeError, ValueError):
        return JSONResponse({"error": "port must be a number"}, status_code=400)
    if not (1 <= port <= 65535):
        return JSONResponse({"error": "port must be 1-65535"}, status_code=400)
    cb = stream_state.get_callback("add_tak_target")
    if cb:
        cb(host, port)
        _audit(request, "add_tak_target", target=f"{host}:{port}")
        return {"status": "added", "host": host, "port": port}
    return JSONResponse({"error": "TAK not available"}, status_code=503)


@app.delete("/api/tak/targets")
async def api_remove_tak_target(
    request: Request, authorization: Optional[str] = Header(None),
):
    """Remove a TAK unicast target. Body: {"host": "ip", "port": 6969}"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    host = str(body.get("host", "")).strip()
    if not host:
        return JSONResponse({"error": "host required"}, status_code=400)
    try:
        port = int(body.get("port", 6969))
    except (TypeError, ValueError):
        return JSONResponse({"error": "port must be a number"}, status_code=400)
    if not (1 <= port <= 65535):
        return JSONResponse({"error": "port must be 1-65535"}, status_code=400)
    cb = stream_state.get_callback("remove_tak_target")
    if cb:
        cb(host, port)
        _audit(request, "remove_tak_target", target=f"{host}:{port}")
        return {"status": "removed"}
    return JSONResponse({"error": "TAK not available"}, status_code=503)


@app.post("/api/mavlink-video/tune")
async def api_mavlink_video_tune(request: Request, authorization: Optional[str] = Header(None)):
    """Live-tune MAVLink video params. Body: {width, height, quality, max_fps} (all optional)"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    for field, lo, hi in [("width", 40, 320), ("height", 30, 240),
                          ("quality", 5, 50), ("max_fps", 0.1, 5.0)]:
        val = body.get(field)
        if val is not None:
            try:
                val = float(val) if field == "max_fps" else int(val)
                if not (lo <= val <= hi):
                    return JSONResponse({"error": f"{field} must be {lo}-{hi}"}, status_code=400)
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{field} must be a number"}, status_code=400)
    cb = stream_state.get_callback("on_mavlink_video_tune")
    if cb:
        result = cb(body)
        _audit(request, "mavlink_video_tune", target=str(body))
        if result.get("status") == "ok":
            return result
        return JSONResponse(result, status_code=500)
    return JSONResponse({"error": "MAVLink video not available"}, status_code=503)


@app.post("/api/pipeline/stop")
async def api_pipeline_stop(request: Request, authorization: Optional[str] = Header(None)):
    """Gracefully stop the pipeline and shut down."""
    auth_err = _check_auth(authorization, request)
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
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
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


# ── Operator Control (mobile) ─────────────────────────────────────

@app.get("/control", response_class=HTMLResponse)
async def control_page(request: Request):
    """Serve the mobile operator control page."""
    return templates.TemplateResponse(request, "control.html")


# ── Instructor Overview ────────────────────────────────────────────

@app.get("/instructor", response_class=HTMLResponse)
async def instructor_page(request: Request):
    """Serve the standalone instructor overview page."""
    return templates.TemplateResponse(request, "instructor.html")


@app.post("/api/abort")
async def api_abort(request: Request):
    """Emergency abort — switch vehicle to RTL mode.

    This endpoint is intentionally unauthenticated. The instructor page
    is the safety exception: an instructor must be able to abort any
    vehicle without configuring tokens.
    """
    _audit(request, "abort")
    # Try RTL first, then LOITER/HOLD as fallback.
    # This is safety-critical — wrap in try/except so a callback crash
    # never prevents the instructor from seeing an error response.
    cb = stream_state.get_callback("on_set_mode_command")
    if cb:
        for mode in ("RTL", "LOITER", "HOLD"):
            try:
                if cb(mode):
                    logger.warning("ABORT: vehicle set to %s by instructor", mode)
                    return {"status": "ok", "mode": mode}
            except Exception as exc:
                logger.error("ABORT callback failed for %s: %s", mode, exc)
        return JSONResponse({"error": "Failed to set abort mode"}, status_code=503)
    return JSONResponse({"error": "MAVLink not connected"}, status_code=503)


# ── Mission Tagging ────────────────────────────────────────────────

@app.post("/api/mission/start")
async def api_start_mission(request: Request, authorization: Optional[str] = Header(None)):
    """Start a named mission. Body: {"name": "mission-alpha"}"""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    body = await _parse_json(request)
    if body is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    name = body.get("name", f"mission-{int(time.time())}")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name must be a non-empty string"}, status_code=400)
    name = name.strip()[:100]  # Bound length
    cb = stream_state.get_callback("on_mission_start")
    if cb:
        cb(name)
    _audit(request, "mission_start", target=name)
    return {"status": "started", "name": name}


@app.post("/api/mission/end")
async def api_end_mission(request: Request, authorization: Optional[str] = Header(None)):
    """End the current mission."""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    cb = stream_state.get_callback("on_mission_end")
    if cb:
        cb()
    _audit(request, "mission_end")
    return {"status": "ended"}


# ── Mission Review ────────────────────────────────────────────────

@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    """Serve the post-mission review page."""
    return templates.TemplateResponse(request, "review.html")


@app.get("/api/review/logs")
async def api_review_logs():
    """List available detection log files and event timeline files."""
    import json as _json

    cb = stream_state.get_callback("get_log_dir")
    log_dir = cb() if cb else "/data/logs"
    image_dir_cb = stream_state.get_callback("get_image_dir")
    image_dir = image_dir_cb() if image_dir_cb else "/data/images"
    result = []
    event_logs = []
    log_path = Path(log_dir)
    if log_path.is_dir():
        for f in sorted(log_path.iterdir(), reverse=True):
            if f.suffix in (".jsonl", ".csv"):
                result.append({
                    "filename": f.name,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "modified": f.stat().st_mtime,
                })
        # Scan for event timeline JSONL files
        for f in sorted(log_path.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(f) as fh:
                    first_line = fh.readline().strip()
                    if first_line:
                        record = _json.loads(first_line)
                        if record.get("type") in ("mission_start", "track", "action", "state"):
                            event_logs.append({
                                "filename": f.name,
                                "size_kb": round(f.stat().st_size / 1024, 1),
                            })
            except (_json.JSONDecodeError, OSError, UnicodeDecodeError):
                logger.debug("Skipping unreadable event log: %s", f.name)
                continue
    return {"logs": result, "event_logs": event_logs, "image_dir": image_dir}


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
    max_records = 50000  # Cap to prevent OOM on large log files
    if path.suffix == ".jsonl":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        continue
                    if len(records) >= max_records:
                        break
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
                if len(records) >= max_records:
                    break

    return {"filename": filename, "count": len(records), "detections": records,
            "truncated": len(records) >= max_records}


@app.get("/api/review/events/{filename}")
async def api_review_events(filename: str):
    """Return events from an event timeline JSONL file."""
    import json as _json
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)

    cb = stream_state.get_callback("get_log_dir")
    log_dir = Path(cb() if cb else "/data/logs")
    filepath = log_dir / filename

    if not filepath.exists() or not filepath.suffix == ".jsonl":
        return JSONResponse({"error": "not found"}, status_code=404)

    events: list = []
    max_events = 50000
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        continue
                    if len(events) >= max_events:
                        break
    except (_json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.error("Failed to read event log %s: %s", filename, exc)
        return JSONResponse({"error": "read error"}, status_code=500)

    return {"events": events, "filename": filename}


@app.get("/api/logs")
async def api_app_logs(lines: int = 50, level: str = "INFO"):
    """Tail the application log file for remote debugging."""
    import re
    from collections import deque

    lines = max(1, min(lines, 500))
    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    min_ord = level_order.get(level.upper(), 1)

    log_re = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
        r"\[([^\]]+)\] "
        r"(\w+): "
        r"(.*)$"
    )

    # Find the log file
    cb = stream_state.get_callback("get_log_dir")
    log_dir = cb() if cb else "/data/logs"
    log_path = Path(log_dir) / "hydra.log"

    if not log_path.exists():
        return []

    result = deque(maxlen=lines)
    try:
        with open(log_path, "r") as f:
            for raw_line in f:
                m = log_re.match(raw_line.strip())
                if m:
                    entry_level = m.group(3)
                    if level_order.get(entry_level, 0) >= min_ord:
                        result.append({
                            "timestamp": m.group(1),
                            "level": entry_level,
                            "module": m.group(2),
                            "message": m.group(4),
                        })
                elif raw_line.strip():
                    result.append({
                        "timestamp": "",
                        "level": "RAW",
                        "module": "",
                        "message": raw_line.strip(),
                    })
    except OSError:
        return []

    return list(result)


@app.get("/api/export")
async def api_export_logs(request: Request, authorization: str | None = Header(None)):
    """Export current session logs + images as a ZIP download (streamed from disk)."""
    import tempfile
    import zipfile

    from fastapi.responses import FileResponse

    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err

    log_dir_cb = stream_state.get_callback("get_log_dir")
    log_dir = log_dir_cb() if log_dir_cb else "./output_data/logs"
    image_dir_cb = stream_state.get_callback("get_image_dir")
    image_dir = image_dir_cb() if image_dir_cb else "./output_data/images"

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for dir_path, dir_name in [(log_dir, "logs"), (image_dir, "images")]:
                p = Path(dir_path)
                if p.exists():
                    for f in p.rglob("*"):
                        if f.is_file() and not f.is_symlink():
                            zf.write(f, f"{dir_name}/{f.relative_to(p)}")
        tmp_path = tmp.name
    finally:
        tmp.close()

    from starlette.background import BackgroundTask

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename="hydra-export.zip",
        background=BackgroundTask(lambda: Path(tmp_path).unlink(missing_ok=True)),
    )


@app.get("/api/export/waypoints")
async def api_export_waypoints(
    request: Request,
    classes: str = "",
    alt_m: float = 15.0,
    authorization: str | None = Header(None),
):
    """Export GPS-tagged detections as a QGC WPL 110 waypoint file.

    Query params:
        classes: comma-separated class filter (e.g. ?classes=person,car)
        alt_m: waypoint altitude in meters (default 15)
    """
    from hydra_detect.waypoint_export import (
        deduplicate,
        format_wpl,
        tracks_to_waypoints,
    )

    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err

    cb = stream_state.get_callback("get_recent_detections")
    detections = cb() if cb else []
    if not detections:
        return JSONResponse({"error": "No detections available"}, status_code=404)

    class_filter: set[str] | None = None
    if classes.strip():
        class_filter = {c.strip() for c in classes.split(",") if c.strip()}

    waypoints = tracks_to_waypoints(detections, alt_m=alt_m, classes=class_filter)
    if not waypoints:
        return JSONResponse(
            {"error": "No GPS-tagged detections found (need GPS fix)"},
            status_code=404,
        )
    waypoints = deduplicate(waypoints)

    # Home position: use first detection with GPS, or vehicle stats position
    home_lat = waypoints[0].lat
    home_lon = waypoints[0].lon

    content = format_wpl(waypoints, home_lat, home_lon,
                         home_alt=0.0, loiter_sec=5.0)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="hydra-waypoints.wpl"'},
    )


@app.get("/api/review/waypoints/{filename}")
async def api_review_waypoints(
    filename: str, classes: str = "", alt_m: float = 15.0,
):
    """Export waypoints from a saved detection log file.

    Query params:
        classes: comma-separated class filter (e.g. ?classes=person,car)
        alt_m: waypoint altitude in meters (default 15)
    """
    import json as _json

    from hydra_detect.waypoint_export import (
        deduplicate,
        format_wpl,
        tracks_to_waypoints,
    )

    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    cb = stream_state.get_callback("get_log_dir")
    log_dir = cb() if cb else "/data/logs"
    path = Path(log_dir) / filename

    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "Log file not found"}, status_code=404)

    records: list[dict] = []
    max_records = 50000
    if path.suffix == ".jsonl":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        continue
                    if len(records) >= max_records:
                        break
    elif path.suffix == ".csv":
        import csv as _csv
        with open(path) as f:
            reader = _csv.DictReader(f)
            for row in reader:
                for key in ("confidence", "lat", "lon", "alt"):
                    if key in row and row[key]:
                        try:
                            row[key] = float(row[key])
                        except ValueError:
                            pass
                records.append(row)
                if len(records) >= max_records:
                    break
    else:
        return JSONResponse({"error": "Unsupported file type"}, status_code=400)

    if not records:
        return JSONResponse({"error": "No records in log file"}, status_code=404)

    class_filter: set[str] | None = None
    if classes.strip():
        class_filter = {c.strip() for c in classes.split(",") if c.strip()}

    waypoints = tracks_to_waypoints(records, alt_m=alt_m, classes=class_filter)
    if not waypoints:
        return JSONResponse(
            {"error": "No GPS-tagged detections found in log"},
            status_code=404,
        )
    waypoints = deduplicate(waypoints)

    # Home position from first GPS-tagged record
    home_lat = waypoints[0].lat
    home_lon = waypoints[0].lon

    content = format_wpl(waypoints, home_lat, home_lon,
                         home_alt=0.0, loiter_sec=5.0)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="hydra-waypoints-{path.stem}.wpl"'},
    )


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


# Cached snapshot to avoid re-encoding the same frame on rapid polls.
_snapshot_cache: dict[str, Any] = {"bytes": b"", "ts": 0.0, "quality": 0}
_raw_snapshot_cache: dict[str, Any] = {"bytes": b"", "ts": 0.0, "quality": 0}
_SNAPSHOT_TTL = 0.033  # 30 fps cap — serve cached JPEG if <33ms old


@app.get("/stream.jpg")
async def snapshot_frame(request: Request):
    """Single JPEG frame snapshot — polled by the dashboard as a fallback
    for browsers/middleware stacks where MJPEG streaming hangs.

    Pass ?raw=1 to get the un-annotated frame (no overlay bounding boxes).
    The Ops HUD uses this so its canvas-drawn boxes don't double up with
    the server-side overlay.
    """
    use_raw = request.query_params.get("raw") == "1"
    cache = _raw_snapshot_cache if use_raw else _snapshot_cache

    now = time.monotonic()
    if now - cache["ts"] < _SNAPSHOT_TTL and cache["bytes"]:
        return Response(content=cache["bytes"], media_type="image/jpeg")
    frame = stream_state.get_raw_frame() if use_raw else stream_state.get_frame()
    if frame is None:
        return Response(status_code=204)
    quality = stream_state.get_mjpeg_quality()
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return Response(status_code=204)
    jpeg_bytes = buf.tobytes()
    cache["bytes"] = jpeg_bytes
    cache["ts"] = now
    cache["quality"] = quality
    return Response(content=jpeg_bytes, media_type="image/jpeg")


async def _generate_mjpeg():
    """Async generator that yields JPEG frames.

    Polls for new frames at ~30 fps. Frame storage is protected by
    threading.Lock inside StreamState, so this is safe across threads.
    """
    try:
        while True:
            frame = stream_state.get_frame()
            if frame is not None:
                quality = stream_state.get_mjpeg_quality()
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

@app.get("/api/config/effective")
async def api_get_effective_config():
    """Return the effective (post-profile-merge) configuration state.

    Includes the active vehicle profile name and the runtime config values
    that reflect any [vehicle.<name>] overrides applied at startup.

    No auth required — read-only, no sensitive data beyond what /api/config
    already exposes.
    """
    rc = stream_state.get_runtime_config()
    return {
        "vehicle_profile": rc.get("vehicle_profile"),
        "runtime_config": rc,
    }


@app.get("/api/config/full")
async def api_get_full_config():
    """Return all config.ini sections as JSON. Sensitive fields are redacted.

    No auth required — this is read-only and sensitive values (api_token,
    kismet_pass) are already redacted by read_config(). Auth is only
    enforced on the POST variant that writes config changes.
    """
    try:
        return read_config()
    except Exception as e:
        logger.error("Failed to read config: %s", e)
        return JSONResponse({"error": "Failed to read configuration"}, status_code=500)


@app.get("/api/config/schema")
async def api_get_config_schema():
    """Return config schema metadata for UI control generation.

    No auth required — read-only metadata describing field types, ranges,
    choices, and defaults. Used by settings.js to render sliders and
    dropdowns instead of plain text inputs.
    """
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for section, fields in CONFIG_SCHEMA.items():
        section_schema: dict[str, dict[str, Any]] = {}
        for key, spec in fields.items():
            section_schema[key] = {
                "type": spec.type.value,
                "min": spec.min_val,
                "max": spec.max_val,
                "choices": spec.choices,
                "default": spec.default,
                "description": spec.description,
            }
        result[section] = section_schema
    return result


@app.post("/api/config/full")
async def api_set_full_config(request: Request, authorization: str | None = Header(None)):
    """Update config.ini fields. Returns list of fields requiring restart."""
    auth_err = _check_auth(authorization, request)
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
    field_errors = validate_config_updates(body)
    if field_errors:
        return JSONResponse(
            {"error": "Validation failed", "field_errors": field_errors},
            status_code=400,
        )
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
    auth_err = _check_auth(authorization, request)
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


@app.post("/api/config/factory-reset")
async def api_factory_reset(request: Request, authorization: str | None = Header(None)):
    """Restore factory defaults from config.ini.factory."""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    if not has_factory():
        return JSONResponse({"error": "No factory defaults available"}, status_code=404)
    try:
        restore_factory()
        _audit(request, "config_factory_reset")
        # Trigger pipeline restart
        cb = stream_state.get_callback("on_restart_command")
        if cb:
            cb()
        return {"status": "ok", "message": "Factory defaults restored"}
    except Exception as e:
        logger.error("Failed to restore factory defaults: %s", e)
        return JSONResponse({"error": f"Failed to restore: {e}"}, status_code=500)


@app.get("/api/config/export")
async def api_config_export(request: Request, authorization: str | None = Header(None)):
    """Export current config as JSON download."""
    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err
    try:
        config = read_config()
        _audit(request, "config_export")
        return config
    except Exception as e:
        logger.error("Failed to export config: %s", e)
        return JSONResponse({"error": "Failed to export configuration"}, status_code=500)


@app.post("/api/config/import")
async def api_config_import(request: Request, authorization: str | None = Header(None)):
    """Import config from uploaded JSON."""
    auth_err = _check_auth(authorization, request)
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
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected JSON object with config sections"}, status_code=400)
    field_errors = validate_config_updates(body)
    if field_errors:
        return JSONResponse(
            {"error": "Validation failed", "field_errors": field_errors},
            status_code=400,
        )
    try:
        result = write_config(body)
        _audit(request, "config_import", target=str(len(body)))
        return {"status": "imported", **result}
    except Exception as e:
        logger.error("Failed to import config: %s", e)
        return JSONResponse({"error": f"Failed to import configuration: {e}"}, status_code=500)


# ── Setup Wizard ─────────────────────────────────────────────────────

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """Serve the first-boot setup wizard page."""
    return templates.TemplateResponse(request, "setup.html")


@app.get("/api/setup/devices")
async def api_setup_devices():
    """List available cameras and serial ports for setup wizard."""
    import glob
    cameras = []
    serial_ports = []

    # Detect V4L2 cameras
    for dev in sorted(glob.glob("/dev/video*")):
        cameras.append({"path": dev, "name": dev})

    # Detect serial ports (potential Pixhawk connections)
    for dev in sorted(glob.glob("/dev/tty*")):
        if any(prefix in dev for prefix in ["ttyACM", "ttyUSB", "ttyTHS", "ttyAMA"]):
            serial_ports.append({"path": dev, "name": dev})

    return {"cameras": cameras, "serial_ports": serial_ports}


@app.post("/api/setup/save")
async def api_setup_save(request: Request, authorization: Optional[str] = Header(None)):
    """Save setup wizard configuration and trigger restart.

    Auth is enforced when a token is configured (post-first-boot).
    On first boot (no token), the setup wizard works without auth.
    """
    auth_err = _check_auth(authorization, request)
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

    camera_source = body.get("camera_source", "auto")
    serial_port = body.get("serial_port", "/dev/ttyTHS1")
    vehicle_type = body.get("vehicle_type", "")
    team_number = body.get("team_number", "")
    callsign = body.get("callsign", "")

    # Validate field types before length checks
    for field in [camera_source, serial_port, vehicle_type, team_number, callsign]:
        if not isinstance(field, str):
            return JSONResponse({"error": "All fields must be strings"}, status_code=400)

    # Validate inputs — bounded lengths
    if len(camera_source) > 200:
        return JSONResponse({"error": "camera_source too long"}, status_code=400)
    if len(serial_port) > 200:
        return JSONResponse({"error": "serial_port too long"}, status_code=400)
    if len(vehicle_type) > 20:
        return JSONResponse({"error": "vehicle_type too long"}, status_code=400)
    if len(team_number) > 20:
        return JSONResponse({"error": "team_number too long"}, status_code=400)
    if len(callsign) > 50:
        return JSONResponse({"error": "callsign too long"}, status_code=400)
    if vehicle_type and vehicle_type not in ("drone", "usv", "ugv", "fw"):
        return JSONResponse(
            {"error": "vehicle_type must be drone, usv, ugv, or fw"},
            status_code=400,
        )

    # Build callsign from team + vehicle if not explicitly set
    if not callsign and team_number and vehicle_type:
        callsign = f"HYDRA-{team_number}-{vehicle_type.upper()}"

    # Write to config
    updates: dict[str, dict[str, str]] = {
        "camera": {"source": camera_source},
        "mavlink": {"connection_string": serial_port},
    }
    if callsign:
        updates["tak"] = {"callsign": callsign}

    field_errors = validate_config_updates(updates)
    if field_errors:
        return JSONResponse(
            {"error": "Validation failed", "field_errors": field_errors},
            status_code=400,
        )

    try:
        result = write_config(updates)
    except Exception as e:
        logger.error("Setup save failed: %s", e)
        return JSONResponse({"error": f"Failed to save: {e}"}, status_code=500)

    _audit(request, "setup_save", target=callsign or "no-callsign")

    # Trigger pipeline restart
    cb = stream_state.get_callback("on_restart_command")
    if cb:
        cb()

    return {"status": "saved", "callsign": callsign, **result}


# ── Server launcher ──────────────────────────────────────────────────

def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> threading.Thread:
    """Start uvicorn in a daemon thread and return the thread handle."""
    import uvicorn

    kwargs: dict[str, Any] = {"host": host, "port": port, "log_level": "warning"}
    if ssl_certfile and ssl_keyfile:
        kwargs["ssl_certfile"] = ssl_certfile
        kwargs["ssl_keyfile"] = ssl_keyfile

    def _run():
        uvicorn.run(app, **kwargs)

    t = threading.Thread(target=_run, daemon=True, name="hydra-web")
    t.start()
    scheme = "https" if ssl_certfile else "http"
    logger.info("Web UI started at %s://%s:%d", scheme, host, port)
    return t
