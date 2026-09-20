# services/market_intelligence/observability/run_summary.py
"""§AC-14: one object that answers what ran, on which source, over which queries,
with how many objects, what failed, how much quota and LLM spend, and what changed.

§42's counters keep their units: `quota_units` are the provider's quota units and
`llm_cost_estimate` is a USD estimate — a figure with no unit cannot be priced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..storage import repositories
from ..storage.models import change_events, sources

__all__ = ["COST_CURRENCY", "QUOTA_UNITS_UNIT", "RunSummary", "summarize_run"]

QUOTA_UNITS_UNIT: Final[str] = "youtube_api_quota_units"
"""The billing unit of `quota_units`: a `search.list` costs 100 of these (§4.1)."""

COST_CURRENCY: Final[str] = "USD"
"""Currency of every money figure in a run summary (the provider bills in USD)."""


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    status: str
    source: str | None
    queries: int
    raw_objects: int
    normalized_objects: int
    quarantined: int
    failed: int
    api_calls: int
    quota_units: int
    llm_calls: int
    llm_tokens: int
    llm_cost_estimate: float
    storage_bytes: int
    score_changes: int = 0
    correlation_id: str | None = None

    @property
    def quota_units_unit(self) -> str:
        """The unit `quota_units` is counted in — stated, never implied."""
        return QUOTA_UNITS_UNIT

    @property
    def cost_currency(self) -> str:
        """The currency `llm_cost_estimate` is denominated in — stated, never implied."""
        return COST_CURRENCY


def summarize_run(session: Session, run_id: str) -> RunSummary:
    """Every counter off the run's stored row; missing counters stay a real `0`.

    Every field here is a value the run recorded about itself (an aggregate of
    rows that were written), so a `0` means "the query returned zero", never
    "nobody knows". `score_changes` counts the change events §32 detected inside
    the run's window `[started_at, finished_at)` — for a still-running run, the
    window is open-ended and counts events detected since it started.
    """
    run = repositories.load_run(session, run_id)
    if run is None:
        raise KeyError(f"unknown run {run_id!r}")
    source = session.execute(
        sa.select(sources.c.provider_name).where(sources.c.id == run["source_id"])
    ).scalar_one_or_none()
    changes = (
        sa.select(sa.func.count()).select_from(change_events)
        .where(change_events.c.detected_at >= run["started_at"])
    )
    if run["finished_at"] is not None:
        changes = changes.where(change_events.c.detected_at < run["finished_at"])
    score_changes = int(session.execute(changes).scalar_one())

    from plm_platform.correlation import current_correlation_id

    return RunSummary(
        run_id=str(run["id"]), status=str(run["status"]), source=source,
        queries=1 if run["query_id"] else 0, raw_objects=int(run["raw_observation_count"]),
        normalized_objects=int(run["normalized_observation_count"]),
        quarantined=repositories.count_quarantined(session, run_id=str(run["id"])),
        failed=int(run["error_count"]), api_calls=int(run["cost_api_calls"]),
        quota_units=int(run["cost_api_quota_units"]), llm_calls=int(run["cost_llm_calls"]),
        llm_tokens=int(run["cost_llm_tokens"]),
        llm_cost_estimate=float(run["cost_llm_cost_estimate"]),
        storage_bytes=int(run["cost_storage_bytes"]), score_changes=score_changes,
        correlation_id=current_correlation_id(),
    )
