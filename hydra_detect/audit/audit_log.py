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

import logging
import threading
import time
from collections import deque
from typing import Iterable

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
