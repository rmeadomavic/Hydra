# Mission Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mission profiles — pre-defined configuration bundles that let operators switch model + confidence + class filters + alert classes + engagement settings in a single click.

**Architecture:** A `profiles.json` file defines 6 profiles. A new `hydra_detect/profiles.py` module loads/validates them. The pipeline applies profiles on startup and via a new web API. The operations UI gets a profile dropdown as the primary control, demoting the model dropdown.

**Tech Stack:** Python 3.10+, FastAPI, vanilla JS, JSON config

**Spec:** `docs/superpowers/specs/2026-03-25-mission-profiles-design.md`

---

### Task 1: Create `profiles.py` module

**Files:**
- Create: `hydra_detect/profiles.py`
- Create: `tests/test_profiles.py`

- [ ] **Step 1: Write tests for profile loading and validation**

```python
"""Tests for mission profile loading and validation."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from hydra_detect.profiles import get_profile, load_profiles


class TestLoadProfiles:
    def test_load_valid_profiles(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text(json.dumps({
            "default_profile": "general",
            "profiles": [
                {
                    "id": "general",
                    "name": "General",
                    "description": "Standard detection",
                    "model": "yolov8n.pt",
                    "confidence": 0.45,
                    "yolo_classes": [0, 1, 2],
                    "alert_classes": ["person", "car"],
                    "auto_loiter_on_detect": False,
                    "strike_distance_m": 20.0,
                },
            ],
        }))
        data = load_profiles(str(p))
        assert data["default_profile"] == "general"
        assert len(data["profiles"]) == 1
        assert data["profiles"][0]["id"] == "general"

    def test_load_missing_file_returns_empty(self):
        data = load_profiles("/nonexistent/profiles.json")
        assert data["profiles"] == []
        assert data["default_profile"] is None

    def test_load_malformed_json_returns_empty(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text("not valid json{{{")
        data = load_profiles(str(p))
        assert data["profiles"] == []

    def test_load_missing_required_field_skips_profile(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text(json.dumps({
            "default_profile": "good",
            "profiles": [
                {"id": "good", "name": "Good", "description": "ok",
                 "model": "m.pt", "confidence": 0.5, "yolo_classes": None,
                 "alert_classes": ["a"], "auto_loiter_on_detect": False,
                 "strike_distance_m": 10.0},
                {"id": "bad", "name": "Bad"},
            ],
        }))
        data = load_profiles(str(p))
        assert len(data["profiles"]) == 1
        assert data["profiles"][0]["id"] == "good"

    def test_null_yolo_classes_accepted(self, tmp_path):
        p = tmp_path / "profiles.json"
        p.write_text(json.dumps({
            "default_profile": "a",
            "profiles": [
                {"id": "a", "name": "A", "description": "d",
                 "model": "m.pt", "confidence": 0.5, "yolo_classes": None,
                 "alert_classes": [], "auto_loiter_on_detect": False,
                 "strike_distance_m": 10.0},
            ],
        }))
        data = load_profiles(str(p))
        assert data["profiles"][0]["yolo_classes"] is None


class TestGetProfile:
    def test_get_existing_profile(self):
        profiles = {
            "default_profile": "a",
            "profiles": [{"id": "a", "name": "A"}],
        }
        assert get_profile(profiles, "a")["id"] == "a"

    def test_get_nonexistent_returns_none(self):
        profiles = {"default_profile": "a", "profiles": [{"id": "a", "name": "A"}]}
        assert get_profile(profiles, "nope") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hydra_detect.profiles'`

- [ ] **Step 3: Implement `profiles.py`**

```python
"""Mission profile loading and validation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = {
    "id", "name", "description", "model", "confidence",
    "yolo_classes", "alert_classes", "auto_loiter_on_detect",
    "strike_distance_m",
}


def load_profiles(path: str) -> dict:
    """Load and validate mission profiles from a JSON file.

    Returns a dict with 'profiles' (list) and 'default_profile' (str | None).
    On any error, returns empty profiles list (graceful degradation).
    """
    result: dict = {"profiles": [], "default_profile": None}
    p = Path(path)
    if not p.exists():
        logger.info("No profiles file at %s — profiles disabled.", path)
        return result
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load profiles from %s: %s", path, exc)
        return result

    if not isinstance(raw, dict):
        logger.warning("Profiles file must be a JSON object.")
        return result

    result["default_profile"] = raw.get("default_profile")

    for entry in raw.get("profiles", []):
        missing = _REQUIRED_FIELDS - set(entry.keys())
        if missing:
            logger.warning("Profile '%s' missing fields %s — skipped.",
                           entry.get("id", "?"), missing)
            continue
        result["profiles"].append(entry)

    return result


def get_profile(profiles: dict, profile_id: str) -> dict | None:
    """Look up a profile by ID. Returns None if not found."""
    for p in profiles.get("profiles", []):
        if p.get("id") == profile_id:
            return p
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/profiles.py tests/test_profiles.py
git commit -m "feat: add profiles module for mission profile loading/validation"
```

