"""Operating mode API — GET/POST /api/mode.

New APIRouter included in server.py via a single include_router() call.
Auth pattern matches existing control endpoints: _check_auth() for POST,
no auth for GET (read-only, needed by dashboard poll).
"""

from __future__ import annotations

import configparser
import logging
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from hydra_detect.operating_mode import (
    ModeTransitionError,
    OperatingMode,
    current_mode,
    set_mode,
)
from hydra_detect.web.config_api import get_config_path

logger = logging.getLogger(__name__)

router = APIRouter()


class ModeRequest(BaseModel):
    """Body for POST /api/mode."""
    mode: OperatingMode
    reason: str = ""
    confirm: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def normalise_mode(cls, v: str) -> str:
        if isinstance(v, str):
            return v.upper()
        return v


def _read_current_mode() -> OperatingMode:
    """Read mode from config.ini on disk."""
    path = get_config_path()
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(path)
    return current_mode(cfg)


@router.get("/api/mode")
async def api_get_mode():
    """Return the current operating mode.

    No auth required — read-only, polled by the dashboard on the same
    cadence as /api/stats.
    """
    mode = _read_current_mode()
    return {"mode": mode.value}


@router.post("/api/mode")
async def api_set_mode(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Transition to a new operating mode.

    Body: {"mode": "FIELD", "reason": "pre-sortie", "confirm": true}

    ARMED requires confirm=true AND a non-empty reason string.
    All other modes accept confirm in any state.

    Auth: same-origin requests (dashboard) bypass Bearer token.
    External callers (curl/scripts) require Authorization: Bearer <token>.
    """
    # Lazy import to avoid circular dependency at module level.
    from hydra_detect.web.server import _check_auth, _audit, _parse_json

    auth_err = _check_auth(authorization, request)
    if auth_err:
        return auth_err

    raw = await _parse_json(request)
    if raw is None:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)

    # Validate body via Pydantic — unknown mode → 422
    try:
        body = ModeRequest(**raw)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    target_mode = body.mode

    # ARMED needs confirm=True + non-empty reason.
    if target_mode is OperatingMode.ARMED:
        if not body.confirm:
            return JSONResponse(
                {"error": "Mode: ARMED requires confirm=true."},
                status_code=400,
            )
        if not body.reason.strip():
            return JSONResponse(
                {"error": "Mode: ARMED requires a reason."},
                status_code=400,
            )

    path = get_config_path()
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(path)

    try:
        set_mode(
            cfg,
            target_mode,
            reason=body.reason,
            confirmed_twice=body.confirm,
            actor="api",
        )
    except ModeTransitionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.error("Mode transition failed: %s", exc)
        return JSONResponse({"error": "Mode transition failed."}, status_code=500)

    _audit(request, "mode_set", target=target_mode.value)
    return {"mode": target_mode.value, "reason": body.reason}
