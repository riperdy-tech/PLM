"""Financial ledger arithmetic and unit-economics calculations (P9 §19, §20, §21, §23).

Handles all edge cases: zero revenue, zero costs, negative margins, null divisors.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.enums import PaybackStatus
from ..domain.models import UnitEconomics


def compute_unit_economics(
    entity_id: str,
    entity_type: str,
    *,
    gross_revenue_usd: float,
    allocated_cost_usd: float,
    views: int | None = None,
    streams: int | None = None,
    daily_revenue_rate_usd: float | None = None,
    as_of: datetime | None = None,
) -> UnitEconomics:
    """Calculate canonical unit economics, P&L, ROI, and payback metrics.

    Args:
        entity_id: Identifier of the subject (video, track, release, arm).
        entity_type: Entity classification.
        gross_revenue_usd: Total gross monetary revenue earned.
        allocated_cost_usd: Total allocated production, distribution, and compute costs.
        views: Optional view count for YouTube video RPM computation.
        streams: Optional stream count for DSP revenue per stream computation.
        daily_revenue_rate_usd: Optional recent daily run-rate for payback day estimation.
        as_of: Computation timestamp.

    Returns:
        Immutable UnitEconomics record.
    """
    now = as_of or datetime.now(UTC)

    # 1. Net contribution
    net_contribution = round(gross_revenue_usd - allocated_cost_usd, 4)

    # 2. Profit margin (net / gross)
    if gross_revenue_usd > 0:
        profit_margin = round(net_contribution / gross_revenue_usd, 4)
    else:
        profit_margin = -1.0 if allocated_cost_usd > 0 else 0.0

    # 3. ROI (net / cost)
    if allocated_cost_usd > 0:
        roi = round(net_contribution / allocated_cost_usd, 4)
    else:
        roi = 1.0 if gross_revenue_usd > 0 else 0.0

    # 4. RPM (revenue per 1,000 views)
    rpm_usd: float | None = None
    if views is not None and views > 0:
        rpm_usd = round((gross_revenue_usd / views) * 1000.0, 4)

    # 5. Revenue per stream
    rev_per_stream: float | None = None
    if streams is not None and streams > 0:
        rev_per_stream = round(gross_revenue_usd / streams, 6)

    # 6. Payback status and payback days
    if allocated_cost_usd <= 0:
        payback_status = PaybackStatus.NOT_APPLICABLE
        payback_days = 0
    elif gross_revenue_usd >= allocated_cost_usd:
        payback_status = PaybackStatus.REPAID
        if daily_revenue_rate_usd and daily_revenue_rate_usd > 0:
            payback_days = max(1, int(allocated_cost_usd / daily_revenue_rate_usd))
        else:
            payback_days = None
    else:
        payback_status = PaybackStatus.NOT_REPAID
        payback_days = None

    return UnitEconomics(
        entity_id=entity_id,
        entity_type=entity_type,
        gross_revenue_usd=round(gross_revenue_usd, 4),
        allocated_cost_usd=round(allocated_cost_usd, 4),
        net_contribution_usd=net_contribution,
        profit_margin=profit_margin,
        roi=roi,
        rpm_usd=rpm_usd,
        revenue_per_stream_usd=rev_per_stream,
        payback_days=payback_days,
        payback_status=payback_status,
        as_of=now,
    )