---

### Task 2: Add `set_classes()` to `YOLODetector`

**Files:**
- Modify: `hydra_detect/detectors/yolo_detector.py:83-89`
- Modify: `tests/test_detectors.py`

- [ ] **Step 1: Write test for `set_classes`**

Add to `tests/test_detectors.py`:

```python
class TestSetClasses:
    def test_set_classes_updates_filter(self):
        det = YOLODetector(model_path="yolov8n.pt", confidence=0.5)
        assert det._classes is None
        det.set_classes([0, 2, 7])
        assert det._classes == [0, 2, 7]

    def test_set_classes_none_clears_filter(self):
        det = YOLODetector(model_path="yolov8n.pt", confidence=0.5, classes=[0, 1])
        det.set_classes(None)
        assert det._classes is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_detectors.py::TestSetClasses -v`
Expected: FAIL — `AttributeError: 'YOLODetector' object has no attribute 'set_classes'`

- [ ] **Step 3: Add `set_classes` method**

In `hydra_detect/detectors/yolo_detector.py`, after the `set_threshold` method (line 86), add:

```python
    def set_classes(self, classes: list[int] | None) -> None:
        """Update YOLO class filter at runtime."""
        self._classes = classes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_detectors.py::TestSetClasses -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/detectors/yolo_detector.py tests/test_detectors.py
git commit -m "feat: add set_classes() for runtime YOLO class filter updates"
```

---

### Task 3: Create `profiles.json` with 6 mission profiles

**Files:**
- Create: `profiles.json`

- [ ] **Step 1: Create `profiles.json`**

```json
{
    "default_profile": "military",
    "profiles": [
        {
            "id": "general",
            "name": "General (COCO)",
            "description": "Standard 80-class detection — person, vehicle, boat, animal",
            "model": "yolov8n.pt",
            "confidence": 0.45,
            "yolo_classes": [0, 1, 2, 3, 5, 7, 8, 14, 15, 16, 24, 25, 28],
            "alert_classes": ["person", "car", "motorcycle", "truck", "bus", "boat", "dog"],
            "auto_loiter_on_detect": false,
            "strike_distance_m": 20.0
        },
        {
            "id": "military",
            "name": "Military (General)",
            "description": "Broad military — air, ground, and maritime targets",
            "model": "yolov8m-defence.pt",
            "confidence": 0.40,
            "yolo_classes": null,
            "alert_classes": ["tank", "fighter jet", "warship", "drone", "missile", "helicopter", "truck", "cargo ship"],
            "auto_loiter_on_detect": false,
            "strike_distance_m": 30.0
        },
        {
            "id": "counter-uas",
            "name": "Counter-UAS",
            "description": "Detect drones, helicopters, and aircraft in the sky",
            "model": "yolo11n-aerodetect.pt",
            "confidence": 0.35,
            "yolo_classes": null,
            "alert_classes": ["Drone", "Helicopter", "AirPlane"],
            "auto_loiter_on_detect": true,
            "strike_distance_m": 50.0
        },
        {
            "id": "aerial-surveillance",
            "name": "Aerial Surveillance",
            "description": "People and vehicles from a drone's aerial perspective",
            "model": "yolo11n-visdrone.pt",
            "confidence": 0.30,
            "yolo_classes": null,
            "alert_classes": ["pedestrian", "people", "car", "van", "truck", "bus"],
            "auto_loiter_on_detect": false,
            "strike_distance_m": 20.0
        },
        {
            "id": "ground-vehicles",
            "name": "Ground Vehicles",
            "description": "Armored and light military vehicles",
            "model": "yolo12n-orion.pt",
            "confidence": 0.40,
            "yolo_classes": null,
            "alert_classes": ["AFV", "APC", "MEV", "LAV"],
            "auto_loiter_on_detect": false,
            "strike_distance_m": 25.0
        },
        {
            "id": "force-protection",
            "name": "Force Protection",
            "description": "Weapons and explosive threat detection",
            "model": "yolov8n-threat.pt",
            "confidence": 0.50,
            "yolo_classes": null,
            "alert_classes": ["Gun", "explosion", "grenade", "knife"],
            "auto_loiter_on_detect": true,
            "strike_distance_m": 15.0
        }
    ]
}
```

