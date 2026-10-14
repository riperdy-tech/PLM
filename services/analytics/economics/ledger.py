"""Entity economics from stored facts, aggregated by observation kind (ADR-0031).

* YouTube snapshots (views, estimated revenue) are cumulative per window: the value is
  the latest snapshot of the requested window (`latest_snapshot`), never a sum across
  windows or dates. Summing the 24h, 7d and lifetime snapshots of one video would count
  the first day three times.
* Statement lines and costs are incremental per period and are summed with
  `sum_incremental`, which refuses overlapping periods.
* Every amount is converted into the reporting currency through the `FxTable` at its own
  instant (P9 §110 transaction-date method). A missing rate raises
  `FxRateMissingError`; it is never 1.
* A stored amount with no declared currency (pre-ADR-0031 rows) is excluded and listed
  in ``unreconciled_record_ids``. When it is a realized-revenue or cost record the net
  contribution is withheld, because the reconciled remainder would misstate it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal

from plm_contracts.measurement import (
    MetricRecord,
    ObservationKind,
    latest_snapshot,
    sum_incremental,
)
from plm_contracts.money import FxTable, Money

from ..domain.enums import ObservationWindow, RevenueStatus
from ..domain.models import (
    CostItem,
    FxProvenance,
    TrackMetricObservation,
    UnitEconomics,
    VideoMetricObservation,
)
from .calculator import compute_unit_economics

__all__ = ["EntityType", "entity_economics"]

EntityType = Literal["video", "track"]

_REALIZED = frozenset({RevenueStatus.REPORTED, RevenueStatus.FINAL})


class _Converter:
    def __init__(self, fx: FxTable, currency: str) -> None:
        self._fx = fx
        self._currency = currency
        self.conversions: list[FxProvenance] = []

    def __call__(self, record_id: str, money: Money, at: datetime) -> Money:
        converted = self._fx.convert(money, self._currency, as_of=at)
        self.conversions.append(
            FxProvenance(
                record_id=record_id,
                original=money,
                converted=converted.converted,
                rate_id=converted.rate.rate_id if converted.rate is not None else None,
                inverted=converted.inverted,
            )
        )
        return converted.converted


def _latest_video_snapshot(
    metrics: Sequence[VideoMetricObservation],
    window: ObservationWindow,
    measured: str,
) -> VideoMetricObservation | None:
    """The newest snapshot of ``window`` in which ``measured`` was observed at all."""
    by_id = {m.id: m for m in metrics}
    records = []
    for m in metrics:
        if measured == "estimated_revenue":
            present = m.estimated_revenue is not None or m.estimated_revenue_unreconciled is not None
        else:
            present = getattr(m, measured) is not None
        if present:
            records.append(
                MetricRecord(
                    record_id=m.id,
                    kind=m.observation_kind,
                    entity_id=m.video_id,
                    metric=measured,
                    value=Decimal(0),  # selection only; the value is read from the row
                    as_of=m.observed_at,
                    window=m.window,
                )
            )
    chosen = latest_snapshot(records, window=window)
    return None if chosen is None else by_id[chosen.record_id]


def entity_economics(
    *,
    entity_id: str,
    entity_type: EntityType,
    video_metrics: Sequence[VideoMetricObservation],
    track_metrics: Sequence[TrackMetricObservation],
    costs: Sequence[CostItem],
    window: ObservationWindow,
    reporting_currency: str,
    fx: FxTable,
    as_of: datetime,
) -> UnitEconomics:
    """Unit economics of one entity from its stored facts (see module docstring)."""
    convert = _Converter(fx, reporting_currency)
    unreconciled: list[str] = []
    blocking_unreconciled = False

    # Platform estimate: latest snapshot of one window (labelled estimated, never booked).
    estimated: Money | None = None
    snapshot = _latest_video_snapshot(video_metrics, window, "estimated_revenue")
    if snapshot is not None:
        if snapshot.estimated_revenue is None:
            unreconciled.append(snapshot.id)
        else:
            estimated = convert(snapshot.id, snapshot.estimated_revenue, snapshot.observed_at)
    views_snapshot = _latest_video_snapshot(video_metrics, window, "views")
    views = views_snapshot.views if views_snapshot is not None else None

    # Realized revenue: reported/final statement lines, incremental per period.
    realized_records: list[MetricRecord] = []
    stream_records: list[MetricRecord] = []
    streams_known = True
    realized_lines = [t for t in track_metrics if t.revenue_status in _REALIZED]
    for line in realized_lines:
        if line.streams is None:
            streams_known = False
        else:
            stream_records.append(
                MetricRecord(
                    record_id=line.id, kind=line.observation_kind, entity_id=line.isrc,
                    metric="streams", value=Decimal(line.streams), as_of=line.observed_at,
                    window=line.window, period=line.date_str,
                )
            )
        if line.reported_revenue is None:
            unreconciled.append(line.id)
            blocking_unreconciled = True
            continue
        amount = convert(line.id, line.reported_revenue, line.observed_at)
        realized_records.append(
            MetricRecord(
                record_id=line.id, kind=line.observation_kind, entity_id=line.isrc,
                metric="realized_revenue", value=amount.amount, as_of=line.observed_at,
                window=line.window, period=line.date_str,
            )
        )
    realized: Money | None = None
    if realized_records:
        realized = Money.of(sum_incremental(realized_records), reporting_currency)
    streams: int | None = None
    if realized_lines and streams_known:
        streams = int(sum_incremental(stream_records))

    # Costs: incremental per (entity, category, period).
    cost_records: list[MetricRecord] = []
    for item in costs:
        if item.amount is None:
            unreconciled.append(item.id)
            blocking_unreconciled = True
            continue
        amount = convert(item.id, item.amount, item.incurred_at)
        cost_records.append(
            MetricRecord(
                record_id=item.id, kind=ObservationKind.INCREMENTAL, entity_id=item.entity_id,
                metric=f"cost.{item.category.value}", value=amount.amount,
                as_of=item.incurred_at, period=item.period,
            )
        )
    allocated: Money | None = None
    if cost_records:
        allocated = Money.of(sum_incremental(cost_records), reporting_currency)

    return compute_unit_economics(
        entity_id,
        entity_type,
        reporting_currency=reporting_currency,
        window=window,
        estimated_revenue=estimated,
        realized_revenue=realized,
        allocated_cost=allocated,
        views=views,
        streams=streams,
        blocked_reason="unreconciled_records" if blocking_unreconciled else None,
        conversions=convert.conversions,
        unreconciled_record_ids=unreconciled,
        as_of=as_of,
    )
