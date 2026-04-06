"""Control callback surface and adapter for web integrations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PipelineControlAdapter:
    """Thin callback adapter exposed to web/server integrations."""

    pipeline: object

    def callbacks(self) -> dict:
        p = self.pipeline
        return {
            "on_threshold_change": p._handle_threshold_change,
            "on_loiter_command": p._handle_loiter_command,
            "on_target_lock": p._handle_target_lock,
            "on_target_unlock": p._handle_target_unlock,
            "on_strike_command": p._handle_strike_command,
            "get_recent_detections": p._det_logger.get_recent,
            "get_active_tracks": p._get_active_tracks,
            "on_stop_command": p._handle_stop_command,
            "on_pause_command": p._handle_pause_command,
            "on_resume_command": p._handle_resume_command,
            "get_camera_sources": p._get_camera_sources,
            "on_camera_switch": p._handle_camera_switch,
            "on_set_power_mode": p._handle_set_power_mode,
            "get_power_modes": p._get_power_modes,
            "get_models": p._get_models,
            "on_model_switch": p._handle_model_switch,
            "get_log_dir": lambda: p._cfg.get("logging", "log_dir", fallback="./output_data/logs"),
            "get_image_dir": lambda: p._cfg.get(
                "logging", "image_dir", fallback="./output_data/images"
            ),
            "get_rf_status": p._get_rf_status,
            "get_rf_rssi_history": p._get_rf_rssi_history,
            "on_rf_start": p._handle_rf_start,
            "on_rf_stop": p._handle_rf_stop,
            "on_set_mode_command": p._handle_set_mode_command,
            "on_alert_classes_change": p._handle_alert_classes_change,
            "get_class_names": p._detector.get_class_names,
            "on_rtsp_toggle": p._handle_rtsp_toggle,
            "get_rtsp_status": p._get_rtsp_status,
            "on_mavlink_video_toggle": p._handle_mavlink_video_toggle,
            "on_mavlink_video_tune": p._handle_mavlink_video_tune,
            "get_mavlink_video_status": p._get_mavlink_video_status,
            "on_tak_toggle": p._handle_tak_toggle,
            "get_tak_status": p._get_tak_status,
            "get_tak_targets": p._get_tak_targets,
            "add_tak_target": p._add_tak_target,
            "remove_tak_target": p._remove_tak_target,
            "get_profiles": p._get_profiles,
            "on_profile_switch": p._handle_profile_switch,
            "get_preflight": p._get_preflight,
            "on_restart_command": p._handle_restart_command,
            "on_drop_command": p._handle_drop_command,
            "on_follow_command": p._handle_follow_command,
            "on_approach_strike_command": p._handle_approach_strike_command,
            "on_pixel_lock_command": p._handle_pixel_lock_command,
            "on_approach_abort": p._handle_approach_abort,
            "get_approach_status": p._get_approach_status,
            "on_mission_start": p._handle_mission_start,
            "on_mission_end": p._handle_mission_end,
            "get_events": p._get_events,
            "get_event_status": p._event_logger.get_status,
            "play_tune": p._play_tune,
        }