- [ ] **Step 2: Verify it loads**

Run: `python -c "from hydra_detect.profiles import load_profiles; d = load_profiles('profiles.json'); print(f'{len(d[\"profiles\"])} profiles loaded, default={d[\"default_profile\"]}')" `
Expected: `6 profiles loaded, default=military`

- [ ] **Step 3: Commit**

```bash
git add profiles.json
git commit -m "feat: add profiles.json with 6 mission profiles"
```

---

### Task 4: Wire profiles into Pipeline

**Files:**
- Modify: `hydra_detect/pipeline.py:39,85-121,493-536,797-835,1009-1025`
- Modify: `tests/test_pipeline_callbacks.py`

- [ ] **Step 1: Write tests for profile switching in pipeline**

Add to `tests/test_pipeline_callbacks.py`:

```python
from hydra_detect.profiles import load_profiles


class TestProfileSwitch:
    def test_handle_profile_switch_applies_settings(self, tmp_path):
        import json
        pf = tmp_path / "profiles.json"
        pf.write_text(json.dumps({
            "default_profile": "a",
            "profiles": [{
                "id": "a", "name": "A", "description": "test",
                "model": "yolov8n.pt", "confidence": 0.30,
                "yolo_classes": [0, 2], "alert_classes": ["person", "car"],
                "auto_loiter_on_detect": True, "strike_distance_m": 50.0,
            }],
        }))
        p = _make_pipeline()
        p._profiles = load_profiles(str(pf))
        p._active_profile = None
        p._models_dir = tmp_path / "models"
        p._models_dir.mkdir()
        p._project_dir = tmp_path
        # Create a fake model file so _handle_model_switch finds it
        (p._project_dir / "yolov8n.pt").touch()
        p._detector.switch_model.return_value = True
        p._alert_classes = None

        result = p._handle_profile_switch("a")
        assert result is True
        assert p._active_profile == "a"
        p._detector.set_threshold.assert_called_with(0.30)
        p._detector.set_classes.assert_called_with([0, 2])
        assert p._alert_classes == {"person", "car"}

    def test_handle_profile_switch_unknown_profile(self):
        p = _make_pipeline()
        p._profiles = {"profiles": [], "default_profile": None}
        p._active_profile = None
        result = p._handle_profile_switch("nonexistent")
        assert result is False

    def test_handle_profile_switch_null_yolo_classes(self, tmp_path):
        import json
        pf = tmp_path / "profiles.json"
        pf.write_text(json.dumps({
            "default_profile": "b",
            "profiles": [{
                "id": "b", "name": "B", "description": "test",
                "model": "yolov8n.pt", "confidence": 0.50,
                "yolo_classes": None, "alert_classes": [],
                "auto_loiter_on_detect": False, "strike_distance_m": 20.0,
            }],
        }))
        p = _make_pipeline()
        p._profiles = load_profiles(str(pf))
        p._active_profile = None
        p._models_dir = tmp_path / "models"
        p._models_dir.mkdir()
        p._project_dir = tmp_path
        (p._project_dir / "yolov8n.pt").touch()
        p._detector.switch_model.return_value = True
        p._alert_classes = {"old"}

        result = p._handle_profile_switch("b")
        assert result is True
        p._detector.set_classes.assert_called_with(None)
        assert p._alert_classes is None  # empty alert_classes = all

    def test_threshold_change_clears_active_profile(self):
        p = _make_pipeline()
        p._active_profile = "some-profile"
        p._handle_threshold_change(0.7)
        assert p._active_profile is None

    def test_alert_classes_change_clears_active_profile(self):
        p = _make_pipeline()
        p._active_profile = "some-profile"
        p._handle_alert_classes_change(["person"])
        assert p._active_profile is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline_callbacks.py::TestProfileSwitch -v`
Expected: FAIL — `AttributeError` (no `_handle_profile_switch`, `_profiles`, etc.)

- [ ] **Step 3: Add profile import and loading to Pipeline.__init__**

