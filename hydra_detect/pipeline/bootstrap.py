"""Bootstrap helpers for Pipeline construction."""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass
from pathlib import Path

from ..detectors.yolo_detector import YOLODetector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapContext:
    cfg: configparser.ConfigParser
    callsign: str
    vehicle: str | None
    project_dir: Path
    models_dir: Path


class PipelineBootstrap:
    """Config and dependency bootstrapping helpers."""

    def load_config(
        self,
        config_path: str,
        vehicle: str | None = None,
        cfg_override: configparser.ConfigParser | None = None,
    ) -> BootstrapContext:
        if cfg_override is not None:
            cfg = cfg_override
        else:
            cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
            cfg.read(config_path)

        if vehicle:
            vehicle_section = f"vehicle.{vehicle}"
            if cfg.has_section(vehicle_section):
                for key, value in cfg.items(vehicle_section):
                    if "." not in key:
                        logger.warning(
                            "Vehicle config key %r missing section prefix "
                            "(expected section.option)",
                            key,
                        )
                        continue
                    section, option = key.split(".", 1)
                    if not cfg.has_section(section):
                        cfg.add_section(section)
                    cfg.set(section, option, value)
            else:
                logger.error(
                    "Vehicle profile %r not found (no [%s] section in config)",
                    vehicle,
                    vehicle_section,
                )

        callsign = cfg.get("tak", "callsign", fallback="HYDRA-1")
        if callsign == "HYDRA-1" and vehicle:
            callsign = f"HYDRA-{vehicle.upper()}"
        if not cfg.has_section("tak"):
            cfg.add_section("tak")
        cfg.set("tak", "callsign", callsign)

        project_dir = Path(config_path).resolve().parent
        models_dir = project_dir / "models"
        return BootstrapContext(
            cfg=cfg,
            callsign=callsign,
            vehicle=vehicle,
            project_dir=project_dir,
            models_dir=models_dir,
        )


def build_detector(cfg: configparser.ConfigParser, models_dir: Path | None = None) -> YOLODetector:
    """Build a YOLO detector from config."""
    classes_raw = cfg.get("detector", "yolo_classes", fallback="")
    classes = None
    if classes_raw.strip():
        try:
            classes = [int(c.strip()) for c in classes_raw.split(",") if c.strip()]
            classes = [c for c in classes if c >= 0] or None
        except ValueError:
            logger.error("Invalid yolo_classes config (comma-separated ints): %s", classes_raw)
            classes = None

    model_name = cfg.get("detector", "yolo_model", fallback="yolov8n.pt")
    model_path = model_name
    project_dir = models_dir.parent if models_dir is not None else None
    for candidate_dir in [Path("/models"), models_dir, project_dir]:
        if candidate_dir is None:
            continue
        candidate = candidate_dir / model_name
        if candidate.exists():
            model_path = str(candidate)
            break

    imgsz_raw = cfg.get("detector", "yolo_imgsz", fallback="")
    imgsz = int(imgsz_raw) if imgsz_raw.strip() else None
    return YOLODetector(
        model_path=model_path,
        confidence=cfg.getfloat("detector", "yolo_confidence", fallback=0.45),
        classes=classes,
        imgsz=imgsz,
    )
