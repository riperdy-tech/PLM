"""What a metric record means, and which aggregations are valid for it (ADR-0031).

A number without its kind is ambiguous. "Views = 1,200 for the 7d window" observed on
day 7 and "views = 1,900 lifetime" observed on day 30 overlap: summing them counts the
first week twice. Every metric record therefore declares one `ObservationKind`, and the
aggregation helpers here accept only the kinds that are valid for them:

* `INCREMENTAL`: an amount for a period that does not overlap other periods of the same
  key (a monthly statement line, a cost entry). Summable.
* `CUMULATIVE_SNAPSHOT`: the running total from a fixed start (publish) up to `as_of`,
  for one window. Never summed; the latest snapshot of a window is the value.
* `ROLLING_SNAPSHOT`: a trailing-window value at `as_of` (e.g. last 28 days). Never
  summed; the latest snapshot is the value.
* `EXPERIMENT_OUTCOME`: one experiment's measured result at a declared horizon. Exactly
  one is effective per (experiment, content); see `effective_outcome`.
* `REVISION`: replaces an earlier record, which it names in `supersedes`.

Maturity is derived, never asserted: a window is mature once `published_at + window`
has passed at `as_of`. `lifetime` has no end, so it is provisional until the upstream
source closes it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, model_validator

from .enums import ObservationWindow
from .seams.c7 import ObservationMaturity

__all__ = [
    "MeasurementKindError",
    "MetricRecord",
    "ObservationKind",
    "effective_outcome",
    "latest_snapshot",
    "maturity_at",
    "sum_incremental",
    "window_duration",
    "window_end",
]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ObservationKind(StrEnum):
    INCREMENTAL = "incremental"
    CUMULATIVE_SNAPSHOT = "cumulative_snapshot"
    ROLLING_SNAPSHOT = "rolling_snapshot"
    EXPERIMENT_OUTCOME = "experiment_outcome"
    REVISION = "revision"


SNAPSHOT_KINDS = frozenset({ObservationKind.CUMULATIVE_SNAPSHOT, ObservationKind.ROLLING_SNAPSHOT})


class MeasurementKindError(ValueError):
    """An aggregation was asked to combine records it is not valid for."""


class MetricRecord(BaseModel):
    """One measured value with the facts needed to aggregate it correctly."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: NonEmpty
    kind: ObservationKind
    entity_id: NonEmpty
    metric: NonEmpty
    value: Decimal
    as_of: AwareDatetime
    window: ObservationWindow | None = None
    """Required for snapshots and outcomes; ``None`` for incremental period amounts."""
    period: str | None = None
    """Required for incremental amounts: the non-overlapping period they cover."""
    supersedes: str | None = None
    """Required for revisions: the ``record_id`` this record replaces."""

    @model_validator(mode="after")
    def _kind_facts(self) -> MetricRecord:
        if self.kind is ObservationKind.INCREMENTAL and not self.period:
            raise ValueError("an incremental record needs the period it covers")
        if self.kind in SNAPSHOT_KINDS | {ObservationKind.EXPERIMENT_OUTCOME} and (
            self.window is None
        ):
            raise ValueError(f"a {self.kind.value} record needs its window")
        if self.kind is ObservationKind.REVISION and not self.supersedes:
            raise ValueError("a revision must name the record it supersedes")
        return self


def window_duration(window: ObservationWindow) -> timedelta | None:
    """The span a window covers; ``None`` for ``lifetime`` (unbounded, not missing)."""
    hours = window.duration_hours
    return None if hours is None else timedelta(hours=hours)


def window_end(published_at: datetime, window: ObservationWindow) -> datetime | None:
    duration = window_duration(window)
    return None if duration is None else published_at + duration


def maturity_at(
    *,
    published_at: datetime,
    window: ObservationWindow,
    as_of: datetime,
    closed_by_source: bool = False,
) -> ObservationMaturity:
    """Mature once the window has fully elapsed; ``lifetime`` matures only when closed."""
    if as_of < published_at:
        raise ValueError("an observation cannot predate the publication it measures")
    if closed_by_source:
        return ObservationMaturity.CLOSED
    end = window_end(published_at, window)
    if end is None or as_of < end:
        return ObservationMaturity.PROVISIONAL
    return ObservationMaturity.MATURE


def _resolve_revisions(records: Sequence[MetricRecord]) -> list[MetricRecord]:
    """Drop every record that a revision supersedes; revisions take their place."""
    superseded = {r.supersedes for r in records if r.kind is ObservationKind.REVISION}
    return [r for r in records if r.record_id not in superseded]


def sum_incremental(records: Iterable[MetricRecord]) -> Decimal:
    """Sum of non-overlapping period amounts, after applying revisions.

    Refuses snapshots and outcomes outright, and refuses two live records for the same
    (entity, metric, period): that is an overlap, and summing it would double count.
    """
    batch = list(records)
    for record in batch:
        if record.kind not in (ObservationKind.INCREMENTAL, ObservationKind.REVISION):
            raise MeasurementKindError(
                f"{record.record_id} is a {record.kind.value}; only incremental amounts sum"
            )
    live = _resolve_revisions(batch)
    seen: dict[tuple[str, str, str | None], str] = {}
    total = Decimal(0)
    for record in live:
        key = (record.entity_id, record.metric, record.period)
        if key in seen:
            raise MeasurementKindError(
                f"{record.record_id} and {seen[key]} both cover {key}; refusing to double count"
            )
        seen[key] = record.record_id
        total += record.value
    return total


def latest_snapshot(
    records: Iterable[MetricRecord], *, window: ObservationWindow
) -> MetricRecord | None:
    """The newest snapshot of one window, or ``None`` when that window was never observed."""
    batch = list(records)
    for record in batch:
        if record.kind not in SNAPSHOT_KINDS | {ObservationKind.REVISION}:
            raise MeasurementKindError(
                f"{record.record_id} is a {record.kind.value}, not a snapshot"
            )
    candidates = [r for r in _resolve_revisions(batch) if r.window is window]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.as_of, r.record_id))


def effective_outcome(
    outcomes: Iterable[MetricRecord], *, horizon: ObservationWindow
) -> MetricRecord | None:
    """The one outcome of a (experiment, content) that may change a decision.

    Only an outcome at the declared ``horizon`` counts; outcomes at other windows are
    history. A revision at the horizon replaces the record it supersedes.
    """
    batch = [r for r in outcomes if r.window is horizon]
    for record in batch:
        if record.kind not in (ObservationKind.EXPERIMENT_OUTCOME, ObservationKind.REVISION):
            raise MeasurementKindError(
                f"{record.record_id} is a {record.kind.value}, not an experiment outcome"
            )
    live = _resolve_revisions(batch)
    if not live:
        return None
    if len(live) > 1:
        ids = ", ".join(sorted(r.record_id for r in live))
        raise MeasurementKindError(
            f"several live outcomes at {horizon.value} ({ids}); a later one must be a revision"
        )
    return live[0]