In `hydra_detect/pipeline.py`, add import at the top (after line 38):

```python
from .profiles import get_profile, load_profiles
```

In `Pipeline.__init__`, after `self._models_dir = ...` (line 92), add:

```python
        # Mission profiles
        profiles_path = self._project_dir / "profiles.json"
        self._profiles = load_profiles(str(profiles_path))
        self._active_profile: str | None = None
```

- [ ] **Step 4: Add `_handle_profile_switch` method**

In `hydra_detect/pipeline.py`, after `_handle_alert_classes_change` (after line 835), add:

```python
    def _handle_profile_switch(self, profile_id: str) -> bool:
        """Apply a mission profile: model + confidence + classes + engagement."""
        profile = get_profile(self._profiles, profile_id)
        if profile is None:
            logger.warning("Unknown profile: %s", profile_id)
            return False

        # 1. Switch model
        model_name = profile["model"]
        if Path(self._detector.model_path).name != model_name:
            if not self._handle_model_switch(model_name):
                logger.error("Profile %s: model switch failed for %s",
                             profile_id, model_name)
                return False

        # 2. Set confidence threshold
        self._detector.set_threshold(profile["confidence"])

        # 3. Set YOLO class filter
        self._detector.set_classes(profile["yolo_classes"])

        # 4. Set alert classes
        alert_classes = profile["alert_classes"]
        if alert_classes:
            self._alert_classes = set(alert_classes)
        else:
            self._alert_classes = None
        if self._mavlink is not None:
            self._mavlink.alert_classes = self._alert_classes

        # 5. Set engagement settings
        if self._mavlink is not None:
            self._mavlink.auto_loiter = profile["auto_loiter_on_detect"]

        # 6. Update runtime config for web UI
        self._active_profile = profile_id
        stream_state.update_runtime_config({
            "threshold": profile["confidence"],
            "alert_classes": alert_classes,
            "auto_loiter": profile["auto_loiter_on_detect"],
            "active_profile": profile_id,
        })
        logger.info("Profile switched: %s (%s)", profile["name"], profile_id)
        return True
```

- [ ] **Step 5: Clear `_active_profile` on manual setting changes**

In `_handle_threshold_change` (line 797), add at the end:

```python
        self._active_profile = None
        stream_state.update_runtime_config({"active_profile": None})
```

In `_handle_alert_classes_change` (line 824), add at the end (after the existing `logger.info` line):

```python
        self._active_profile = None
        stream_state.update_runtime_config({"active_profile": None})
```

In `_handle_model_switch` (line 1017), after `return self._detector.switch_model(str(candidate))`, wrap to also clear profile:

```python
    def _handle_model_switch(self, model_name: str) -> bool:
        """Switch YOLO model at runtime."""
        for candidate_dir in [Path("/models"), self._models_dir, self._project_dir]:
            candidate = candidate_dir / model_name
            if candidate.exists():
                success = self._detector.switch_model(str(candidate))
                if success:
                    self._active_profile = None
                    stream_state.update_runtime_config({"active_profile": None})
                return success
        logger.error("Model not found: %s", model_name)
        return False
```

- [ ] **Step 6: Register profile callbacks with web server**

In `Pipeline.run()`, in the `stream_state.set_callbacks(...)` block (around line 503), add:

```python
                get_profiles=self._get_profiles,
                on_profile_switch=self._handle_profile_switch,
```

And add the `_get_profiles` helper after `_get_models` (around line 1015):

```python
    def _get_profiles(self) -> dict:
        """Return profiles data for the web API."""
        models_on_disk = {m["name"] for m in self._get_models()}
        profiles_list = []
        for p in self._profiles.get("profiles", []):
            profiles_list.append({
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "model": p["model"],
                "confidence": p["confidence"],
                "alert_classes": p["alert_classes"],
                "auto_loiter_on_detect": p["auto_loiter_on_detect"],
                "model_exists": p["model"] in models_on_disk,
            })
        return {
            "profiles": profiles_list,
            "active_profile": self._active_profile,
        }
```

- [ ] **Step 7: Apply default profile on startup**

In `Pipeline.run()`, after `stream_state.update_runtime_config(...)` (after line 500), add:

```python
            # Apply default mission profile if configured
            default_id = self._profiles.get("default_profile")
            if default_id:
                if not self._handle_profile_switch(default_id):
                    logger.warning("Default profile '%s' failed to apply — "
                                   "using config.ini defaults.", default_id)
```

