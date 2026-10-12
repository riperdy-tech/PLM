"""Cost ingestion and allocation module (P9 §18, §72)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..domain.enums import CostCategory
from ..domain.ids import build_cost_id
from ..domain.models import CostItem
from ..storage.repositories import insert_cost_item


def ingest_cost_entry(
    session: Session,
    *,
    entity_id: str,
    entity_type: str,
    category: CostCategory,
    amount_usd: float,
    period: str,
    description: str = "",
    incurred_at: datetime | None = None,
) -> CostItem:
    """Ingest a cost ledger record."""
    now = incurred_at or datetime.now(UTC)
    cost_id = build_cost_id(entity_id, category.value, period)

    item = CostItem(
        id=cost_id,
        entity_id=entity_id,
        entity_type=entity_type,
        category=category,
        amount_usd=round(amount_usd, 4),
        period=period,
        description=description or f"Cost for {entity_id} ({category.value})",
        incurred_at=now,
    )

    insert_cost_item(session, item)
    return item
