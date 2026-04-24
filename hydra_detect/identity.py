"""Per-unit identity generation for Hydra Detect.

Generates callsign, API token, and web password on first Platform Setup.
Passwords are returned plaintext exactly once to the caller; only the hash
is persisted. Tokens are never logged in full.
"""

from __future__ import annotations

import configparser
import hashlib
import logging
import os
import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedded wordlist for passphrase generation.
# Short, unambiguous words. No homophones that would cause read-back errors.
# ---------------------------------------------------------------------------
_WORDLIST: list[str] = [
    "alpha", "amber", "anvil", "apex", "ash",
    "beacon", "birch", "bolt", "bridge", "brook",
    "cable", "cedar", "chalk", "chart", "cliff",
    "cobalt", "cord", "crest", "croft", "cross",
    "delta", "depth", "drift", "dune", "dust",
    "echo", "ember", "falcon", "fern", "field",
    "flint", "forge", "frost", "gale", "gate",
    "glass", "grain", "grave", "gravel", "hawk",
    "haze", "helm", "heron", "hill", "hinge",
    "hull", "iron", "jade", "kite", "knot",
    "lake", "lance", "lantern", "lark", "leaf",
    "ledge", "lime", "linden", "link", "lock",
    "maple", "marsh", "mast", "mesh", "mist",
    "moth", "mound", "moss", "nail", "nook",
    "north", "oak", "oar", "onyx", "orbit",
    "otter", "peak", "peat", "pine", "pitch",
    "plain", "plank", "plum", "pond", "post",
    "quartz", "rail", "ramp", "rapid", "raven",
    "reed", "reef", "relay", "ridge", "ring",
    "rivet", "robin", "rock", "root", "rope",
    "rowan", "rust", "sage", "sand", "shard",
    "shale", "shelf", "signal", "slate", "slope",
    "smoke", "soil", "span", "spike", "spur",
    "stark", "steel", "stem", "stone", "storm",
    "straw", "swift", "thorn", "tide", "tinder",
    "torch", "tower", "track", "trail", "twig",
    "vale", "vault", "veil", "vent", "vine",
    "violet", "wall", "warden", "wave", "weld",
    "willow", "wind", "wing", "wire", "wren",
    "yard", "zinc",
]


@dataclass
class UnitIdentity:
    """Identity fields for a single Hydra unit."""
    hostname: str
    callsign: str
    api_token: str
    web_password_hash: str
    software_version: str
    commit_hash: str
    generated_at: str  # ISO 8601 UTC

    def token_redacted(self) -> str:
        """Return first 4 chars + *** — safe for log lines."""
        if len(self.api_token) >= 4:
            return self.api_token[:4] + "***"
        return "***"


def _get_software_version(repo_root: Path | None = None) -> str:
    """Read software version from package metadata or __version__.py."""
    # Try package metadata first (installed package)
    try:
        from importlib.metadata import version
        return version("hydra_detect")
    except Exception:
        pass

    # Fall back to __version__.py in the package directory
    try:
        version_file = Path(__file__).parent / "__version__.py"
        if not version_file.exists():
            # Also try the repo root convention
            if repo_root:
                version_file = repo_root / "hydra_detect" / "__version__.py"
        if version_file.exists():
            ns: dict = {}
            exec(version_file.read_text(), ns)  # noqa: S102
            ver = ns.get("__version__")
            if ver:
                return str(ver)
    except Exception:
        pass

    # Try __init__.py __version__
    try:
        from hydra_detect import __version__  # type: ignore[attr-defined]
        return str(__version__)
    except Exception:
        pass

    return "unknown"


def _get_commit_hash(repo_root: Path | None = None) -> str:
    """Get current git HEAD commit hash."""
    cwd = str(repo_root) if repo_root else str(Path(__file__).parent.parent)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _generate_passphrase(word_count: int = 4) -> str:
    """Generate a passphrase from the embedded wordlist.

    Returns a dash-separated string of word_count words selected with
    cryptographic randomness. No external network calls.
    """
    word_count = max(4, min(6, word_count))
    words = [secrets.choice(_WORDLIST) for _ in range(word_count)]
    return "-".join(words)


