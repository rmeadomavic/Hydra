from __future__ import annotations

import configparser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hydra_detect.pipeline.bootstrap import PipelineBootstrap, build_detector
from hydra_detect.pipeline.control import PipelineControlAdapter
from hydra_detect.pipeline.integrations import PipelineIntegrations
from hydra_detect.pipeline.runtime import PipelineRuntime


def test_bootstrap_load_config_applies_vehicle_overrides(tmp_path: Path):
    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(
        """
[tak]
callsign = HYDRA-1
[vehicle.alpha]
camera.source = 1
"""
    )
    ctx = PipelineBootstrap().load_config(str(cfg_path), vehicle="alpha")
    assert ctx.cfg.get("camera", "source") == "1"
    assert ctx.callsign == "HYDRA-ALPHA"


def test_bootstrap_build_detector_uses_model_search_path(tmp_path: Path):
    cfg = configparser.ConfigParser()
    cfg.read_dict({"detector": {"yolo_model": "demo.pt", "yolo_confidence": "0.25"}})
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "demo.pt").write_text("x")

    with patch("hydra_detect.pipeline.bootstrap.YOLODetector") as detector_cls:
        build_detector(cfg, models_dir)

    kwargs = detector_cls.call_args.kwargs
    assert kwargs["model_path"].endswith("demo.pt")
    assert kwargs["confidence"] == 0.25


def test_control_adapter_exposes_expected_callbacks():
    p = SimpleNamespace(
        _handle_threshold_change=lambda _v: None,
        _handle_loiter_command=lambda: None,
        _handle_target_lock=lambda *_a, **_k: True,
        _handle_target_unlock=lambda *_a, **_k: None,
        _handle_strike_command=lambda _tid: True,
        _det_logger=SimpleNamespace(get_recent=lambda: []),
        _get_active_tracks=lambda: [],
        _handle_stop_command=lambda: None,
        _handle_pause_command=lambda: None,
        _handle_resume_command=lambda: None,
        _get_camera_sources=lambda: [],
        _handle_camera_switch=lambda _s: True,
        _handle_set_power_mode=lambda _m: {},
        _get_power_modes=lambda: [],
        _get_models=lambda: [],
        _handle_model_switch=lambda _m: True,
        _cfg=configparser.ConfigParser(),
        _get_rf_status=lambda: {},
        _get_rf_rssi_history=lambda: [],
        _get_rf_devices=lambda: {"mode": "unavailable", "devices": []},
        _get_rf_events=lambda: [],
        _handle_rf_start=lambda _p: True,
        _handle_rf_stop=lambda: None,
        _handle_rf_target=lambda _p: True,
        _handle_set_mode_command=lambda _m: True,
        _handle_alert_classes_change=lambda _c: None,
        _detector=SimpleNamespace(get_class_names=lambda: []),
        _handle_rtsp_toggle=lambda _e: {},
        _get_rtsp_status=lambda: {},
        _handle_mavlink_video_toggle=lambda _e: {},
        _handle_mavlink_video_tune=lambda _p: {},
        _get_mavlink_video_status=lambda: {},
        _handle_tak_toggle=lambda _e: {},
        _get_tak_status=lambda: {},
        _get_tak_targets=lambda: [],
        _add_tak_target=lambda *_a, **_k: True,
        _remove_tak_target=lambda *_a, **_k: True,
        _get_profiles=lambda: {},
        _handle_profile_switch=lambda _p: True,
        _get_preflight=lambda: {},
        _handle_restart_command=lambda: None,
        _handle_drop_command=lambda _tid: True,
        _handle_follow_command=lambda _tid: True,
        _handle_approach_strike_command=lambda _tid: True,
        _handle_pixel_lock_command=lambda _tid: True,
        _handle_approach_abort=lambda: None,
        _get_approach_status=lambda: {},
        _handle_mission_start=lambda _n: None,
        _handle_mission_end=lambda: None,
        _get_events=lambda: {},
        _event_logger=SimpleNamespace(get_status=lambda: {}),
        _play_tune=lambda _n: None,
    )
    cbs = PipelineControlAdapter(p).callbacks()
    assert "on_threshold_change" in cbs
    assert "on_strike_command" in cbs
    assert "get_preflight" in cbs


def test_integrations_register_web_callbacks():
    adapter = MagicMock()
    adapter.callbacks.return_value = {"on_threshold_change": lambda _x: None}
    p = SimpleNamespace()
    integrations = PipelineIntegrations(p)
    stream_state = MagicMock()
    with patch("hydra_detect.pipeline.integrations._get_stream_state", return_value=stream_state):
        integrations.register_web_callbacks(adapter)
    stream_state.set_callbacks.assert_called_once()


