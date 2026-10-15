"""Request and response schemas for `/analytics` API (P9 §88, Ruling R10, R11, ADR-0031).

Amounts on the wire are decimal strings (or integers) with an explicit ISO 4217
currency. A JSON float is refused: it cannot carry an exact decimal amount. A metric
that was not measured is omitted or ``null``, never ``0``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from plm_contracts import CurrencyCode, ObservationWindow, QualityStatus
from plm_contracts.money import FxRate, Money
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c7 import ObservationMaturity
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ..domain.amounts import parse_amount
from ..domain.enums import CostCategory, RevenueStatus


def _wire_amount(value: object) -> Decimal:
    amount = parse_amount(value)  # AmountParseError is a ValueError: a 422, not a 500
    if amount is None:
        raise ValueError("an amount is required")
    return amount


WireAmount = Annotated[Decimal, BeforeValidator(_wire_amount)]
"""A decimal string or integer; floats are refused (ADR-0031)."""

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class MoneyIn(BaseModel):
    """An amount with its currency, e.g. ``{"amount": "12.50", "currency": "USD"}``."""

    model_config = ConfigDict(extra="forbid")

    amount: WireAmount
    currency: CurrencyCode

    def to_money(self) -> Money:
        return Money.of(self.amount, self.currency)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "healthy"
    service: str = "analytics"
    timestamp: str


class YouTubeIngestRequest(BaseModel):
    """One cumulative snapshot of a video for one window (ADR-0031).

    Metrics that were not measured are omitted or ``null``. ``estimated_revenue`` is a
    platform estimate and needs ``revenue_currency``, the currency YouTube reported.
    """

    model_config = ConfigDict(extra="forbid")

    video_id: NonEmpty
    channel_id: NonEmpty
    date_str: NonEmpty
    window: ObservationWindow
    observed_at: AwareDatetime
    views: int | None = Field(default=None, ge=0)
    watch_time_minutes: float | None = Field(default=None, ge=0)
    average_view_duration_seconds: float | None = Field(default=None, ge=0)
    average_percentage_viewed: float | None = Field(default=None, ge=0)
    subscribers_gained: int | None = Field(default=None, ge=0)
    subscribers_lost: int | None = Field(default=None, ge=0)
    estimated_revenue: WireAmount | None = None
    revenue_currency: CurrencyCode | None = None
    source: Literal["youtube_analytics", "synthetic"] = "youtube_analytics"
    """``synthetic`` labels numbers from a mock source (R5), so they are never read as a
    real audience."""

    @model_validator(mode="after")
    def _revenue_has_currency(self) -> Self:
        if self.estimated_revenue is not None and self.revenue_currency is None:
            raise ValueError("estimated_revenue needs revenue_currency; no currency is assumed")
        return self

    def estimated_revenue_money(self) -> Money | None:
        if self.estimated_revenue is None or self.revenue_currency is None:
            return None
        return Money.of(self.estimated_revenue, self.revenue_currency)


class DistributorIngestRequest(BaseModel):
    """A distributor statement in its declared ``currency`` (required)."""

    model_config = ConfigDict(extra="forbid")

    provider: NonEmpty
    period: NonEmpty
    currency: CurrencyCode
    raw_content: str
    source_reference: NonEmpty
    rows: list[dict[str, Any]] | None = None
    revenue_status: RevenueStatus = RevenueStatus.REPORTED
    received_at: AwareDatetime | None = None
    """When the statement was obtained, if the caller knows it (the pipeline passes its
    ``workflow.now()``); otherwise P9's clock. Its lines are observed at this instant."""


class ReleaseRecordIngestRequest(BaseModel):
    """P8's Seam C7 leg-1 distribution record (ADR-0030), parsed with the shared model."""

    model_config = ConfigDict(extra="forbid")

    record: dict[str, Any]


class MeasureRequest(BaseModel):
    """Measure one published unit from the facts P9 stored, and enqueue its reward (R5).

    The caller names *what* to measure, never a number: the unit and its lineage and
    publication time come from P7's publication event (C6 hop 2), the recordings from
    their ISRCs (each must be linked to the same lineage by an ingested C7 leg-1
    record). Views and watch time are the latest snapshot of ``window`` observed at or
    before ``observed_at``; realized revenue the statement lines of the ISRCs; costs the
    unit's recorded costs; all converted into the reporting currency through recorded
    FX rates. Maturity is derived. The reward is the ``engagement-contribution-proxy-v1``
    proxy, not expected economic value.
    """

    model_config = ConfigDict(extra="forbid")

    publication: dict[str, Any]
    isrcs: list[str] = Field(min_length=1)
    window: ObservationWindow
    observed_at: AwareDatetime
    closed_by_source: bool = False
    quality_status: QualityStatus | None


class DispatchFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: NonEmpty
    content_id: NonEmpty
    observation_window: ObservationWindow
    status: ObservationMaturity


class DispatchRequest(BaseModel):
    """Run one outbox pass and report the delivery status of each named reward fact."""

    model_config = ConfigDict(extra="forbid")

    facts: list[DispatchFact] = Field(min_length=1)
    limit: int = Field(default=100, ge=1, le=1000)


class CostIngestRequest(BaseModel):
    """One cost entry in its declared ``currency`` (required)."""

    model_config = ConfigDict(extra="forbid")

    entity_id: NonEmpty
    entity_type: NonEmpty
    category: CostCategory
    amount: WireAmount
    currency: CurrencyCode
    period: NonEmpty
    description: str = ""
    incurred_at: AwareDatetime


class FxRateIn(BaseModel):
    """One recorded rate: 1 ``base`` = ``rate`` ``quote`` as of ``as_of``, from ``source``."""

    model_config = ConfigDict(extra="forbid")

    base: CurrencyCode
    quote: CurrencyCode
    rate: WireAmount = Field(gt=0)
    source: NonEmpty
    as_of: AwareDatetime

    def to_rate(self) -> FxRate:
        return FxRate(base=self.base, quote=self.quote, rate=self.rate, source=self.source, as_of=self.as_of)


class FxRateIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rates: list[FxRateIn] = Field(min_length=1)


class FeedbackRequest(BaseModel):
    """Evaluate one outcome and build its C7 leg-2 reward (ADR-0030, ADR-0031).

    Every measurement fact is explicit. An unmeasured raw metric is ``null``, never 0.
    Maturity is not accepted: it is derived from ``published_at``, the window and
    ``observed_at`` (``closed_by_source`` marks a window the source has closed).
    ``net_contribution`` and ``estimated_revenue`` are converted into the P9 reporting
    currency before the reward is computed. The reward is the
    ``engagement-contribution-proxy-v1`` proxy, not expected economic value.
    """

    model_config = ConfigDict(extra="forbid")

    lineage: ExperimentLineage
    unit_id: str = Field(min_length=1)
    net_contribution: MoneyIn
    views: int | None
    watch_time_minutes: float | None
    average_percentage_viewed: float | None
    subscribers_gained: int | None
    estimated_revenue: MoneyIn | None = Field(
        description="Platform estimate (revenue_status=estimated), not realized revenue."
    )
    window: ObservationWindow
    published_at: AwareDatetime
    closed_by_source: bool = False
    attribution_confidence: float = Field(ge=0.0, le=1.0)
    quality_status: QualityStatus | None
    observed_at: AwareDatetime
