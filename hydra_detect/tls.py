"""Self-signed TLS certificate generation for field deployment."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_tls_cert(cert_path: str, key_path: str) -> bool:
    """Generate a self-signed TLS cert if it doesn't exist. Returns True on success."""
    cert = Path(cert_path)
    key = Path(key_path)

    if cert.exists() and key.exists():
        logger.info("TLS cert found: %s", cert)
        return True

    # Create parent directory
    cert.parent.mkdir(parents=True, exist_ok=True)
    key.parent.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(key), "-out", str(cert),
                "-days", "3650", "-nodes",
                "-subj", "/CN=hydra-detect/O=SORCC/C=US",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        logger.info("Self-signed TLS cert generated: %s", cert)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.error("Failed to generate TLS cert: %s", exc)
        return False
