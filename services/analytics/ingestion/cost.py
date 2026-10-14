"""Cost ingestion and allocation module (P9 §18, §25, §72, ADR-0031).

A cost is an incremental amount for one (entity, category, period). It carries the
currency it was incurred in, declared by the caller; no currency is assumed.
"""

from __future__ import annotations

from datetime import datetime

from plm_contracts.money import Money
from plm_platform.clock import require_aware
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
    amount: Money,
    period: str,
    description: str = "",
    incurred_at: datetime,
) -> CostItem:
    """Ingest a cost ledger record in its declared currency."""
    now = require_aware(incurred_at)
    cost_id = build_cost_id(entity_id, category.value, period)

    item = CostItem(
        id=cost_id,
        entity_id=entity_id,
        entity_type=entity_type,
        category=category,
        amount=amount,
        period=period,
        description=description or f"Cost for {entity_id} ({category.value})",
        incurred_at=now,
    )

    insert_cost_item(session, item)
    return item
