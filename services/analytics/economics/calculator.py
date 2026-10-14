"""Unit-economics arithmetic in the reporting currency (P9 §19–§23, §96, §97, ADR-0031).

Every amount is `Money` already converted into ``reporting_currency``; an amount in any
other currency is refused (`CurrencyMismatchError`), never coerced. Estimated revenue
(YouTube platform estimates) is reported next to realized revenue (reported or final
statements) and never enters the net contribution. A ratio whose inputs are unknown or
whose divisor is zero is ``None``, not a sentinel such as ``-1`` or ``1``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from plm_contracts.money import CurrencyMismatchError, Money
from plm_platform.clock import require_aware

from ..domain.enums import ObservationWindow, PaybackStatus
from ..domain.models import FxProvenance, UnitEconomics

__all__ = ["RATIO_QUANTUM", "compute_unit_economics"]

RATIO_QUANTUM = Decimal("0.000001")


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(RATIO_QUANTUM, rounding=ROUND_HALF_EVEN)


def _require_currency(name: str, money: Money | None, currency: str) -> None:
    if money is not None and money.currency != currency:
        raise CurrencyMismatchError(
            f"{name} is in {money.currency}; convert to the reporting currency {currency} first"
        )


def compute_unit_economics(
    entity_id: str,
    entity_type: str,
    *,
    reporting_currency: str,
    window: ObservationWindow,
    estimated_revenue: Money | None,
    realized_revenue: Money | None,
    allocated_cost: Money | None,
    views: int | None = None,
    streams: int | None = None,
    daily_realized_revenue_rate: Money | None = None,
    blocked_reason: str | None = None,
    conversions: Sequence[FxProvenance] = (),
    unreconciled_record_ids: Sequence[str] = (),
    as_of: datetime,
) -> UnitEconomics:
    """Compute unit economics from amounts already in ``reporting_currency``.

    Args:
        estimated_revenue: Latest platform estimate for ``window``; shown, never booked.
        realized_revenue: Reported/final statement revenue; ``None`` when none reported.
        allocated_cost: Allocated costs; ``None`` when no cost was recorded.
        blocked_reason: Set by the caller when the inputs are known to be incomplete
            (e.g. unreconciled records); the net contribution is then withheld.
        conversions: FX provenance of every converted amount (P9 §23).
        as_of: Computation timestamp, supplied by the caller.
    """
    now = require_aware(as_of)
    for name, money in (
        ("estimated_revenue", estimated_revenue),
        ("realized_revenue", realized_revenue),
        ("allocated_cost", allocated_cost),
        ("daily_realized_revenue_rate", daily_realized_revenue_rate),
    ):
        _require_currency(name, money, reporting_currency)

    reason = blocked_reason
    if reason is None and realized_revenue is None:
        reason = "realized_revenue_not_reported"
    if reason is None and allocated_cost is None:
        reason = "cost_not_recorded"

    net: Money | None = None
    profit_margin: Decimal | None = None
    roi: Decimal | None = None
    payback_status: PaybackStatus | None = None
    payback_days: int | None = None
    if reason is None and realized_revenue is not None and allocated_cost is not None:
        net = realized_revenue - allocated_cost
        profit_margin = _ratio(net.amount, realized_revenue.amount)
        roi = _ratio(net.amount, allocated_cost.amount)
        if allocated_cost.amount <= 0:
            payback_status = PaybackStatus.NOT_APPLICABLE
            payback_days = 0
        elif realized_revenue.compare(allocated_cost) >= 0:
            payback_status = PaybackStatus.REPAID
            if daily_realized_revenue_rate is not None and daily_realized_revenue_rate.amount > 0:
                days = allocated_cost.amount / daily_realized_revenue_rate.amount
                payback_days = max(1, int(days))
        else:
            payback_status = PaybackStatus.NOT_REPAID

    estimated_rpm: Money | None = None
    if estimated_revenue is not None and views is not None and views > 0:
        estimated_rpm = Money(amount=estimated_revenue.amount * 1000 / views, currency=reporting_currency)

    per_stream: Money | None = None
    if realized_revenue is not None and streams is not None and streams > 0:
        per_stream = Money(amount=realized_revenue.amount / streams, currency=reporting_currency)

    rate_ids = sorted({c.rate_id for c in conversions if c.rate_id is not None})
    return UnitEconomics(
        entity_id=entity_id,
        entity_type=entity_type,
        reporting_currency=reporting_currency,
        window=window,
        estimated_revenue=estimated_revenue,
        realized_revenue=realized_revenue,
        allocated_cost=allocated_cost,
        net_contribution=net,
        net_contribution_blocked_reason=None if net is not None else reason,
        profit_margin=profit_margin,
        roi=roi,
        views=views,
        streams=streams,
        estimated_rpm=estimated_rpm,
        realized_revenue_per_stream=per_stream,
        payback_days=payback_days,
        payback_status=payback_status,
        fx_rate_ids=rate_ids,
        conversions=list(conversions),
        unreconciled_record_ids=sorted(unreconciled_record_ids),
        as_of=now,
    )
