# services/market_intelligence/observability/metrics.py
"""P1's Prometheus instruments, bound to the platform registry (no second registry)."""

from __future__ import annotations

from plm_platform.observability import METRICS_REGISTRY
from prometheus_client import Counter

from .run_summary import RunSummary

__all__ = [
    "METRICS_REGISTRY", "QUARANTINED_TOTAL", "QUOTA_UNITS_TOTAL", "RUNS_TOTAL",
    "SCORES_WRITTEN_TOTAL", "record_run_metrics",
]

RUNS_TOTAL = Counter("plm_market_runs_total", "Market collection runs by status.",
                     ["status"], registry=METRICS_REGISTRY)
QUARANTINED_TOTAL = Counter("plm_market_quarantined_total", "Quarantined market records.",
                            ["provider"], registry=METRICS_REGISTRY)
QUOTA_UNITS_TOTAL = Counter("plm_market_quota_units_total", "Provider quota units consumed.",
                            ["provider"], registry=METRICS_REGISTRY)
SCORES_WRITTEN_TOTAL = Counter("plm_market_scores_written_total",
                               "Opportunity score rows written.", registry=METRICS_REGISTRY)


def record_run_metrics(summary: RunSummary) -> None:
    """One finished step's summary → the registry. Counters only ever move up."""
    provider = summary.source or "unknown"
    RUNS_TOTAL.labels(status=summary.status).inc()
    QUOTA_UNITS_TOTAL.labels(provider=provider).inc(summary.quota_units)
    QUARANTINED_TOTAL.labels(provider=provider).inc(summary.quarantined)
