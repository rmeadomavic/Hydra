# Observability

Hydra exposes two health-style surfaces: `/api/health` (JSON, operator-facing)
and `/api/metrics` (Prometheus text format, scrape-facing). This doc covers
conventions that are easy to "fix" the wrong way.

## Prometheus NaN-vs-zero convention

In `hydra_detect/observability/metrics.py`, `_format_value()` renders `None`
and floating-point `NaN` as the literal string `NaN` rather than coercing to
`0.0`.

```python
def _format_value(v: Optional[float]) -> str:
    if v is None:
        return "NaN"
    if v != v:  # NaN
        return "NaN"
    if v == float("inf"):
        return "+Inf"
    if v == float("-inf"):
        return "-Inf"
    ...
```

**This is intentional.** Prometheus treats `NaN` as "value unknown" and
excludes the sample from `rate()`, `avg_over_time()`, and `increase()`
calculations. Coercing to `0.0` would make a missing-data condition look
identical to a "really zero" value:

- Camera offline (no frame stats yet) — should be `NaN`, not `fps=0.0` which
  a dashboard alert rule may interpret as "camera saturated at zero fps."
- MAVLink not yet connected — `last_heartbeat_age_sec` is `NaN` (no
  baseline), not `0.0` (which means "heartbeat just arrived").
- GPS not yet locked — `gps_lat` is `NaN`, not `0.0` (which is a real
  coordinate in the Gulf of Guinea).

When adding a new gauge, **default to `None` for "not yet observed."** Only
coerce to a numeric value when you have a real observation. The Prometheus
convention is the one that lets dashboard authors distinguish "data missing"
from "data zero" without an out-of-band convention.

See PR #236 R2-1 (issue #241 tracker) for the adversarial finding that
prompted this doc.

## Additive surface convention

Both `/api/health` and `/api/metrics` are append-only. Removing or renaming a
top-level key in `health_snapshot()` or a gauge name in the Prometheus
exposition is a breaking change for downstream scrapers (phone-home, external
Grafana, operator-side `curl` scripts).

`tests/test_observability.py::TestHealthBodyContract` pins the top-level key
set so additions are deliberate — a PR that adds, removes, or renames a key
must update `EXPECTED_KEYS` in the same diff. The Prometheus exposition does
not yet have a parallel contract test; add one when the gauge surface
stabilizes.

## Related

- `hydra_detect/observability/metrics.py` — Prometheus collectors
- `hydra_detect/observability/health.py` — `/api/health` body
- `tests/test_observability.py` — contract tests
- PR #236 (disk telemetry, closes #232)
- Issue #241 (Wave 3 adversarial follow-ups tracker)