def test_runtime_start_stop_lifecycle_order():
    calls: list[str] = []

    class Logger:
        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    class Servo:
        def safe(self):
            calls.append("safe")

    p = SimpleNamespace(_det_logger=Logger(), _servo_tracker=Servo(), _running=False)
    runtime = PipelineRuntime(p)

    runtime.start_components()
    runtime.stop_components()

    assert calls == ["start", "safe", "stop"]
    assert p._running is False


def test_pipeline_contract_adapter_callbacks_keep_command_behavior():
    # Contract: existing callback names still route to same bound methods.
    cb_owner = SimpleNamespace()
    cb_owner._handle_strike_command = MagicMock(return_value=True)
    cb_owner._handle_target_lock = MagicMock(return_value=True)
    cb_owner._handle_target_unlock = MagicMock(return_value=None)
    cb_owner._handle_threshold_change = MagicMock()
    cb_owner._handle_loiter_command = MagicMock()
    cb_owner._det_logger = SimpleNamespace(get_recent=lambda: [])
    cb_owner._get_active_tracks = MagicMock(return_value=[])
    cb_owner._handle_stop_command = MagicMock()
    cb_owner._handle_pause_command = MagicMock()
    cb_owner._handle_resume_command = MagicMock()
    cb_owner._get_camera_sources = MagicMock(return_value=[])
    cb_owner._handle_camera_switch = MagicMock(return_value=True)
    cb_owner._handle_set_power_mode = MagicMock(return_value={})
    cb_owner._get_power_modes = MagicMock(return_value=[])
    cb_owner._get_models = MagicMock(return_value=[])
    cb_owner._handle_model_switch = MagicMock(return_value=True)
    cb_owner._cfg = configparser.ConfigParser()
    cb_owner._get_rf_status = MagicMock(return_value={})
    cb_owner._get_rf_rssi_history = MagicMock(return_value=[])
    cb_owner._get_rf_devices = MagicMock(
        return_value={"mode": "unavailable", "devices": []},
    )
    cb_owner._get_rf_events = MagicMock(return_value=[])
    cb_owner._handle_rf_start = MagicMock(return_value=True)
    cb_owner._handle_rf_stop = MagicMock()
    cb_owner._handle_rf_target = MagicMock(return_value=True)
    cb_owner._handle_set_mode_command = MagicMock(return_value=True)
    cb_owner._handle_alert_classes_change = MagicMock()
    cb_owner._detector = SimpleNamespace(get_class_names=lambda: [])
    cb_owner._handle_rtsp_toggle = MagicMock(return_value={})
    cb_owner._get_rtsp_status = MagicMock(return_value={})
    cb_owner._handle_mavlink_video_toggle = MagicMock(return_value={})
    cb_owner._handle_mavlink_video_tune = MagicMock(return_value={})
    cb_owner._get_mavlink_video_status = MagicMock(return_value={})
    cb_owner._handle_tak_toggle = MagicMock(return_value={})
    cb_owner._get_tak_status = MagicMock(return_value={})
    cb_owner._get_tak_targets = MagicMock(return_value=[])
    cb_owner._add_tak_target = MagicMock(return_value=True)
    cb_owner._remove_tak_target = MagicMock(return_value=True)
    cb_owner._get_profiles = MagicMock(return_value={})
    cb_owner._handle_profile_switch = MagicMock(return_value=True)
    cb_owner._get_preflight = MagicMock(return_value={})
    cb_owner._handle_restart_command = MagicMock()
    cb_owner._handle_drop_command = MagicMock(return_value=True)
    cb_owner._handle_follow_command = MagicMock(return_value=True)
    cb_owner._handle_approach_strike_command = MagicMock(return_value=True)
    cb_owner._handle_pixel_lock_command = MagicMock(return_value=True)
    cb_owner._handle_approach_abort = MagicMock()
    cb_owner._get_approach_status = MagicMock(return_value={})
    cb_owner._handle_mission_start = MagicMock()
    cb_owner._handle_mission_end = MagicMock()
    cb_owner._get_events = MagicMock(return_value={})
    cb_owner._event_logger = SimpleNamespace(get_status=lambda: {})
    cb_owner._play_tune = MagicMock()

    callbacks = PipelineControlAdapter(cb_owner).callbacks()

    # Identity check for every direct method/attribute binding. If
    # control.py rebinds a callback to the wrong handler (copy-paste
    # rename, refactor regression), one of these fails specifically.
    expected_bindings = {
        "on_threshold_change": cb_owner._handle_threshold_change,
        "on_loiter_command": cb_owner._handle_loiter_command,
        "on_target_lock": cb_owner._handle_target_lock,
        "on_target_unlock": cb_owner._handle_target_unlock,
        "on_strike_command": cb_owner._handle_strike_command,
        "get_recent_detections": cb_owner._det_logger.get_recent,
        "get_active_tracks": cb_owner._get_active_tracks,
        "on_stop_command": cb_owner._handle_stop_command,
        "on_pause_command": cb_owner._handle_pause_command,
        "on_resume_command": cb_owner._handle_resume_command,
        "get_camera_sources": cb_owner._get_camera_sources,
        "on_camera_switch": cb_owner._handle_camera_switch,
        "on_set_power_mode": cb_owner._handle_set_power_mode,
        "get_power_modes": cb_owner._get_power_modes,
        "get_models": cb_owner._get_models,
        "on_model_switch": cb_owner._handle_model_switch,
        "get_rf_status": cb_owner._get_rf_status,
        "get_rf_rssi_history": cb_owner._get_rf_rssi_history,
        "get_rf_devices": cb_owner._get_rf_devices,
        "get_rf_events": cb_owner._get_rf_events,
        "on_rf_start": cb_owner._handle_rf_start,
        "on_rf_stop": cb_owner._handle_rf_stop,
        "on_rf_target": cb_owner._handle_rf_target,
        "on_set_mode_command": cb_owner._handle_set_mode_command,
        "on_alert_classes_change": cb_owner._handle_alert_classes_change,
        "get_class_names": cb_owner._detector.get_class_names,
        "on_rtsp_toggle": cb_owner._handle_rtsp_toggle,
        "get_rtsp_status": cb_owner._get_rtsp_status,
        "on_mavlink_video_toggle": cb_owner._handle_mavlink_video_toggle,
        "on_mavlink_video_tune": cb_owner._handle_mavlink_video_tune,
        "get_mavlink_video_status": cb_owner._get_mavlink_video_status,
        "on_tak_toggle": cb_owner._handle_tak_toggle,
        "get_tak_status": cb_owner._get_tak_status,
        "get_tak_targets": cb_owner._get_tak_targets,
        "add_tak_target": cb_owner._add_tak_target,
        "remove_tak_target": cb_owner._remove_tak_target,
        "get_profiles": cb_owner._get_profiles,
        "on_profile_switch": cb_owner._handle_profile_switch,
        "get_preflight": cb_owner._get_preflight,
        "on_restart_command": cb_owner._handle_restart_command,
        "on_drop_command": cb_owner._handle_drop_command,
        "on_follow_command": cb_owner._handle_follow_command,
        "on_approach_strike_command": cb_owner._handle_approach_strike_command,
        "on_pixel_lock_command": cb_owner._handle_pixel_lock_command,
        "on_approach_abort": cb_owner._handle_approach_abort,
        "get_approach_status": cb_owner._get_approach_status,
        "on_mission_start": cb_owner._handle_mission_start,
        "on_mission_end": cb_owner._handle_mission_end,
        "get_events": cb_owner._get_events,
        "get_event_status": cb_owner._event_logger.get_status,
        "play_tune": cb_owner._play_tune,
    }
    for cb_name, expected in expected_bindings.items():
        assert callbacks[cb_name] is expected, (
            f"{cb_name} not bound to expected handler"
        )

    # Lambda-wrapped config getters can't use identity — verify behavior.
    cb_owner._cfg.read_dict({
        "logging": {"log_dir": "/tmp/logs", "image_dir": "/tmp/imgs"},
    })
    assert callbacks["get_log_dir"]() == "/tmp/logs"
    assert callbacks["get_image_dir"]() == "/tmp/imgs"

    # Spot-check that an invocation forwards args correctly.
    callbacks["on_strike_command"](12)
    cb_owner._handle_strike_command.assert_called_once_with(12)


