"""Entry point: python -m hydra_detect [--config config.ini]"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
from pathlib import Path

from .pipeline import Pipeline

logger = logging.getLogger(__name__)


def _apply_sim_overrides(cfg: configparser.ConfigParser) -> None:
    """Override config values for SITL simulation mode."""
    cfg.set("camera", "source_type", "file")
    cfg.set("camera", "source", "sim_video.mp4")
    cfg.set("mavlink", "enabled", "true")
    cfg.set("mavlink", "connection_string", "udp:127.0.0.1:14550")
    cfg.set("mavlink", "baud", "115200")
    # Sim GPS for when no SITL is running
    has_lat = (cfg.has_option("mavlink", "sim_gps_lat")
               and cfg.get("mavlink", "sim_gps_lat").strip())
    if not has_lat:
        cfg.set("mavlink", "sim_gps_lat", "35.0527")  # Default sim location
    has_lon = (cfg.has_option("mavlink", "sim_gps_lon")
               and cfg.get("mavlink", "sim_gps_lon").strip())
    if not has_lon:
        cfg.set("mavlink", "sim_gps_lon", "-79.4927")
    # Disable hardware-dependent features
    if cfg.has_section("osd"):
        cfg.set("osd", "enabled", "false")
    if cfg.has_section("servo_tracking"):
        cfg.set("servo_tracking", "enabled", "false")
    if cfg.has_section("rf_homing"):
        cfg.set("rf_homing", "enabled", "false")
    logger.info("SITL simulation mode — MAVLink UDP, file camera, sim GPS")


def _apply_camera_source_override(cfg: configparser.ConfigParser, source: str) -> None:
    """Override camera source and auto-detect source type."""
    cfg.set("camera", "source", source)
    if source.isdigit():
        cfg.set("camera", "source_type", "usb")
    elif source.startswith("rtsp"):
        cfg.set("camera", "source_type", "rtsp")
    elif Path(source).suffix in (".mp4", ".avi", ".mkv", ".mov"):
        cfg.set("camera", "source_type", "file")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Hydra Detect v2.0")
    parser.add_argument(
        "-c", "--config",
        default="config.ini",
        help="Path to config.ini (default: config.ini)",
    )
    parser.add_argument(
        "--vehicle",
        default=os.environ.get("HYDRA_VEHICLE"),
        help="Vehicle profile (e.g. drone, usv, ugv). "
             "Overrides base config with [vehicle.<name>] sections. "
             "Can also be set via HYDRA_VEHICLE env var.",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="SITL simulation mode — auto-configures for ArduPilot SITL",
    )
    parser.add_argument(
        "--camera-source",
        help="Override camera source (e.g., 0 for webcam, path to video file)",
    )
    args = parser.parse_args()

    # Pre-load config so we can apply --sim and --camera-source overrides
    # before Pipeline.__init__ reads the values.
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(args.config)

    if args.sim:
        _apply_sim_overrides(cfg)

    if args.camera_source:
        _apply_camera_source_override(cfg, args.camera_source)

    pipeline = Pipeline(config_path=args.config, vehicle=args.vehicle, cfg_override=cfg)
    pipeline.start()

    # Hard exit to prevent "terminate called without an active exception"
    # from CUDA/PyTorch/OpenCV daemon thread cleanup races on Jetson.
    os._exit(0)


if __name__ == "__main__":
    main()
