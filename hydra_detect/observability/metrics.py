"""Prometheus exposition + bounded client-error ring.

Hand-rolled so Hydra stays stdlib-only. Emits the 0.0.4 Prometheus text
format (the current de-facto exposition format used by ``prometheus_client``)
without the dependency.

Counters are process-lifetime and bumped by a ``logging.Handler`` attached
to ``hydra.audit`` — so every strike/drop/TAK/HMAC event that already flows
through the audit logger is tallied automatically.

Gauges are sampled on scrape. The server module passes in a ``StreamState``
snapshot plus optional tegra-probe callbacks; when a probe raises, the gauge
is simply omitted rather than crashing the scrape.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

# Client error ring size — small; client-side errors are normally rare and we
# only need enough buffer to survive a burst from a single broken page.
_CLIENT_ERROR_MAXLEN = 200

# Cap on a single stored client-error payload (defensive against huge stacks).
_MAX_STACK_LEN = 4096
_MAX_STR_LEN = 512


def _clip(value: Any, limit: int) -> str:
    """Coerce to string, strip control chars, clip to ``limit`` chars."""
    if value is None:
        return ""
    s = str(value)
    # Drop newlines/control chars that would confuse downstream log parsers.
    # Stacks keep newlines — callers pass _MAX_STACK_LEN for those.
    return s[:limit]


class ClientErrorSink:
    """Bounded, thread-safe ring of frontend error reports.

    Records from ``window.onerror`` / ``onunhandledrejection`` land here via
    ``POST /api/client_error``. Designed to be cheap on the hot path — one
    lock acquisition + a deque append.
    """

    def __init__(self, maxlen: int = _CLIENT_ERROR_MAXLEN) -> None:
        self._events: Deque[Dict[str, Any]] = deque(maxlen=int(maxlen))
        self._lock = threading.Lock()

    def push(
        self,
        *,
        message: str = "",
        source: str = "",
        lineno: Any = None,
        colno: Any = None,
        stack: str = "",
        url: str = "",
        client_ts: Any = None,
        remote_addr: str = "",
        user_agent: str = "",
    ) -> Dict[str, Any]:
        """Append one client error. Returns the stored record."""
        entry = {
            "ts": time.time(),
            "client_ts": float(client_ts) if isinstance(client_ts, (int, float)) else None,
            "message": _clip(message, _MAX_STR_LEN),
            "source": _clip(source, _MAX_STR_LEN),
            "lineno": int(lineno) if isinstance(lineno, (int, float)) else None,
            "colno": int(colno) if isinstance(colno, (int, float)) else None,
            "stack": _clip(stack, _MAX_STACK_LEN),
            "url": _clip(url, _MAX_STR_LEN),
            "remote_addr": _clip(remote_addr, 64),
            "user_agent": _clip(user_agent, _MAX_STR_LEN),
        }
        with self._lock:
            self._events.append(entry)
        return entry

    def snapshot(self, limit: int = 50) -> Dict[str, Any]:
        """Return the newest ``limit`` entries plus the ring total."""
        with self._lock:
            events = list(self._events)
            total = len(self._events)
        limit = max(0, int(limit))
        return {"total": total, "recent": events[-limit:] if limit else []}

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


# ----------------------------------------------------------------------
# Module-level default sink
# ----------------------------------------------------------------------
_default_client_error_sink = ClientErrorSink()


def get_client_error_sink() -> ClientErrorSink:
    """Return the process-wide default client-error sink."""
    return _default_client_error_sink


# ======================================================================
# Prometheus collectors (hand-rolled, no prometheus_client dep)
# ======================================================================


class Counter:
    """Monotonic process-lifetime counter — thread-safe."""

    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help_text = help_text
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, n: int = 1) -> None:
        if n < 0:
            return
        with self._lock:
            self._value += int(n)

    def value(self) -> int:
        with self._lock:
            return self._value

    def reset(self) -> None:
        """Test-only — zero the counter."""
        with self._lock:
            self._value = 0


class Gauge:
    """Sampled-on-scrape gauge. ``provider`` is called from ``render()``."""

    def __init__(
        self,
        name: str,
        help_text: str,
        provider: Optional[Callable[[], Optional[float]]] = None,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self._provider = provider
        self._value: Optional[float] = None
        self._lock = threading.Lock()

    def set(self, v: Optional[float]) -> None:
        with self._lock:
            self._value = float(v) if v is not None else None

    def set_provider(self, provider: Callable[[], Optional[float]]) -> None:
        self._provider = provider

    def sample(self) -> Optional[float]:
        """Return the current value — provider wins if set, else last ``set``."""
        prov = self._provider
        if prov is not None:
            try:
                v = prov()
            except Exception:
                return None
            return float(v) if v is not None else None
        with self._lock:
            return self._value


# Counters — bumped by the audit-logger handler below.
hydra_tak_accepted_total = Counter(
    "hydra_tak_accepted_total",
    "Total accepted TAK GeoChat commands.",
)
hydra_tak_rejected_total = Counter(
    "hydra_tak_rejected_total",
    "Total rejected TAK GeoChat commands.",
)
hydra_strike_events_total = Counter(
    "hydra_strike_events_total",
    "Total strike engagement events.",
)
hydra_drop_events_total = Counter(
    "hydra_drop_events_total",
    "Total drop engagement events.",
)
hydra_hmac_invalid_total = Counter(
    "hydra_hmac_invalid_total",
    "Total HMAC verification failures.",
)

# Gauges — sampled on scrape via registered providers.
hydra_fps = Gauge("hydra_fps", "Current detection pipeline frames-per-second.")
hydra_inference_ms = Gauge(
    "hydra_inference_ms", "Current per-frame inference latency in ms.",
)
hydra_cpu_temp_c = Gauge("hydra_cpu_temp_c", "CPU temperature in degrees Celsius.")
hydra_gpu_temp_c = Gauge("hydra_gpu_temp_c", "GPU temperature in degrees Celsius.")
hydra_ram_pct = Gauge("hydra_ram_pct", "RAM utilization percentage (0-100).")


_COUNTERS = (
    hydra_tak_accepted_total,
    hydra_tak_rejected_total,
    hydra_strike_events_total,
    hydra_drop_events_total,
    hydra_hmac_invalid_total,
)

_GAUGES = (
    hydra_fps,
    hydra_inference_ms,
    hydra_cpu_temp_c,
    hydra_gpu_temp_c,
    hydra_ram_pct,
)


# Map audit-sink kinds → counter. Keys match ``audit.audit_log._KINDS``.
_AUDIT_KIND_TO_COUNTER = {
    "tak_accepted": hydra_tak_accepted_total,
    "tak_rejected": hydra_tak_rejected_total,
    "strike_events": hydra_strike_events_total,
    "drop_events": hydra_drop_events_total,
    "hmac_invalid_events": hydra_hmac_invalid_total,
}


def _format_value(v: Optional[float]) -> str:
    """Render a gauge value per Prometheus text format rules."""
    if v is None:
        return "NaN"
    if v != v:  # NaN
        return "NaN"
    if v == float("inf"):
        return "+Inf"
    if v == float("-inf"):
        return "-Inf"
    # Prefer a compact integer representation when exact.
    if float(v).is_integer():
        return str(int(v))
    return repr(float(v))


def render_metrics() -> str:
    """Return the full Prometheus exposition text (text/plain 0.0.4).

    Format, per https://prometheus.io/docs/instrumenting/exposition_formats/:

        # HELP <name> <help text>
        # TYPE <name> counter|gauge
        <name> <value>
    """
    lines: list[str] = []
    for c in _COUNTERS:
        lines.append(f"# HELP {c.name} {c.help_text}")
        lines.append(f"# TYPE {c.name} counter")
        lines.append(f"{c.name} {c.value()}")
    for g in _GAUGES:
        lines.append(f"# HELP {g.name} {g.help_text}")
        lines.append(f"# TYPE {g.name} gauge")
        sampled = g.sample()
        lines.append(f"{g.name} {_format_value(sampled)}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Audit-logger → counter bridge
# ----------------------------------------------------------------------


class _MetricsAuditHandler(logging.Handler):
    """Bumps the Prometheus counters whenever an ``hydra.audit`` record fires."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from hydra_detect.audit.audit_log import _classify
            msg = record.getMessage()
            kind = _classify(msg)
            counter = _AUDIT_KIND_TO_COUNTER.get(kind)
            if counter is not None:
                counter.inc(1)
        except Exception:  # pragma: no cover — logging must not raise
            self.handleError(record)


_metrics_handler: _MetricsAuditHandler | None = None
_metrics_attach_lock = threading.Lock()


def attach_audit_counters(logger_name: str = "hydra.audit") -> None:
    """Attach the counter-bumping handler to the audit logger (idempotent)."""
    global _metrics_handler
    with _metrics_attach_lock:
        if _metrics_handler is not None:
            return
        handler = _MetricsAuditHandler()
        logging.getLogger(logger_name).addHandler(handler)
        _metrics_handler = handler


def reset_counters_for_test() -> None:
    """Test-only — zero every counter. Production callers must not use."""
    for c in _COUNTERS:
        c.reset()
