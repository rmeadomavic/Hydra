"""Thread-safe bounded sink for hydra.audit events.

Powers ``/api/audit/summary``. Designed to be driven in two complementary
ways:

1. **Automatic capture** via a ``logging.Handler`` attached to the
   ``hydra.audit`` logger (see ``attach_to_logger``). Every audit record
   emitted anywhere in the codebase — TAK rejections, approach arm/abort,
   strike/drop, HMAC failures — lands in the sink without the caller
   needing to know the sink exists.

2. **Direct push** via ``AuditSink.push()`` when a caller already holds a
   structured record and does not want the log-message round trip.

Design invariants:
- Bounded ring (``_RECENT_MAXLEN``) — newest events evict oldest.
- Single ``threading.Lock`` protects all writes and reads.
- ``window_seconds`` filtering is applied lazily on read so we only pay
  for it when the endpoint is polled.
- No external dependency — classification is a small lookup table driven
  by the message text produced by the existing audit emitters.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

# Cap on retained audit events — last N are kept, older are evicted.
_RECENT_MAXLEN = 500

# Default roll-up window (seconds) for the summary endpoint.
_DEFAULT_WINDOW_SEC = 3600

# Event kinds tallied by the summary endpoint. These match the fields
# expected by the dashboard security panel. Unknown kinds are counted
# under "other".
_KINDS = (
    "tak_accepted",
    "tak_rejected",
    "approach_arm_events",
    "approach_abort_events",
    "strike_events",
    "drop_events",
    "hmac_invalid_events",
    "other",
)


def _classify(message: str) -> str:
    """Map an audit log message to a summary-bucket kind.

    The classification rules hug the existing emitters in
    ``hydra_detect.tak.tak_input``, ``hydra_detect.approach``, etc. When
    wiring new audit lines, either match one of the prefixes below or
    call ``AuditSink.push(kind=...)`` directly.
    """
    if not message:
        return "other"
    upper = message.upper()
    if "HMAC_INVALID" in upper or "HMAC VERIFICATION FAILED" in upper:
        return "hmac_invalid_events"
    if upper.startswith("TAK_CMD_REJECTED") or "TAK CMD REJECTED" in upper:
        return "tak_rejected"
    if upper.startswith("TAK_CMD_ACCEPTED") or "TAK CMD ACCEPTED" in upper:
        return "tak_accepted"
    if "APPROACH DROP" in upper:
        return "drop_events"
    if "APPROACH STRIKE" in upper:
        return "strike_events"
    if "APPROACH ABORT" in upper:
        return "approach_abort_events"
    if (
        "APPROACH" in upper
        and ("START" in upper or "ARM" in upper or "PIXEL_LOCK" in upper)
    ):
        return "approach_arm_events"
    if "STRIKE" in upper:
        return "strike_events"
    if "DROP" in upper:
        return "drop_events"
    return "other"


class AuditSink:
    """Bounded ring of audit events with a windowed summary view."""

    def __init__(self, maxlen: int = _RECENT_MAXLEN) -> None:
        self._events: deque[dict] = deque(maxlen=int(maxlen))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Push paths
    # ------------------------------------------------------------------
    def push(
        self,
        *,
        kind: str,
        message: str = "",
        ref: str | None = None,
        operator: str | None = None,
        ts: float | None = None,
    ) -> None:
        """Append a classified audit event to the ring.

        Cheap and thread-safe. Callers that do not know the kind can pass
        ``kind="auto"`` to trigger classification on the message text.
        """
        kind_resolved = kind if kind != "auto" else _classify(message)
        if kind_resolved not in _KINDS:
            kind_resolved = "other"
        entry = {
            "ts": float(ts) if ts is not None else time.time(),
            "kind": kind_resolved,
            "message": str(message or ""),
            "ref": ref,
            "operator": operator,
        }
        with self._lock:
            self._events.append(entry)

    def handle_log_record(self, record: logging.LogRecord) -> None:
        """Ingest a ``hydra.audit`` logging record without format side effects."""
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        self.push(
            kind="auto",
            message=msg,
            ts=getattr(record, "created", None),
        )

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------
    def summary(
        self,
        window_seconds: int = _DEFAULT_WINDOW_SEC,
        recent_limit: int = 50,
    ) -> dict:
        """Return the /api/audit/summary shape over the last window."""
        now = time.time()
        cutoff = now - max(0, int(window_seconds))
        counts: dict[str, int] = {k: 0 for k in _KINDS if k != "other"}
        with self._lock:
            events = [e for e in self._events if e["ts"] >= cutoff]
        for ev in events:
            if ev["kind"] in counts:
                counts[ev["kind"]] += 1
            # "other" is not surfaced in the counts block per spec.
        recent = list(events[-max(0, int(recent_limit)):])
        # Map to the dashboard schema — drop the internal "message" field
        # from the recent list (keep ts/kind/ref/operator).
        shaped = [
            {
                "ts": e["ts"],
                "kind": e["kind"],
                "ref": e["ref"],
                "operator": e["operator"],
            }
            for e in recent
        ]
        return {
            "window_seconds": int(window_seconds),
            "counts": counts,
            "recent_events": shaped,
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def iter_kinds(self) -> Iterable[str]:
        """Expose the tallied kind list for tests and callers."""
        return tuple(k for k in _KINDS if k != "other")


class _AuditRingHandler(logging.Handler):
    """logging.Handler that forwards records into an AuditSink."""

    def __init__(self, sink: AuditSink) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink.handle_log_record(record)
        except Exception:  # pragma: no cover — logging must never raise
            self.handleError(record)


# ----------------------------------------------------------------------
# Module-level default sink + attach helper
# ----------------------------------------------------------------------
_default_sink = AuditSink()
_default_handler: _AuditRingHandler | None = None
_attach_lock = threading.Lock()


def get_default_sink() -> AuditSink:
    """Return the process-wide default sink (used by the web server)."""
    return _default_sink


# ----------------------------------------------------------------------
# Durable JSONL file sink with size-based rotation
# ----------------------------------------------------------------------

# Non-blocking buffer cap — if the disk stalls, we keep at most this many
# entries queued in memory and drop-oldest when the buffer is full.
_FILE_BUFFER_MAXLEN = 500


class FileJSONLSink:
    """Thread-safe rotating JSONL sink for after-action audit review.

    Writes one JSON object per line to ``path``. When the active file
    reaches ``max_size_mb`` megabytes, it is rotated aside (``.1``,
    ``.2``, ... ``.<max_rotations>``) and a fresh file is opened. Files
    beyond ``max_rotations`` are pruned.

    This sink is deliberately non-blocking: disk hiccups never back up
    the audit logger. A bounded deque (``drop-oldest``) holds lines that
    could not be flushed; the next successful write drains the backlog.

    Only stdlib — no ``logging.handlers.RotatingFileHandler`` — so we
    control the rotation moment precisely (on counter overflow, not on
    every write) and avoid a per-write ``os.stat`` call.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] = "/data/audit/hydra.jsonl",
        max_size_mb: float = 10.0,
        max_rotations: int = 5,
        buffer_maxlen: int = _FILE_BUFFER_MAXLEN,
    ) -> None:
        self._path = Path(path)
        self._max_bytes = max(1, int(float(max_size_mb) * 1024 * 1024))
        self._max_rotations = max(1, int(max_rotations))
        self._buffer: deque[str] = deque(maxlen=max(1, int(buffer_maxlen)))
        self._lock = threading.Lock()
        self._file: Any = None
        self._bytes = 0
        self._closed = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Non-fatal — open() below will fail and buffering kicks in.
            pass
        self._open_file()

    # ------------------------------------------------------------------
    # File lifecycle
    # ------------------------------------------------------------------
    def _rotation_name(self, idx: int) -> Path:
        """Return ``path.N`` for rotation index N (``>=1``)."""
        return self._path.with_name(self._path.name + f".{idx}")

    def _current_size(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def _rotate_files(self) -> None:
        """Shift rotations: base→.1, .1→.2, …, drop beyond ``max_rotations``.

        Safe to call whether or not the base file exists. Ignores
        individual rename/unlink errors so one weird FS state never
        crashes the audit thread.
        """
        # Drop the file that would spill past max_rotations.
        oldest = self._rotation_name(self._max_rotations)
        if oldest.exists():
            try:
                oldest.unlink()
            except OSError:
                pass
        # Shift .N-1 → .N, working top-down to avoid overwrite.
        for i in range(self._max_rotations - 1, 0, -1):
            src = self._rotation_name(i)
            dst = self._rotation_name(i + 1)
            if src.exists():
                try:
                    src.rename(dst)
                except OSError:
                    pass
        # Base → .1
        if self._path.exists():
            try:
                self._path.rename(self._rotation_name(1))
            except OSError:
                pass

    def _open_file(self) -> None:
        """Check size, rotate if needed, then open for append.

        Called from ``__init__`` and after a rotation trigger. Any
        failure leaves ``self._file is None``; subsequent pushes buffer.
        """
        if self._current_size() >= self._max_bytes:
            self._rotate_files()
        try:
            self._file = open(self._path, "a", encoding="utf-8")
            self._bytes = self._current_size()
        except OSError:
            self._file = None
            self._bytes = 0

    def _rotate_and_reopen(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
        self._rotate_files()
        try:
            self._file = open(self._path, "a", encoding="utf-8")
            self._bytes = 0
        except OSError:
            self._file = None

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    @staticmethod
    def _format_line(
        kind: str,
        message: str,
        ref: Any,
        operator: str | None,
        ts: float | None,
    ) -> str:
        entry = {
            "ts": float(ts) if ts is not None else time.time(),
            "kind": str(kind or ""),
            "message": str(message or ""),
            "ref": ref,
            "operator": operator,
        }
        return json.dumps(entry, separators=(",", ":"), default=str) + "\n"

    def push(
        self,
        *,
        kind: str,
        message: str = "",
        ref: Any = None,
        operator: str | None = None,
        ts: float | None = None,
    ) -> None:
        """Append one JSONL entry. Non-blocking; safe from any thread."""
        line = self._format_line(kind, message, ref, operator, ts)
        with self._lock:
            if self._closed:
                return
            # Bounded deque drops oldest automatically at capacity.
            self._buffer.append(line)
            self._flush_locked()

    def handle_log_record(self, record: logging.LogRecord) -> None:
        """Ingest a ``hydra.audit`` logging record (file sink path)."""
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        self.push(
            kind=_classify(msg),
            message=msg,
            ts=getattr(record, "created", None),
        )

    def _flush_locked(self) -> None:
        """Drain the buffer into the active file. Caller holds lock."""
        if self._file is None:
            # Try to (re)open once — if still unavailable, keep buffering.
            try:
                self._file = open(self._path, "a", encoding="utf-8")
                self._bytes = self._current_size()
            except OSError:
                return
        while self._buffer:
            line = self._buffer[0]
            try:
                self._file.write(line)
                self._file.flush()
            except OSError:
                # Disk slow / ENOSPC / broken handle — leave queued.
                return
            except Exception:
                return
            self._buffer.popleft()
            self._bytes += len(line.encode("utf-8"))
            if self._bytes >= self._max_bytes:
                self._rotate_and_reopen()
                if self._file is None:
                    return

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._file is not None:
                try:
                    self._file.flush()
                    self._file.close()
                except Exception:
                    pass
                self._file = None

    def buffered(self) -> int:
        """Number of lines currently queued (disk-slow indicator)."""
        with self._lock:
            return len(self._buffer)

    @property
    def path(self) -> Path:
        return self._path


class _AuditFileHandler(logging.Handler):
    """logging.Handler that forwards records into a FileJSONLSink."""

    def __init__(self, sink: FileJSONLSink) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink.handle_log_record(record)
        except Exception:  # pragma: no cover — logging must never raise
            self.handleError(record)


def get_default_file_sink(
    config: configparser.ConfigParser | None,
) -> FileJSONLSink | None:
    """Build a ``FileJSONLSink`` from the ``[audit]`` config section.

    Returns ``None`` when the section exists and ``enabled=false``.
    A missing section yields a sink with the schema defaults, matching
    the ``enabled=true`` default — students ship with durable audit on.
    """
    if config is None or not config.has_section("audit"):
        return FileJSONLSink()
    s = config["audit"]
    if not s.getboolean("enabled", fallback=True):
        return None
    return FileJSONLSink(
        path=s.get("jsonl_path", fallback="/data/audit/hydra.jsonl"),
        max_size_mb=s.getint("max_size_mb", fallback=10),
        max_rotations=s.getint("max_rotations", fallback=5),
    )


def attach_file_sink(
    sink: FileJSONLSink,
    logger_name: str = "hydra.audit",
) -> FileJSONLSink:
    """Attach a ``_AuditFileHandler`` to the audit logger.

    Unlike the in-memory ``attach_to_logger``, this always adds a new
    handler — callers own the sink and its lifecycle.
    """
    logger = logging.getLogger(logger_name)
    handler = _AuditFileHandler(sink)
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)
    return sink


def attach_to_logger(
    logger_name: str = "hydra.audit",
    sink: AuditSink | None = None,
) -> AuditSink:
    """Attach a ring handler to the given logger (idempotent).

    Returns the sink that is receiving records. Safe to call multiple
    times — subsequent calls are no-ops on the default handler.
    """
    global _default_handler
    # Must compare against None explicitly — AuditSink defines __len__, so an
    # empty sink evaluates falsy under truthiness tests.
    target_sink = sink if sink is not None else _default_sink
    logger = logging.getLogger(logger_name)
    with _attach_lock:
        if sink is None:
            # Managing the default handler — install once, leave attached.
            if _default_handler is None:
                _default_handler = _AuditRingHandler(target_sink)
                logger.addHandler(_default_handler)
                # Ensure the logger actually propagates records to handlers.
                if logger.level == logging.NOTSET or logger.level > logging.INFO:
                    logger.setLevel(logging.INFO)
        else:
            # Custom sink — always attach a fresh handler.
            handler = _AuditRingHandler(target_sink)
            logger.addHandler(handler)
            if logger.level == logging.NOTSET or logger.level > logging.INFO:
                logger.setLevel(logging.INFO)
    return target_sink
