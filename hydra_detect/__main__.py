"""Entry point: python -m hydra_detect [--config config.ini]"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import sys
from pathlib import Path

from .pipeline import Pipeline

logger = logging.getLogger(__name__)


def _run_boot_migrations(config_path: str) -> None:
    """Run pending config schema migrations before Pipeline() is constructed.

    Runs after basicConfig() (so migration logs go to the same handler) but
    before cfg.read() (so the pipeline sees the migrated file). Exits nonzero
    on MigrationError; a failed migration leaves config state unknown.
    """
    from .config_migrate import MigrationError, run_migrations

    try:
        result = run_migrations(Path(config_path))
    except MigrationError as exc:
        logger.critical(
            "Config migration failed; refusing to start pipeline: %s", exc
        )
        sys.exit(1)

    if result.applied:
        logger.info(
            "Config migrated v%d → v%d | applied: %s | backup: %s",
            result.from_version,
            result.to_version,
            result.applied,
            result.backup_path,
        )
    else:
        logger.debug(
            "Config schema at v%d; no migrations needed", result.to_version
        )


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


def _wire_ambient_rf(cfg: configparser.ConfigParser) -> None:
    """Register an AmbientScanBuffer with the web server; start the
    Kismet poller if ``[kismet]`` is present and enabled.

    The buffer is *always* registered — even with no Kismet, the
    ``/api/rf/ambient_scan`` endpoint then returns an empty sample set
    instead of the idle/disabled shape. The poller is the only optional
    piece: missing or disabled config leaves the buffer empty but alive.
    """
    from .rf import AmbientScanBuffer, KismetPoller
    from .web import server as web_server

    buffer = AmbientScanBuffer()
    web_server.set_rf_ambient_scan(buffer)

    if not cfg.has_section("kismet"):
        logger.info(
            "[kismet] section absent — ambient scan buffer registered, "
            "poller not started",
        )
        return
    kc = cfg["kismet"]
    if not kc.getboolean("enabled", fallback=True):
        logger.info("[kismet] enabled=false — poller not started")
        return
    host = kc.get("host", fallback="").strip()
    if not host:
        logger.warning("[kismet] host empty — poller not started")
        return
    try:
        poller = KismetPoller(
            buffer,
            host=host,
            user=kc.get("user", fallback=""),
            password=kc.get("password", fallback=""),
            poll_interval_sec=kc.getfloat(
                "poll_interval_sec", fallback=0.5,
            ),
            timeout_sec=kc.getfloat("timeout_sec", fallback=2.0),
            max_samples_per_cycle=kc.getint(
                "max_samples_per_cycle", fallback=50,
            ),
        )
        poller.start()
    except Exception as exc:
        logger.warning("KismetPoller init failed: %s", exc)


def _wire_audit_file_sink(cfg: configparser.ConfigParser) -> None:
    """Attach a durable JSONL audit sink to ``hydra.audit`` if enabled.

    Additive to the in-memory ring already used by /api/audit/summary —
    the two sinks coexist on the same logger.
    """
    from .audit import attach_file_sink, get_default_file_sink
    try:
        sink = get_default_file_sink(cfg)
    except Exception as exc:
        logger.warning("audit file sink init failed: %s", exc)
        return
    if sink is None:
        logger.info("[audit] enabled=false — JSONL file sink not attached")
        return
    try:
        attach_file_sink(sink)
        logger.info("[audit] JSONL sink attached at %s", sink.path)
    except Exception as exc:
        logger.warning("audit file sink attach failed: %s", exc)


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

    # Run config schema migrations before anything reads config values.
    # Must happen before cfg.read() so the pipeline sees the migrated file.
    _run_boot_migrations(args.config)

    # Normalize empty string to None. When systemd expands an unset
    # environment variable, HYDRA_VEHICLE arrives as "" rather than
    # unset, which would otherwise propagate an empty profile through
    # to /api/config/effective and facade.py._vehicle.
    if args.vehicle == "":
        args.vehicle = None

    # Pre-load config so we can apply --sim and --camera-source overrides
    # before Pipeline.__init__ reads the values.
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(args.config)

    if args.sim:
        _apply_sim_overrides(cfg)

    if args.camera_source:
        _apply_camera_source_override(cfg, args.camera_source)

    from .identity_boot import maybe_generate_identity
    maybe_generate_identity(args.config)

    pipeline = Pipeline(config_path=args.config, vehicle=args.vehicle, cfg_override=cfg)

    # Wire the ambient-RF scan buffer + Kismet poller *before* the
    # pipeline starts its web server so the first request to
    # /api/rf/ambient_scan already sees the live buffer.
    _wire_ambient_rf(cfg)

    # Attach durable JSONL audit sink alongside the in-memory ring.
    _wire_audit_file_sink(cfg)

    pipeline.start()

    # Hard exit to prevent "terminate called without an active exception"
    # from CUDA/PyTorch/OpenCV daemon thread cleanup races on Jetson.
    os._exit(0)


if __name__ == "__main__":
    main()
