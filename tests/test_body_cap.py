"""Tests for the request-body size cap (issue #289).

`_read_body_capped` must reject oversized bodies with 413 BEFORE the full
body is resident, on both the declared-Content-Length path and the streamed
(chunked) path. `/api/client_error` — the unauthenticated worst case — must
rate-limit before it touches the body.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hydra_detect.web import server as web_server
from hydra_detect.web.config_api import MAX_BODY_SIZE


@pytest.fixture
def client():
    return TestClient(web_server.app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    with web_server._client_error_lock:
        web_server._client_error_hits.clear()
    yield
    with web_server._client_error_lock:
        web_server._client_error_hits.clear()


def _oversized_payload() -> bytes:
    # Valid JSON just past the cap, so only the size check can reject it.
    return b'{"message": "' + b'x' * MAX_BODY_SIZE + b'"}'


class TestDeclaredLengthCap:
    def test_client_error_oversized_body_413(self, client):
        r = client.post(
            "/api/client_error",
            content=_oversized_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413
        assert r.json() == {"error": "Request body too large"}

    def test_lying_content_length_still_rejected(self):
        # Declared length under the cap; actual streamed body over it. The
        # streamed cap must catch what the header check waves through, and
        # must answer EXACTLY 413. Raw ASGI invocation — an HTTP client or
        # its transport would normalize/refuse the mismatched header, and an
        # earlier version of this test accepted any >= 400, which a 400/429
        # from unrelated causes could satisfy (2026-07-18 Codex re-review).
        import asyncio

        async def _run():
            sent = []
            body = _oversized_payload()
            chunks = [body[i:i + 8192] for i in range(0, len(body), 8192)]
            idx = {"i": 0}

            async def receive():
                if idx["i"] < len(chunks):
                    c = chunks[idx["i"]]
                    idx["i"] += 1
                    return {"type": "http.request", "body": c,
                            "more_body": idx["i"] < len(chunks)}
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            scope = {
                "type": "http", "asgi": {"version": "3.0"},
                "http_version": "1.1", "method": "POST", "scheme": "http",
                "path": "/api/client_error", "raw_path": b"/api/client_error",
                "query_string": b"", "root_path": "",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", b"application/json"),
                    (b"content-length", b"10"),  # the lie
                ],
                "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
            }
            await web_server.app(scope, receive, send)
            start = next(m for m in sent if m["type"] == "http.response.start")
            return start["status"]

        assert asyncio.run(_run()) == 413


class TestStreamedCap:
    def test_chunked_body_over_cap_413(self, client):
        # A generator body sends Transfer-Encoding: chunked — no
        # Content-Length header at all, so only the streamed cap applies.
        def gen():
            chunk = b'x' * 8192
            for _ in range((MAX_BODY_SIZE // 8192) + 2):
                yield chunk

        r = client.post(
            "/api/client_error",
            content=gen(),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413

    def test_small_body_still_accepted(self, client):
        r = client.post("/api/client_error", json={"message": "boom"})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestRateLimitBeforeBodyRead:
    def test_limited_ip_gets_429_even_with_malformed_json(self, client):
        # Exhaust the per-IP window with valid posts...
        for _ in range(web_server._CLIENT_ERROR_MAX_PER_WINDOW):
            assert client.post(
                "/api/client_error", json={"message": "x"}
            ).status_code == 200
        # ...then a malformed body. Pre-fix this returned 400 (body parsed
        # before the limiter); post-fix the limiter answers first.
        r = client.post(
            "/api/client_error",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 429


class TestAdminEndpointsKeep413:
    def test_config_full_oversized_413(self, client):
        web_server.configure_auth(None)
        r = client.post(
            "/api/config/full",
            content=b'{"camera": {"source": "' + b"x" * MAX_BODY_SIZE + b'"}}',
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413

    def test_parse_json_consumers_now_capped(self, client):
        # Representative previously-unguarded endpoint (auth off): the cap
        # must answer 413, not attempt to buffer the body.
        web_server.configure_auth(None)
        r = client.post(
            "/api/mode",
            content=_oversized_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413
