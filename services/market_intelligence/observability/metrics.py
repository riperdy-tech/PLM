# services/market_intelligence/observability/metrics.py
"""P1's Prometheus instruments, bound to the platform registry (no second registry).

The counters are built through `get_or_create_counter`: this module is importable
under two identities (`services.market_intelligence.observability.metrics` and the
editable top-level `market_intelligence.observability.metrics`), and a module-level
`Counter(...)` would register twice into the one shared registry when an in-process
invocation (the CLI under test) meets the other import path.
"""

from __future__ import annotations

from plm_platform.observability import METRICS_REGISTRY, get_or_create_counter

from .run_summary import RunSummary

__all__ = [
    "METRICS_REGISTRY", "QUARANTINED_TOTAL", "QUOTA_UNITS_TOTAL", "RUNS_TOTAL",
    "SCORES_WRITTEN_TOTAL", "record_run_metrics",
]

RUNS_TOTAL = get_or_create_counter(
    "plm_market_runs_total", "Market collection runs by status.", ["status"])
QUARANTINED_TOTAL = get_or_create_counter(
    "plm_market_quarantined_total", "Quarantined market records.", ["provider"])
QUOTA_UNITS_TOTAL = get_or_create_counter(
    "plm_market_quota_units_total", "Provider quota units consumed.", ["provider"])
SCORES_WRITTEN_TOTAL = get_or_create_counter(
    "plm_market_scores_written_total", "Opportunity score rows written.")


def record_run_metrics(summary: RunSummary) -> None:
    """One finished step's summary → the registry. Counters only ever move up."""
    provider = summary.source or "unknown"
    RUNS_TOTAL.labels(status=summary.status).inc()
    QUOTA_UNITS_TOTAL.labels(provider=provider).inc(summary.quota_units)
    QUARANTINED_TOTAL.labels(provider=provider).inc(summary.quarantined)