def _hash_password(plaintext: str) -> str:
    """Hash a password using pbkdf2-sha256 (stdlib only, no external deps).

    Returns a storable string: ``pbkdf2:sha256:<iterations>:<hex_salt>:<hex_dk>``.
    Uses 480,000 iterations (OWASP 2023 minimum for pbkdf2-sha256).
    """
    salt = secrets.token_bytes(16)
    iterations = 480_000
    dk = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt, iterations)
    return f"pbkdf2:sha256:{iterations}:{salt.hex()}:{dk.hex()}"


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored pbkdf2 hash.

    Supports the ``pbkdf2:sha256:<iter>:<salt_hex>:<dk_hex>`` format
    produced by ``_hash_password``.
    """
    try:
        parts = stored_hash.split(":")
        if len(parts) != 5 or parts[0] != "pbkdf2" or parts[1] != "sha256":
            return False
        iterations = int(parts[2])
        salt = bytes.fromhex(parts[3])
        stored_dk = bytes.fromhex(parts[4])
        dk = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt, iterations)
        return secrets.compare_digest(dk, stored_dk)
    except Exception:
        return False


def generate_identity(
    unit_number: int,
    profile: str,
    repo_root: Path | None = None,
) -> tuple[UnitIdentity, str]:
    """Generate a complete unit identity.

    Parameters
    ----------
    unit_number:
        Integer unit number (1-99). Padded to 2 digits in callsign/hostname.
    profile:
        Mission profile string (e.g. "ugv", "drone", "usv", "fw").
        Uppercased in the callsign.
    repo_root:
        Optional path to the repo root for git/version lookups.
        Defaults to parent of this file's directory.

    Returns
    -------
    (UnitIdentity, plaintext_password)
        The identity dataclass with hashed password, and the plaintext
        password for one-time display. Caller MUST display then discard.
    """
    if not 1 <= unit_number <= 99:
        raise ValueError(f"unit_number must be 1-99, got {unit_number}")
    if not profile or not profile.isidentifier():
        raise ValueError(f"profile must be a valid identifier, got {repr(profile)}")

    hostname = f"hydra-{unit_number:02d}"
    callsign = f"HYDRA-{unit_number:02d}-{profile.upper()}"
    api_token = secrets.token_urlsafe(32)
    plaintext_password = _generate_passphrase(word_count=4)
    web_password_hash = _hash_password(plaintext_password)
    software_version = _get_software_version(repo_root)
    commit_hash = _get_commit_hash(repo_root)
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    identity = UnitIdentity(
        hostname=hostname,
        callsign=callsign,
        api_token=api_token,
        web_password_hash=web_password_hash,
        software_version=software_version,
        commit_hash=commit_hash,
        generated_at=generated_at,
    )

    logger.info(
        "Identity generated: callsign=%s hostname=%s token=%s version=%s commit=%s",
        callsign,
        hostname,
        identity.token_redacted(),
        software_version,
        commit_hash[:8] if commit_hash != "unknown" else "unknown",
    )

    return identity, plaintext_password


def write_identity_to_config(
    identity: UnitIdentity,
    config_path: Path | str,
) -> None:
    """Write [identity] section to config.ini using an atomic write.

    Uses the same write-to-.tmp -> fsync -> os.replace pattern as config_api.py.
    Existing config sections are preserved.
    """
    config_path = Path(config_path)
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(config_path)

    if not cfg.has_section("identity"):
        cfg.add_section("identity")

    cfg.set("identity", "hostname", identity.hostname)
    cfg.set("identity", "callsign", identity.callsign)
    cfg.set("identity", "api_token", identity.api_token)
    cfg.set("identity", "web_password_hash", identity.web_password_hash)
    cfg.set("identity", "software_version", identity.software_version)
    cfg.set("identity", "commit_hash", identity.commit_hash)
    cfg.set("identity", "generated_at", identity.generated_at)

    tmp_path = Path(str(config_path) + ".tmp")
    try:
        with open(tmp_path, "w") as f:
            cfg.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config_path)
        logger.info(
            "Identity written to %s (callsign=%s token=%s)",
            config_path,
            identity.callsign,
            identity.token_redacted(),
        )
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def load_identity_from_config(config_path: Path | str) -> UnitIdentity | None:
    """Read [identity] from config.ini. Returns None if section absent or incomplete."""
    config_path = Path(config_path)
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(config_path)

    if not cfg.has_section("identity"):
        return None

    required = [
        "hostname", "callsign", "api_token",
        "web_password_hash", "software_version",
        "commit_hash", "generated_at",
    ]
    section = cfg["identity"]
    for key in required:
        if not section.get(key, "").strip():
            return None

    return UnitIdentity(
        hostname=section["hostname"].strip(),
        callsign=section["callsign"].strip(),
        api_token=section["api_token"].strip(),
        web_password_hash=section["web_password_hash"].strip(),
        software_version=section["software_version"].strip(),
        commit_hash=section["commit_hash"].strip(),
        generated_at=section["generated_at"].strip(),
    )


# Pattern for a fully-qualified Hydra callsign: HYDRA-NN-PROFILE
_CALLSIGN_RE = __import__("re").compile(r"^HYDRA-\d{2}-[A-Z0-9]+$")


def is_callsign_valid(callsign: str) -> bool:
    """Return True if callsign matches HYDRA-NN-PROFILE pattern."""
    return bool(_CALLSIGN_RE.match(callsign.upper()))
