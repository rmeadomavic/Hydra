"""Dev-mode compose scaffolding tests.

Guards the UI dev loop described in docs/dev-loop.md: the compose file
must stay valid YAML, expose :8081, bind-mount hydra_detect/, and run
uvicorn --reload. Also verifies Makefile + README pointers stay wired.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "compose.dev.yml"
MAKEFILE = REPO / "Makefile"
README = REPO / "README.md"
DEV_DOC = REPO / "docs" / "dev-loop.md"


def _dev_service() -> dict:
    parsed = yaml.safe_load(COMPOSE.read_text())
    assert parsed and "services" in parsed, "compose.dev.yml missing services"
    services = parsed["services"]
    assert len(services) >= 1
    return next(iter(services.values()))


def _command_as_string(cmd) -> str:
    if isinstance(cmd, list):
        return " ".join(str(x) for x in cmd)
    return str(cmd)


def test_compose_dev_is_valid_yaml():
    parsed = yaml.safe_load(COMPOSE.read_text())
    assert isinstance(parsed, dict)


def test_compose_dev_uses_prod_image_not_build():
    svc = _dev_service()
    assert "image" in svc, "dev container must reuse the prod image"
    assert "hydra-detect" in svc["image"]
    assert "build" not in svc, "dev must not trigger a rebuild"


def test_compose_dev_exposes_8081():
    svc = _dev_service()
    ports = svc.get("ports", [])
    assert ports, "dev compose needs a ports mapping"
    assert any("8081" in str(p) for p in ports)


def test_compose_dev_binds_hydra_detect_source():
    svc = _dev_service()
    vols = svc.get("volumes", [])
    assert any(
        v.endswith(":/app/hydra_detect") or ":/app/hydra_detect:" in v
        for v in vols
    ), "hydra_detect/ must be bind-mounted for hot edits"


def test_compose_dev_runs_uvicorn_with_reload():
    svc = _dev_service()
    cmd = _command_as_string(svc.get("command", ""))
    assert "uvicorn" in cmd
    assert "--reload" in cmd
    assert "hydra_detect.web.server:app" in cmd
    assert "8081" in cmd
    assert "0.0.0.0" in cmd


def test_compose_dev_leaves_prod_port_alone():
    svc = _dev_service()
    ports = [str(p) for p in svc.get("ports", [])]
    # Must not map 8080 — prod systemd container owns it.
    assert not any(p.startswith("8080:") or p == "8080" for p in ports)


def test_makefile_has_dev_target():
    txt = MAKEFILE.read_text()
    assert "\ndev:\n" in txt, "Makefile must define a `dev` target"
    assert "compose.dev.yml" in txt


def test_makefile_has_dev_down_target():
    txt = MAKEFILE.read_text()
    assert "\ndev-down:\n" in txt


def test_makefile_phony_includes_dev():
    txt = MAKEFILE.read_text()
    phony = [line for line in txt.splitlines() if line.startswith(".PHONY")]
    joined = " ".join(phony)
    assert "dev" in joined
    assert "dev-down" in joined


def test_makefile_existing_targets_untouched():
    # Additive — build/up/smoke must still exist.
    txt = MAKEFILE.read_text()
    for tgt in ("build:", "up:", "logs:", "shell:", "smoke:", "test:"):
        assert f"\n{tgt}" in txt, f"existing target {tgt!r} missing"


def test_readme_has_dev_loop_section():
    txt = README.read_text()
    assert "Dev loop" in txt
    assert "compose.dev.yml" in txt
    assert "8081" in txt


def test_dev_loop_doc_exists_and_references_compose():
    assert DEV_DOC.exists(), "docs/dev-loop.md must exist"
    txt = DEV_DOC.read_text()
    assert "compose.dev.yml" in txt
    assert "8081" in txt
    assert "make dev" in txt