Also update the initial `runtime_config` to include `active_profile` (line 494):

```python
            stream_state.update_runtime_config({
                "threshold": self._cfg.getfloat("detector", "yolo_confidence", fallback=0.45),
                "auto_loiter": self._cfg.getboolean(
                    "mavlink", "auto_loiter_on_detect", fallback=False
                ),
                "alert_classes": list(self._alert_classes) if self._alert_classes else [],
                "active_profile": None,
            })
```

- [ ] **Step 8: Run tests**

Run: `python -m pytest tests/test_pipeline_callbacks.py::TestProfileSwitch -v`
Expected: All 5 tests PASS

- [ ] **Step 9: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 10: Commit**

```bash
git add hydra_detect/pipeline.py tests/test_pipeline_callbacks.py
git commit -m "feat: wire mission profiles into pipeline with startup default"
```

---

### Task 5: Add profile API endpoints to web server

**Files:**
- Modify: `hydra_detect/web/server.py:545-572`
- Modify: `tests/test_web_api.py`

- [ ] **Step 1: Write tests for profile endpoints**

Add to `tests/test_web_api.py`:

```python
class TestProfileEndpoints:
    def test_get_profiles(self, client):
        stream_state.set_callbacks(
            get_profiles=lambda: {
                "profiles": [
                    {"id": "general", "name": "General", "description": "test",
                     "model": "yolov8n.pt", "model_exists": True,
                     "confidence": 0.45, "alert_classes": ["person"],
                     "auto_loiter_on_detect": False},
                ],
                "active_profile": "general",
            },
        )
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["profiles"]) == 1
        assert data["active_profile"] == "general"

    def test_get_profiles_no_callback(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["profiles"] == []

    def test_switch_profile_requires_auth(self, client):
        configure_auth("secret-token")
        resp = client.post("/api/profiles/switch", json={"profile": "general"})
        assert resp.status_code == 401

    def test_switch_profile_success(self, client):
        configure_auth("secret-token")
        stream_state.set_callbacks(on_profile_switch=lambda pid: True)
        resp = client.post("/api/profiles/switch",
                           json={"profile": "general"},
                           headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_switch_profile_failure(self, client):
        configure_auth("secret-token")
        stream_state.set_callbacks(on_profile_switch=lambda pid: False)
        resp = client.post("/api/profiles/switch",
                           json={"profile": "bad"},
                           headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 400

    def test_switch_profile_missing_id(self, client):
        configure_auth("secret-token")
        resp = client.post("/api/profiles/switch",
                           json={},
                           headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_api.py::TestProfileEndpoints -v`
Expected: FAIL — 404 on `/api/profiles`

- [ ] **Step 3: Add endpoints to server.py**

In `hydra_detect/web/server.py`, after the `/api/models/switch` endpoint (after line 572), add:

```python
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
    auth_err = _check_auth(authorization)
    if auth_err:
        return auth_err
    body = await request.json()
    profile_id = body.get("profile")
    if not profile_id:
        return JSONResponse({"error": "profile ID required"}, status_code=400)
    cb = stream_state.get_callback("on_profile_switch")
    if cb:
        success = cb(profile_id)
        if success:
            _audit(request, "profile_switch", target=profile_id)
            return {"status": "ok", "profile": profile_id}
        _audit(request, "profile_switch", target=profile_id, outcome="failed")
        return JSONResponse({"error": f"Failed to switch to profile '{profile_id}'"}, status_code=400)
    return JSONResponse({"error": "Profile switching not available"}, status_code=503)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_web_api.py::TestProfileEndpoints -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/web/server.py tests/test_web_api.py
git commit -m "feat: add GET/POST /api/profiles endpoints"
```

---

### Task 6: Update operations HTML — add profile dropdown

**Files:**
- Modify: `hydra_detect/web/templates/operations.html:178-207`

- [ ] **Step 1: Replace the Detection Config panel content**

Replace lines 183-206 (the panel-body contents of the Detection Config panel) with:

