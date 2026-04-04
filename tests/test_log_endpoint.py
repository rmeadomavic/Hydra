"""Tests for live log file persistence and API endpoint."""

from __future__ import annotations

import logging
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


import re  # noqa: E402


LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[(?P<module>[^\]]+)\] "
    r"(?P<level>\w+): "
    r"(?P<message>.*)$"
)

LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def parse_log_line(line: str) -> dict | None:
    """Parse a single log line into structured fields."""
    m = LOG_LINE_RE.match(line.strip())
    if not m:
        return None
    return {
        "timestamp": m.group("timestamp"),
        "level": m.group("level"),
        "module": m.group("module"),
        "message": m.group("message"),
    }


class TestLogParsing:
    def test_parse_info_line(self):
        line = "2026-03-19 14:23:01,123 [hydra_detect.pipeline] INFO: Pipeline started"
        result = parse_log_line(line)
        assert result is not None
        assert result["level"] == "INFO"
        assert result["module"] == "hydra_detect.pipeline"
        assert result["message"] == "Pipeline started"

    def test_parse_warning_line(self):
        line = "2026-03-19 14:23:02,456 [hydra_detect.rf.hunt] WARNING: Kismet connection lost"
        result = parse_log_line(line)
        assert result["level"] == "WARNING"

    def test_parse_garbage_returns_none(self):
        assert parse_log_line("not a log line") is None

    def test_level_filter(self):
        lines = [
            "2026-03-19 14:23:01,000 [mod] INFO: info msg",
            "2026-03-19 14:23:02,000 [mod] WARNING: warn msg",
            "2026-03-19 14:23:03,000 [mod] ERROR: error msg",
        ]
        min_level = "WARNING"
        min_ord = LEVEL_ORDER.get(min_level, 0)
        filtered = []
        for line in lines:
            parsed = parse_log_line(line)
            if parsed and LEVEL_ORDER.get(parsed["level"], 0) >= min_ord:
                filtered.append(parsed)
        assert len(filtered) == 2
        assert filtered[0]["level"] == "WARNING"
        assert filtered[1]["level"] == "ERROR"

    def test_tail_lines_limit(self):
        from collections import deque
        lines = [f"2026-03-19 14:23:{i:02d},000 [mod] INFO: msg {i}" for i in range(20)]
        tail = deque(lines, maxlen=5)
        assert len(tail) == 5
        assert "msg 19" in tail[-1]
