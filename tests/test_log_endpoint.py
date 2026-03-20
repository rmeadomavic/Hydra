"""Tests for live log file persistence and API endpoint."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path


class TestLogFileSetup:
    def test_rotating_file_handler_creates_log(self):
        """Verify RotatingFileHandler writes to the expected path."""
        from logging.handlers import RotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "hydra.log"
            handler = RotatingFileHandler(
                str(log_path), maxBytes=5 * 1024 * 1024, backupCount=3,
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
            ))
            test_logger = logging.getLogger("test.log.setup")
            test_logger.addHandler(handler)
            test_logger.setLevel(logging.INFO)
            test_logger.info("Test log message")
            handler.flush()

            assert log_path.exists()
            content = log_path.read_text()
            assert "Test log message" in content
            assert "INFO" in content

            test_logger.removeHandler(handler)
            handler.close()
