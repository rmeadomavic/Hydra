"""mediamtx sidecar scaffolding + smoke tests.

Guards the WebRTC sidecar story from issue #187: a `mediamtx` service
ingests Hydra's RTSP at rtsp://localhost:8554/hydra and republishes as
WebRTC at http://<host>:8889/cam/whep.

Most tests are scaffolding (compose YAML, mediamtx.yml, README pointer)
and run in CI. The live HTTP smoke test is gated on a reachable
mediamtx on localhost:8889 and skips gracefully when the service is not
running — CI environments do not bring up Docker Compose.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "docker-compose.yml"
MTX_CONFIG = REPO / "mediamtx.yml"
README = REPO / "README.md"
ENV_EXAMPLE = REPO / ".env.example"

MTX_HOST = "127.0.0.1"
MTX_WEBRTC_PORT = 8889
MTX_WHEP_PATH = "/cam/whep"
MTX_IMAGE_TAG = "bluenviron/mediamtx:1.9.0"


def _compose() -> dict:
    parsed = yaml.safe_load(COMPOSE.read_text())
    assert parsed and "services" in parsed, "docker-compose.yml missing services"
    return parsed


def _mediamtx_service() -> dict:
    services = _compose()["services"]
    assert "mediamtx" in services, "mediamtx service missing from docker-compose.yml"
    return services["mediamtx"]


def _hydra_service() -> dict:
    services = _compose()["services"]
    assert "hydra-detect" in services, "hydra-detect service missing"
    return services["hydra-detect"]


# ── Compose scaffolding ─────────────────────────────────────────────


def test_compose_is_valid_yaml():
    parsed = yaml.safe_load(COMPOSE.read_text())
    assert isinstance(parsed, dict)


def test_compose_has_hydra_and_mediamtx_services():
    services = _compose()["services"]
    assert "hydra-detect" in services
    assert "mediamtx" in services


def test_mediamtx_pinned_to_specific_tag():
    svc = _mediamtx_service()
    assert "image" in svc, "mediamtx must specify an image"
    assert svc["image"] == MTX_IMAGE_TAG, (
        f"mediamtx must pin to {MTX_IMAGE_TAG}, got {svc['image']!r}"
    )
    assert ":latest" not in svc["image"], "no :latest in production sidecar pins"


def test_mediamtx_uses_host_networking():
    # Host networking is required so mediamtx can reach Hydra's RTSP on
    # 127.0.0.1:8554 and so WebRTC ICE candidates advertise the host IP.
    svc = _mediamtx_service()
    assert svc.get("network_mode") == "host"


def test_mediamtx_mounts_config_read_only():
    svc = _mediamtx_service()
    vols = svc.get("volumes", [])
    assert any("mediamtx.yml" in v and ":ro" in v for v in vols), (
        "mediamtx.yml must be bind-mounted read-only into the container"
    )


def test_mediamtx_profile_gated_on_streaming_flag():
    # Profile name comes from HYDRA_STREAMING_MTX (default `on`). When
    # the flag is `off`, the profile no longer matches COMPOSE_PROFILES
    # and the service stays down.
    svc = _mediamtx_service()
    profiles = svc.get("profiles", [])
    assert profiles, "mediamtx must be profile-gated for the kill switch"
    joined = " ".join(str(p) for p in profiles)
    assert "HYDRA_STREAMING_MTX" in joined, (
        "profile must reference HYDRA_STREAMING_MTX so the env flag controls startup"
    )


def test_compose_does_not_collide_with_dev_compose_8081():
    # compose.dev.yml owns :8081. The streaming sidecar must not steal
    # it. Sanity-check by scanning declared port mappings.
    svc = _mediamtx_service()
    ports = [str(p) for p in svc.get("ports", [])]
    assert not any(p.startswith("8081:") or p == "8081" for p in ports)


def test_hydra_service_present_for_sidecar_pairing():
    # The whole point of the compose file is to pair Hydra with the
    # sidecar. Make sure we did not accidentally drop the producer.
    svc = _hydra_service()
    assert "image" in svc
    assert "hydra-detect" in svc["image"]


# ── mediamtx.yml config ─────────────────────────────────────────────


def test_mediamtx_config_exists_and_is_valid_yaml():
    assert MTX_CONFIG.exists(), "mediamtx.yml must exist at repo root"
    parsed = yaml.safe_load(MTX_CONFIG.read_text())
    assert isinstance(parsed, dict)


def test_mediamtx_config_pulls_from_hydra_rtsp():
    parsed = yaml.safe_load(MTX_CONFIG.read_text())
    paths = parsed.get("paths", {}) or {}
    cam = paths.get("cam") or {}
    src = str(cam.get("source", ""))
    assert "rtsp://" in src and ":8554" in src and "/hydra" in src, (
        f"mediamtx cam path must pull from Hydra RTSP, got {src!r}"
    )


def test_mediamtx_webrtc_on_8889():
    parsed = yaml.safe_load(MTX_CONFIG.read_text())
    assert parsed.get("webrtc") in (True, "yes"), "WebRTC must be enabled"
    addr = str(parsed.get("webrtcAddress", ""))
    assert addr.endswith(":8889"), f"WebRTC port must be 8889, got {addr!r}"


def test_mediamtx_hls_off_by_default():
    parsed = yaml.safe_load(MTX_CONFIG.read_text())
    # Locked scope: HLS off by default. Either omitted or explicitly no.
    hls = parsed.get("hls", False)
    assert hls in (False, "no", None), f"HLS must default off, got {hls!r}"


def test_mediamtx_no_recording():
    # Out-of-scope: no recording. Spec lock from #187.
    parsed = yaml.safe_load(MTX_CONFIG.read_text())
    record = parsed.get("record", False)
    assert record in (False, "no", None), f"recording must be off, got {record!r}"


# ── README + env wiring ─────────────────────────────────────────────


def test_readme_documents_browser_view():
    txt = README.read_text()
    assert "View in browser" in txt, "README must have a 'View in browser' section"
    assert "8889" in txt
    assert "/cam/whep" in txt


def test_env_example_has_streaming_flag():
    txt = ENV_EXAMPLE.read_text()
    assert "HYDRA_STREAMING_MTX" in txt, (
        ".env.example must document HYDRA_STREAMING_MTX so operators can flip it"
    )


# ── Live smoke test ─────────────────────────────────────────────────


def _mediamtx_listening() -> bool:
    """Best-effort probe — TCP connect to the WebRTC port.

    Cheap and fast; avoids importing requests just to discover the
    service is down. Returns False on any error so the smoke test
    skips cleanly in CI / dev machines without docker compose up.
    """
    try:
        with socket.create_connection((MTX_HOST, MTX_WEBRTC_PORT), timeout=0.25):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _mediamtx_listening(),
    reason=(
        f"mediamtx not listening on {MTX_HOST}:{MTX_WEBRTC_PORT} — "
        "run `docker compose up` to enable this smoke test"
    ),
)
def test_webrtc_endpoint_returns_2xx_when_up():
    # Live HTTP probe of the WHEP endpoint. Locked scope says: verify
    # the WebRTC endpoint returns HTTP 200 when the service is up.
    # We accept any 2xx so a future mediamtx version that returns 204
    # for a HEAD doesn't break the gate.
    requests = pytest.importorskip("requests")
    url = f"http://{MTX_HOST}:{MTX_WEBRTC_PORT}{MTX_WHEP_PATH}"
    resp = requests.head(url, timeout=2.0, allow_redirects=True)
    assert 200 <= resp.status_code < 300, (
        f"WebRTC WHEP endpoint {url} returned {resp.status_code}"
    )
