"""Model manifest — schema, validation, and generation for YOLO model files."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"


def compute_file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(manifest_path: Path) -> list[dict[str, Any]] | None:
    """Load and return manifest entries, or None if not found / invalid."""
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text())
        if not isinstance(data, list):
            logger.warning("Manifest is not a JSON array: %s", manifest_path)
            return None
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load manifest: %s", exc)
        return None


def validate_model(entry: dict[str, Any], model_dirs: list[Path]) -> tuple[bool, str]:
    """Validate a manifest entry against the actual model file.

    Returns (ok, reason).
    """
    filename = entry.get("filename", "")
    expected_hash = entry.get("sha256", "")

    if not filename:
        return False, "missing filename"
    if not expected_hash:
        return False, "missing sha256"

    # Find the file
    for d in model_dirs:
        candidate = d / filename
        if candidate.exists():
            actual_hash = compute_file_hash(candidate)
            if actual_hash != expected_hash:
                return False, f"checksum mismatch (expected {expected_hash[:16]}...)"
            classes = entry.get("classes", [])
            if not classes:
                return False, "empty class list"
            return True, "ok"

    return False, f"file not found in {[str(d) for d in model_dirs]}"


def generate_manifest(*model_dirs: str) -> list[dict[str, Any]]:
    """Scan model directories and generate manifest entries.

    Returns a list of manifest entries (without class names — those must be
    filled in manually or by loading the model).
    """
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for dir_str in model_dirs:
        d = Path(dir_str)
        if not d.is_dir():
            continue
        for pattern in ("*.pt", "*.engine", "*.onnx"):
            for path in sorted(d.glob(pattern)):
                if path.name in seen:
                    continue
                seen.add(path.name)
                size_mb = round(path.stat().st_size / (1024 * 1024), 1)
                file_hash = compute_file_hash(path)
                entries.append({
                    "filename": path.name,
                    "classes": [],  # must be populated manually or by model introspection
                    "input_resolution": [640, 640],
                    "sha256": file_hash,
                    "size_mb": size_mb,
                    "description": "",
                })
    return entries


def auto_update_manifest(models_dir: Path) -> bool:
    """Scan models directory and add any new .pt files to manifest.json.

    Returns True if manifest was updated.
    """
    if not models_dir.is_dir():
        return False

    manifest_path = models_dir / MANIFEST_FILENAME
    manifest = load_manifest(manifest_path) or []
    existing_files = {e["filename"] for e in manifest}

    updated = False
    for pt_file in sorted(models_dir.glob("*.pt")):
        if pt_file.name not in existing_files:
            file_hash = compute_file_hash(pt_file)
            size_mb = round(pt_file.stat().st_size / (1024 * 1024), 1)
            entry = {
                "filename": pt_file.name,
                "sha256": file_hash,
                "size_mb": size_mb,
                "input_size": 416,
                "classes": [],
            }
            manifest.append(entry)
            updated = True
            logger.info(
                "Auto-manifest: added %s (%.1f MB)", pt_file.name, size_mb
            )

    if updated:
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        logger.info(
            "Manifest updated: %s (%d models)", manifest_path, len(manifest)
        )

    return updated


def main() -> None:
    """CLI: generate or update manifest.json in a models directory."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m hydra_detect.model_manifest <models_dir> [models_dir2 ...]")
        print("  Generates manifest.json in the first directory.")
        sys.exit(1)

    dirs = sys.argv[1:]
    output_dir = Path(dirs[0])

    manifest_path = output_dir / MANIFEST_FILENAME
    existing = load_manifest(manifest_path) or []
    existing_by_name = {e["filename"]: e for e in existing}

    entries = generate_manifest(*dirs)

    # Merge: keep existing class names and descriptions if the hash matches
    merged: list[dict[str, Any]] = []
    for entry in entries:
        old = existing_by_name.get(entry["filename"])
        if old and old.get("sha256") == entry["sha256"]:
            # Keep manually-curated fields from old entry
            entry["classes"] = old.get("classes", entry["classes"])
            entry["description"] = old.get("description", entry["description"])
            entry["input_resolution"] = old.get("input_resolution", entry["input_resolution"])
        merged.append(entry)

    manifest_path.write_text(json.dumps(merged, indent=2) + "\n")
    print(f"Wrote {len(merged)} entries to {manifest_path}")
    for e in merged:
        status = "classes OK" if e["classes"] else "NEEDS CLASSES"
        print(f"  {e['filename']} ({e['size_mb']} MB) — {status}")


if __name__ == "__main__":
    main()
