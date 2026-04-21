"""Dev-experience infra presence checks.

Covers:
  (a) Makefile exists and declares every expected target
  (b) .pre-commit-config.yaml is valid YAML with at least one hook repo
  (c) .github/workflows/ci.yml is valid YAML and defines a jobs map
  (d) .editorconfig exists with the core indent rules
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED_MAKE_TARGETS = (
    "test",
    "test-all",
    "lint",
    "build",
    "up",
    "logs",
    "shell",
    "clean",
    "smoke",
)


def test_makefile_exists_and_has_targets():
    mk = ROOT / "Makefile"
    assert mk.exists(), "Makefile missing at repo root"
    text = mk.read_text()
    assert ".PHONY" in text, "Makefile should declare .PHONY"
    for target in EXPECTED_MAKE_TARGETS:
        assert f"{target}:" in text, f"Makefile missing target: {target}"


def test_pre_commit_config_valid_yaml():
    yaml = pytest.importorskip("yaml")
    p = ROOT / ".pre-commit-config.yaml"
    assert p.exists(), ".pre-commit-config.yaml missing"
    data = yaml.safe_load(p.read_text())
    assert isinstance(data, dict), "pre-commit config must be a mapping"
    assert "repos" in data and data["repos"], "pre-commit needs at least one repo"
    hook_ids = {
        h.get("id")
        for repo in data["repos"]
        for h in (repo.get("hooks") or [])
    }
    for required in ("trailing-whitespace", "end-of-file-fixer", "check-yaml", "flake8"):
        assert required in hook_ids, f"pre-commit missing hook: {required}"


def test_ci_workflow_valid_yaml():
    yaml = pytest.importorskip("yaml")
    p = ROOT / ".github" / "workflows" / "ci.yml"
    assert p.exists(), ".github/workflows/ci.yml missing"
    data = yaml.safe_load(p.read_text())
    assert isinstance(data, dict), "ci.yml must parse to a mapping"
    assert "jobs" in data and data["jobs"], "ci.yml must define jobs"


def test_editorconfig_present():
    p = ROOT / ".editorconfig"
    assert p.exists(), ".editorconfig missing"
    text = p.read_text()
    assert "root = true" in text
    assert "charset = utf-8" in text
    assert "end_of_line = lf" in text
    assert "indent_size = 4" in text
    assert "indent_size = 2" in text