```html
        <div class="panel-body">
            <div class="panel-field">
                <label class="panel-field-label" for="ctrl-profile-select">Mission Profile</label>
                <select id="ctrl-profile-select">
                    <option value="">Loading...</option>
                </select>
                <div class="panel-profile-desc" id="ctrl-profile-desc"></div>
            </div>
            <div class="panel-field">
                <label class="panel-field-label">Model</label>
                <span class="panel-field-value mono" id="ctrl-model-display">—</span>
            </div>
            <div class="panel-field">
                <label class="panel-field-label">Confidence Threshold</label>
                <div class="panel-range-row">
                    <input type="range" id="ctrl-thresh-slider" min="0.05" max="0.95" step="0.05" value="0.45">
                    <span class="panel-range-val mono" id="ctrl-thresh-val">0.45</span>
                </div>
            </div>
            <div class="panel-field">
                <label class="panel-field-label">Alert Classes <span class="panel-field-note">(GCS alerts + map markers)</span></label>
                <div class="panel-alert-btns">
                    <button class="btn btn-sm" id="ctrl-alert-all">All</button>
                    <button class="btn btn-sm" id="ctrl-alert-clear">Clear</button>
                    <button class="btn btn-green btn-sm" id="ctrl-alert-apply">Apply</button>
                </div>
                <div class="panel-alert-class-list" id="ctrl-alert-class-list"></div>
            </div>
        </div>
```

This replaces the model `<select>` dropdown with:
- A new profile `<select>` dropdown at the top
- A profile description `<div>` below it
- A read-only model display `<span>` replacing the old model select
- Confidence slider and alert classes unchanged

- [ ] **Step 2: Commit**

```bash
git add hydra_detect/web/templates/operations.html
git commit -m "feat: add mission profile dropdown to operations HTML"
```

---

### Task 7: Update operations.js — profile loading and switching

**Files:**
- Modify: `hydra_detect/web/static/js/operations.js:40-71,154-176,948-959`

- [ ] **Step 1: Add profile state and loading function**

At the top of the IIFE in `operations.js`, near the `alertClassData` declaration, add:

```javascript
    let profileData = { profiles: [], active: null };
```

Replace `loadModels()` in `loadDropdowns()` (line 42) with `loadProfiles()`:

```javascript
    async function loadDropdowns() {
        loadProfiles();
        loadPowerModes();
        loadConfig();
        loadAlertClasses();
        rfModeChanged();
        loadRTSPStatus();
        loadMAVLinkVideoStatus();
        loadTAKStatus();
    }
```

Replace the entire `loadModels()` function (lines 52-71) with:

```javascript
    async function loadProfiles() {
        const data = await HydraApp.apiGet('/api/profiles');
        const sel = document.getElementById('ctrl-profile-select');
        const descEl = document.getElementById('ctrl-profile-desc');
        const modelEl = document.getElementById('ctrl-model-display');
        if (!sel || !data) return;
        profileData.profiles = data.profiles || [];
        profileData.active = data.active_profile;
        clearChildren(sel);

        // Add "Custom" option for when operator deviates from a profile
        const customOpt = document.createElement('option');
        customOpt.value = '';
        customOpt.textContent = '— Custom —';
        if (!profileData.active) customOpt.selected = true;
        sel.appendChild(customOpt);

        for (const p of profileData.profiles) {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.name;
            if (!p.model_exists) opt.textContent += ' (model missing)';
            if (p.id === profileData.active) opt.selected = true;
            sel.appendChild(opt);
        }

        // Update description and model display
        const active = profileData.profiles.find(p => p.id === profileData.active);
        if (descEl) descEl.textContent = active ? active.description : '';
        if (modelEl) {
            const models = await HydraApp.apiGet('/api/models');
            const current = models ? models.find(m => m.active) : null;
            modelEl.textContent = current ? current.name + ' (' + current.size_mb + ' MB)' : '—';
        }
    }
```

- [ ] **Step 2: Replace `switchModel` with `switchProfile`**

Replace the `switchModel` function (lines 948-959) with:

```javascript
    async function switchProfile(profileId) {
        if (!profileId) return;  // "Custom" selected — no action
        const sel = document.getElementById('ctrl-profile-select');
        if (sel) sel.disabled = true;
        const result = await HydraApp.apiPost('/api/profiles/switch', { profile: profileId });
        if (result && result.status === 'ok') {
            HydraApp.showToast('Profile: ' + profileId, 'success');
            // Reload everything to reflect new profile settings
            loadProfiles();
            loadConfig();
            loadAlertClasses();
        } else {
            HydraApp.showToast('Profile switch failed', 'error');
            loadProfiles();
        }
        if (sel) sel.disabled = false;
    }
```

- [ ] **Step 3: Update event handler wiring**

In `wireEventHandlers()`, replace the model select change handler.

