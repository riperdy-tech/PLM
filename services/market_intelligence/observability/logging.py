# services/market_intelligence/observability/logging.py
"""One structured line per run, through the platform logger (no second logging stack).

The six §42 counters are emitted under their own names, and the two figures whose
unit is not obvious carry it explicitly: `api_quota_units` names its unit and
`llm_cost_estimate` names its currency.
"""

from __future__ import annotations

import logging

from .run_summary import RunSummary

__all__ = ["log_run_summary"]

_logger = logging.getLogger("plm.market.run")


def log_run_summary(summary: RunSummary) -> None:
    """`extra` carries the counters so the JSON formatter emits them as fields."""
    _logger.info(
        "market run summary",
        extra={
            "run_id": summary.run_id, "status": summary.status, "source": summary.source,
            "queries": summary.queries, "raw_objects": summary.raw_objects,
            "normalized_objects": summary.normalized_objects,
            "quarantined": summary.quarantined, "failed": summary.failed,
            "api_calls": summary.api_calls, "api_quota_units": summary.quota_units,
            "api_quota_units_unit": summary.quota_units_unit,
            "llm_calls": summary.llm_calls, "llm_tokens": summary.llm_tokens,
            "llm_cost_estimate": summary.llm_cost_estimate,
            "llm_cost_estimate_currency": summary.cost_currency,
            "storage_bytes": summary.storage_bytes,
            "score_changes": summary.score_changes,
            "correlation_id": summary.correlation_id,
        },
    )
