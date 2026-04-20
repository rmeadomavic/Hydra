"""Observability primitives — Prometheus metrics + client-error sink + health.

Importable from the rest of the codebase as ``hydra_detect.observability``.
All members are stdlib-only; no external dependency is introduced.
"""

from __future__ import annotations

from .health import health_snapshot, SUBSYSTEMS
from .metrics import (
    ClientErrorSink,
    Counter,
    Gauge,
    attach_audit_counters,
    get_client_error_sink,
    hydra_cpu_temp_c,
    hydra_drop_events_total,
    hydra_fps,
    hydra_gpu_temp_c,
    hydra_hmac_invalid_total,
    hydra_inference_ms,
    hydra_ram_pct,
    hydra_strike_events_total,
    hydra_tak_accepted_total,
    hydra_tak_rejected_total,
    render_metrics,
    reset_counters_for_test,
)

__all__ = [
    "ClientErrorSink",
    "Counter",
    "Gauge",
    "SUBSYSTEMS",
    "attach_audit_counters",
    "get_client_error_sink",
    "health_snapshot",
    "hydra_cpu_temp_c",
    "hydra_drop_events_total",
    "hydra_fps",
    "hydra_gpu_temp_c",
    "hydra_hmac_invalid_total",
    "hydra_inference_ms",
    "hydra_ram_pct",
    "hydra_strike_events_total",
    "hydra_tak_accepted_total",
    "hydra_tak_rejected_total",
    "render_metrics",
    "reset_counters_for_test",
]
