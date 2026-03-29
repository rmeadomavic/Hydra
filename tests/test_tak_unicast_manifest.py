"""Tests for TAK unicast target management and auto-manifest generation (PR #14)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hydra_detect.model_manifest import auto_update_manifest, load_manifest
from hydra_detect.tak.tak_output import TAKOutput


# =====================================================================
# TAK unicast target management
# =====================================================================

class TestTAKUnicastTargets:
    def _make_tak(self) -> TAKOutput:
        mav = MagicMock()
        return TAKOutput(mav)

    def test_add_unicast_target(self):
        tak = self._make_tak()
        tak.add_unicast_target("10.0.0.1", 6969)
        targets = tak.get_unicast_targets()
        assert len(targets) == 1
        assert targets[0] == {"host": "10.0.0.1", "port": 6969}

    def test_add_duplicate_ignored(self):
        tak = self._make_tak()
        tak.add_unicast_target("10.0.0.1", 6969)
        tak.add_unicast_target("10.0.0.1", 6969)
        assert len(tak.get_unicast_targets()) == 1

    def test_add_multiple_targets(self):
        tak = self._make_tak()
        tak.add_unicast_target("10.0.0.1", 6969)
        tak.add_unicast_target("10.0.0.2", 4242)
        targets = tak.get_unicast_targets()
        assert len(targets) == 2

    def test_remove_unicast_target(self):
        tak = self._make_tak()
        tak.add_unicast_target("10.0.0.1", 6969)
        tak.add_unicast_target("10.0.0.2", 4242)
        tak.remove_unicast_target("10.0.0.1", 6969)
        targets = tak.get_unicast_targets()
        assert len(targets) == 1
        assert targets[0] == {"host": "10.0.0.2", "port": 4242}

    def test_remove_nonexistent_is_noop(self):
        tak = self._make_tak()
        tak.add_unicast_target("10.0.0.1", 6969)
        tak.remove_unicast_target("192.168.1.1", 9999)
        assert len(tak.get_unicast_targets()) == 1

    def test_get_empty_targets(self):
        tak = self._make_tak()
        assert tak.get_unicast_targets() == []

    def test_different_ports_are_distinct(self):
        tak = self._make_tak()
        tak.add_unicast_target("10.0.0.1", 6969)
        tak.add_unicast_target("10.0.0.1", 4242)
        assert len(tak.get_unicast_targets()) == 2


# =====================================================================
# TAK unicast API endpoints
# =====================================================================

class TestTAKUnicastAPI:
    @pytest.fixture
    def client(self):
        from hydra_detect.web.server import app, stream_state
        from starlette.testclient import TestClient

        # Wire up mock TAK callbacks
        targets: list[tuple[str, int]] = []

        def get_targets():
            return [{"host": h, "port": p} for h, p in targets]

        def add_target(host, port):
            t = (host, port)
            if t not in targets:
                targets.append(t)

        def remove_target(host, port):
            t = (host, port)
            if t in targets:
                targets.remove(t)

        stream_state.set_callbacks(
            get_tak_targets=get_targets,
            add_tak_target=add_target,
            remove_tak_target=remove_target,
        )
        yield TestClient(app)
        # Clean up callbacks
        stream_state._callbacks.pop("get_tak_targets", None)
        stream_state._callbacks.pop("add_tak_target", None)
        stream_state._callbacks.pop("remove_tak_target", None)

    def test_get_empty_targets(self, client):
        resp = client.get("/api/tak/targets")
        assert resp.status_code == 200
        assert resp.json() == {"targets": []}

    def test_add_target(self, client):
        resp = client.post("/api/tak/targets", json={"host": "10.0.0.1", "port": 6969})
        assert resp.status_code == 200
        assert resp.json()["status"] == "added"

    def test_add_then_get(self, client):
        client.post("/api/tak/targets", json={"host": "10.0.0.1", "port": 6969})
        resp = client.get("/api/tak/targets")
        targets = resp.json()["targets"]
        assert len(targets) == 1
        assert targets[0]["host"] == "10.0.0.1"

    def test_add_requires_host(self, client):
        resp = client.post("/api/tak/targets", json={"port": 6969})
        assert resp.status_code == 400

    def test_remove_target(self, client):
        client.post("/api/tak/targets", json={"host": "10.0.0.1", "port": 6969})
        resp = client.request("DELETE", "/api/tak/targets", json={"host": "10.0.0.1", "port": 6969})
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    def test_get_no_callback_returns_empty(self):
        """When no callbacks are wired, GET returns empty targets."""
        from hydra_detect.web.server import app, stream_state
        from starlette.testclient import TestClient

        stream_state._callbacks.pop("get_tak_targets", None)
        client = TestClient(app)
        resp = client.get("/api/tak/targets")
        assert resp.status_code == 200
        assert resp.json() == {"targets": []}


# =====================================================================
# Auto-manifest generation
# =====================================================================

class TestAutoUpdateManifest:
    def test_adds_new_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "yolov8n.pt").write_bytes(b"fake model 1")
            (d / "yolov8s.pt").write_bytes(b"fake model 2")

            result = auto_update_manifest(d)
            assert result is True

            manifest = load_manifest(d / "manifest.json")
            assert manifest is not None
            assert len(manifest) == 2
            names = {e["filename"] for e in manifest}
            assert names == {"yolov8n.pt", "yolov8s.pt"}

    def test_skips_existing_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "yolov8n.pt").write_bytes(b"fake model 1")

            # First scan
            auto_update_manifest(d)
            manifest_before = load_manifest(d / "manifest.json")

            # Second scan -- should not update
            result = auto_update_manifest(d)
            assert result is False

            manifest_after = load_manifest(d / "manifest.json")
            assert manifest_before == manifest_after

    def test_creates_manifest_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "test.pt").write_bytes(b"model data")

            manifest_path = d / "manifest.json"
            assert not manifest_path.exists()

            auto_update_manifest(d)
            assert manifest_path.exists()
            manifest = json.loads(manifest_path.read_text())
            assert len(manifest) == 1
            assert manifest[0]["filename"] == "test.pt"

    def test_adds_only_new_to_existing_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "existing.pt").write_bytes(b"existing model")

            # Create initial manifest
            auto_update_manifest(d)

            # Add a new model
            (d / "new_model.pt").write_bytes(b"new model data")
            result = auto_update_manifest(d)
            assert result is True

            manifest = load_manifest(d / "manifest.json")
            assert len(manifest) == 2
            names = {e["filename"] for e in manifest}
            assert "existing.pt" in names
            assert "new_model.pt" in names

    def test_entry_has_expected_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "model.pt").write_bytes(b"x" * 1024)

            auto_update_manifest(d)
            manifest = load_manifest(d / "manifest.json")
            entry = manifest[0]
            assert "filename" in entry
            assert "sha256" in entry
            assert "size_mb" in entry
            assert "input_size" in entry
            assert "classes" in entry
            assert len(entry["sha256"]) == 64

    def test_nonexistent_directory_returns_false(self):
        result = auto_update_manifest(Path("/nonexistent/path"))
        assert result is False

    def test_empty_directory_no_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            result = auto_update_manifest(d)
            assert result is False