Find the line that wires `ctrl-model-select` (should be something like):
```javascript
        addChange('ctrl-model-select', (e) => switchModel(e.target.value));
```

Replace with:
```javascript
        addChange('ctrl-profile-select', (e) => switchProfile(e.target.value));
```

- [ ] **Step 4: Mark profile as "Custom" on manual threshold/alert changes**

In the `updateThreshold()` function, after the API call, add:

```javascript
        // Manual threshold change = custom profile
        const profSel = document.getElementById('ctrl-profile-select');
        if (profSel) profSel.value = '';
        const descEl = document.getElementById('ctrl-profile-desc');
        if (descEl) descEl.textContent = '';
```

In the `applyAlertClasses()` function, after the API call succeeds, add:

```javascript
        // Manual alert class change = custom profile
        const profSel = document.getElementById('ctrl-profile-select');
        if (profSel) profSel.value = '';
        const descEl = document.getElementById('ctrl-profile-desc');
        if (descEl) descEl.textContent = '';
```

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/web/static/js/operations.js
git commit -m "feat: add profile selector JS — load, switch, custom state"
```

---

### Task 8: Add CSS for profile UI elements

**Files:**
- Modify: `hydra_detect/web/static/css/operations.css`

- [ ] **Step 1: Add profile-specific styles**

After the `.panel-field select` styles (around line 521), add:

```css
/* ── Mission Profile ── */
.panel-profile-desc {
    font-size: var(--font-xs);
    color: var(--text-dim);
    margin-top: 4px;
    line-height: 1.3;
    min-height: 1.3em;
}

.panel-field-value {
    font-size: var(--font-sm);
    color: var(--text-dim);
    display: block;
}
```

- [ ] **Step 2: Commit**

```bash
git add hydra_detect/web/static/css/operations.css
git commit -m "feat: add CSS for mission profile description and model display"
```

---

### Task 9: Update settings.js model dropdown reference

**Files:**
- Modify: `hydra_detect/web/static/js/settings.js:31`

- [ ] **Step 1: Check if settings.js model dropdown needs updating**

The settings page has its own `yolo_model` dropdown in the config editor. This should remain — it controls the persistent `config.ini` value. No changes needed to settings.js since profiles are an operations-view feature only.

Skip this task — no changes required.

---

### Task 10: End-to-end verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass (existing + new profile/detector/web tests)

- [ ] **Step 2: Run linter**

Run: `flake8 hydra_detect/profiles.py tests/test_profiles.py`
Expected: No errors

- [ ] **Step 3: Verify profiles.json loads on startup**

Run: `python -c "from hydra_detect.profiles import load_profiles; d = load_profiles('profiles.json'); [print(f'  {p[\"id\"]}: {p[\"name\"]} -> {p[\"model\"]}') for p in d['profiles']]"`

Expected output:
```
  general: General (COCO) -> yolov8n.pt
  military: Military (General) -> yolov8m-defence.pt
  counter-uas: Counter-UAS -> yolo11n-aerodetect.pt
  aerial-surveillance: Aerial Surveillance -> yolo11n-visdrone.pt
  ground-vehicles: Ground Vehicles -> yolo12n-orion.pt
  force-protection: Force Protection -> yolov8n-threat.pt
```

- [ ] **Step 4: Verify API endpoints respond**

Start the app (or test with `TestClient`):
```python
from fastapi.testclient import TestClient
from hydra_detect.web.server import app
client = TestClient(app)
r = client.get("/api/profiles")
print(r.json())
```

- [ ] **Step 5: Commit all remaining changes and create PR**

```bash
git add -A
git status
# Verify only expected files are staged
git commit -m "feat: mission profiles — operator one-click model+config switching

Adds mission profiles that bundle model, confidence, class filters,
alert classes, and engagement settings into single-click presets.

Six profiles: General (COCO), Military (General), Counter-UAS,
Aerial Surveillance, Ground Vehicles, Force Protection.

New files:
- profiles.json — profile definitions
- hydra_detect/profiles.py — load/validate profiles
- tests/test_profiles.py — profile module tests

Modified:
- pipeline.py — profile switching + startup default
- yolo_detector.py — set_classes() for runtime filter updates
- server.py — GET/POST /api/profiles endpoints
- operations.html — profile dropdown replaces model dropdown
- operations.js — profile loading, switching, custom state
- operations.css — profile description styling"
```
