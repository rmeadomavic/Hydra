from __future__ import annotations

from fastapi.testclient import TestClient

from hydra_detect.web.server import app, configure_auth, stream_state


def test_protected_endpoint_rejects_unauthorized() -> None:
    configure_auth("secret-token-123")
    client = TestClient(app)

    resp = client.post("/api/vehicle/mode", json={"mode": "AUTO"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "Authorization header with Bearer token required"


def test_schema_validation_returns_structured_400() -> None:
    configure_auth(None)
    client = TestClient(app)

    resp = client.post("/api/config/threshold", json={"threshold": 3.0})
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "Validation failed"
    assert isinstance(data["field_errors"], list)
    assert data["field_errors"][0]["field"] in {"threshold", "body.threshold"}


def test_success_response_parity_for_threshold() -> None:
    configure_auth(None)
    client = TestClient(app)

    stream_state.set_callbacks(on_threshold_change=lambda v: True)
    resp = client.post("/api/config/threshold", json={"threshold": 0.4})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "threshold": 0.4}
