"""Shared FastAPI dependencies and wrappers for web routes."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from hydra_detect.web.config_api import MAX_BODY_SIZE


def require_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse | None:
    """Dependency wrapper for token/session auth checks."""
    from hydra_detect.web import server

    return server._check_auth(authorization, request)


async def request_size_guard(request: Request) -> JSONResponse | None:
    """Reject overly large request bodies consistently."""
    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        return JSONResponse({"error": "Request body too large"}, status_code=413)
    return None


async def parse_json_with_size_guard(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Parse JSON payload after enforcing MAX_BODY_SIZE."""
    size_err = await request_size_guard(request)
    if size_err:
        return None, size_err
    try:
        body = json.loads(await request.body())
    except (ValueError, json.JSONDecodeError):
        return None, JSONResponse({"error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return None, JSONResponse({"error": "Expected JSON object"}, status_code=400)
    return body, None


def audit_action(request: Request, action: str, target: str = "", outcome: str = "ok") -> None:
    """Dependency helper for consistent audit logging."""
    from hydra_detect.web import server

    server._audit(request, action, target=target, outcome=outcome)
