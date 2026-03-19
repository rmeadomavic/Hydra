"""System and hardware utility functions for Jetson/Linux platforms."""

from __future__ import annotations

import glob
import subprocess
import threading
from pathlib import Path


# ---------------------------------------------------------------------------
# nvpmodel async cache — keeps power_mode fresh without blocking the hot loop
# ---------------------------------------------------------------------------

_nvpmodel_cache: dict = {"power_mode": None}
_nvpmodel_lock: threading.Lock = threading.Lock()
_nvpmodel_refresh_running: bool = False


def query_nvpmodel_background() -> None:
    """Run 'nvpmodel -q' in a background thread and update the cache."""
    global _nvpmodel_refresh_running
    try:
        result = subprocess.run(
            ["nvpmodel", "-q"], capture_output=True, text=True, timeout=2,
        )
        power_mode: str | None = None
        for line in result.stdout.splitlines():
            if "NV Power Mode" in line:
                power_mode = line.split(":", 1)[1].strip()
                break
        with _nvpmodel_lock:
            _nvpmodel_cache["power_mode"] = power_mode
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass  # leave cache unchanged on failure
    finally:
        with _nvpmodel_lock:
            _nvpmodel_refresh_running = False


def refresh_nvpmodel_async() -> None:
    """Trigger a background refresh of the nvpmodel cache if none is running."""
    global _nvpmodel_refresh_running
    with _nvpmodel_lock:
        if _nvpmodel_refresh_running:
            return
        _nvpmodel_refresh_running = True
    t = threading.Thread(target=query_nvpmodel_background, daemon=True)
    t.start()


def query_nvpmodel_sync() -> str | None:
    """Run 'nvpmodel -q' synchronously (for use outside the hot loop)."""
    try:
        result = subprocess.run(
            ["nvpmodel", "-q"], capture_output=True, text=True, timeout=2,
        )
        for line in result.stdout.splitlines():
            if "NV Power Mode" in line:
                mode = line.split(":", 1)[1].strip()
                with _nvpmodel_lock:
                    _nvpmodel_cache["power_mode"] = mode
                return mode
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return None


# ---------------------------------------------------------------------------
# Thermal / hardware stats
# ---------------------------------------------------------------------------

def read_thermal(zone: str) -> float | None:
    """Read a thermal zone temperature in °C."""
    try:
        raw = Path(f"/sys/devices/virtual/thermal/thermal_zone{zone}/temp").read_text().strip()
        return round(int(raw) / 1000.0, 1)
    except (FileNotFoundError, ValueError, PermissionError):
        return None


def read_jetson_stats() -> dict:
    """Read Jetson system stats from sysfs; power_mode comes from async cache."""
    stats: dict = {}

    # Temperatures
    stats["gpu_temp_c"] = read_thermal("1") or read_thermal("0")
    stats["cpu_temp_c"] = read_thermal("0")

    # RAM usage (shared CPU/GPU on Jetson)
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = total - available
        stats["ram_used_mb"] = round(used / 1024)
        stats["ram_total_mb"] = round(total / 1024)
    except (FileNotFoundError, ValueError):
        stats["ram_used_mb"] = None
        stats["ram_total_mb"] = None

    # GPU utilization (0-1000 scale -> percentage)
    try:
        load = int(Path("/sys/devices/platform/gpu.0/load").read_text().strip())
        stats["gpu_load_pct"] = round(load / 10.0, 1)
    except (FileNotFoundError, ValueError, PermissionError):
        stats["gpu_load_pct"] = None

    # Power mode — read from async-updated cache (never blocks the hot loop)
    with _nvpmodel_lock:
        stats["power_mode"] = _nvpmodel_cache["power_mode"]

    return stats


# ---------------------------------------------------------------------------
# Power mode management
# ---------------------------------------------------------------------------

def list_power_modes() -> list[dict]:
    """Parse nvpmodel config for available power modes."""
    modes: list[dict] = []
    try:
        r = subprocess.run(
            ["nvpmodel", "-p", "--verbose"],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stderr.splitlines() + r.stdout.splitlines():
            if "POWER_MODEL: ID=" in line:
                # "NVPM VERB: POWER_MODEL: ID=0 NAME=15W"
                parts = line.split("POWER_MODEL:")[1].strip()
                mode_id = int(parts.split("ID=")[1].split()[0])
                mode_name = parts.split("NAME=")[1].strip()
                modes.append({"id": mode_id, "name": mode_name})
    except (FileNotFoundError, Exception):
        pass
    return modes


def set_power_mode(mode_id: int) -> dict:
    """Set Jetson power mode by ID and optionally enable jetson_clocks."""
    result: dict = {"status": "ok", "actions": []}
    try:
        r = subprocess.run(
            ["nvpmodel", "-m", str(mode_id)],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            result["actions"].append(f"Power mode set to ID {mode_id}")
        else:
            result["status"] = "error"
            result["error"] = r.stderr.strip() or r.stdout.strip()
            return result
    except (FileNotFoundError, Exception) as e:
        result["status"] = "error"
        result["error"] = f"nvpmodel not available: {e}"
        return result
    # Enable jetson_clocks for max performance modes
    try:
        r = subprocess.run(
            ["jetson_clocks"], capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            result["actions"].append("jetson_clocks enabled")
    except (FileNotFoundError, Exception):
        pass
    return result


# ---------------------------------------------------------------------------
# Model file discovery
# ---------------------------------------------------------------------------

def list_models(*model_dirs: str) -> list[dict]:
    """List available YOLO model files across one or more directories.

    Searches each directory for .pt, .engine, and .onnx files.
    Deduplicates by filename (first directory wins).
    """
    models: list[dict] = []
    seen_names: set[str] = set()
    for models_dir in model_dirs:
        if not Path(models_dir).is_dir():
            continue
        for pattern in ("*.pt", "*.engine", "*.onnx"):
            for path in sorted(glob.glob(f"{models_dir}/{pattern}")):
                name = Path(path).name
                if name in seen_names:
                    continue
                seen_names.add(name)
                size_mb = round(Path(path).stat().st_size / (1024 * 1024), 1)
                models.append({"name": name, "path": path, "size_mb": size_mb})
    return models