def test_facade_imports_and_invokes_set_autonomous_controller():
    """Regression guard: pipeline facade MUST wire the live autonomous controller
    into the web server, otherwise /api/autonomy/status returns the idle
    placeholder and /api/autonomy/mode 503s in production — the whole mode
    picker is cosmetic.

    This is a structural check — constructing the full HydraPipeline requires
    hardware + a config file. We assert on the source that both the import
    and the registration call exist in the expected code paths.
    """
    import ast
    import inspect

    from hydra_detect.pipeline import facade

    src = inspect.getsource(facade)
    tree = ast.parse(src)

    # 1. Import must be present
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "..web.server":
            imported.update(a.name for a in node.names)
    # Relative imports read as just "web.server" in some AST versions — look
    # for any import bringing `set_autonomous_controller` into module scope.
    if "set_autonomous_controller" not in imported:
        assert "from ..web.server import" in src or "from hydra_detect.web.server import" in src
        assert "set_autonomous_controller" in src, (
            "facade.py must import set_autonomous_controller"
        )

    # 2. A call site must exist — both register (on autonomous-enabled branch)
    #    and unregister (on shutdown). Count the call occurrences.
    call_count = src.count("set_autonomous_controller(")
    assert call_count >= 2, (
        f"expected ≥2 call sites (register + unregister), found {call_count}"
    )
    assert "set_autonomous_controller(self._autonomous)" in src, (
        "must register the live controller on startup"
    )
    assert "set_autonomous_controller(None)" in src, (
        "must unregister on shutdown to avoid stale snapshots after pipeline stop"
    )
