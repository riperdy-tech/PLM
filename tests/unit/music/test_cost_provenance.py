"""B03: P4 provider credits cannot silently become USD amounts."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from services.music_factory.domain.enums import AttemptStatus
from services.music_factory.domain.models import (
    CostRecord,
    GenerationAttempt,
)
from services.music_factory.storage.models import (
    generation_attempts_table,
    provider_cost_records_table,
)
from services.music_factory.storage.repositories import (
    insert_cost_record,
    insert_generation_attempt,
)
from tests.support.music_db import music_db_session

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def _attempt(**changes: object) -> GenerationAttempt:
    data: dict[str, object] = {
        "attempt_id": "a1",
        "generation_plan_id": "p1",
        "creative_variant_id": "v1",
        "provider_id": "suno",
        "model_id": "model",
        "provider_job_id": "external1",
        "request_hash": "h1",
        "request_payload_hash": "h1",
        "status": AttemptStatus.SUCCEEDED,
        "started_at": NOW,
        "submitted_at": NOW,
        "actual_credits": 10.0,
        "credit_basis": "legacy_unverified",
    }
    data.update(changes)
    return GenerationAttempt(**data)


def _cost(**changes: object) -> CostRecord:
    data: dict[str, object] = {
        "cost_record_id": "c1",
        "attempt_id": "a1",
        "credits_actual": 10.0,
        "credit_basis": "legacy_unverified",
        "created_at": NOW,
    }
    data.update(changes)
    return CostRecord(**data)


def test_unknown_money_remains_unknown_even_with_provider_credits() -> None:
    attempt = _attempt()
    cost = _cost()
    assert attempt.actual_credits == cost.credits_actual == 10.0
    assert attempt.actual_cost is None and attempt.cost_estimate is None
    assert attempt.cost_currency is None and attempt.money_basis == "unpriced"
    assert cost.actual_amount is None and cost.estimated_amount is None
    assert cost.currency is None and cost.money_basis == "unpriced"
    assert cost.pricing_version == "unpriced"


@pytest.mark.parametrize(
    "changes",
    [
        {"actual_cost": 0.5},
        {"actual_cost": 0.0},
        {"cost_estimate": 0.5},
        {"cost_currency": "USD"},
    ],
)
def test_generation_attempt_refuses_unpriced_money_guess(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="unpriced attempt"):
        _attempt(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"actual_amount": 0.5},
        {"actual_amount": 0.0},
        {"estimated_amount": 0.5},
        {"currency": "USD"},
    ],
)
def test_cost_record_refuses_unpriced_money_guess(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="unpriced cost record"):
        _cost(**changes)


def test_reported_money_requires_actual_currency_and_source() -> None:
    with pytest.raises(ValidationError, match="provider-reported money"):
        _attempt(money_basis="provider_reported", actual_cost=0.5)
    with pytest.raises(ValidationError, match="provider-reported cost"):
        _cost(money_basis="provider_reported", actual_amount=0.5, currency="USD")
    record = _cost(
        money_basis="provider_reported", actual_amount=0.5, currency="USD",
        pricing_version="provider-receipt-1",
        cost_receipt_ref="provider-invoice-verified-123",
    )
    assert record.actual_amount == 0.5 and record.currency == "USD"


def test_unknown_provider_credits_remain_null_not_zero() -> None:
    attempt = _attempt(actual_credits=None, credit_basis="unknown")
    cost = _cost(credits_actual=None, credit_basis="unknown")
    assert attempt.actual_credits is None and cost.credits_actual is None
    assert attempt.credit_basis == cost.credit_basis == "unknown"


@pytest.mark.parametrize("credit", [0.0, 10.0])
def test_unknown_usage_rejects_fabricated_credit_numbers(credit: float) -> None:
    with pytest.raises(ValidationError, match="unknown credit usage"):
        _attempt(actual_credits=credit, credit_basis="unknown")
    with pytest.raises(ValidationError, match="unknown credit usage"):
        _cost(credits_actual=credit, credit_basis="unknown")


def test_reported_credits_require_a_receipt_not_just_a_number() -> None:
    with pytest.raises(ValidationError, match="reported credits"):
        _attempt(actual_credits=10.0, credit_basis="provider_reported")
    with pytest.raises(ValidationError, match="reported credits"):
        _cost(credits_actual=10.0, credit_basis="provider_reported")


def test_old_amounts_remain_visible_but_not_verified_money() -> None:
    old = _attempt(actual_cost=0.5, cost_estimate=0.4, money_basis="legacy_unverified")
    cost = _cost(
        actual_amount=0.5, currency="USD", money_basis="legacy_unverified",
        pricing_version="price-v1",
    )
    assert old.actual_cost == 0.5
    assert old.money_basis == cost.money_basis == "legacy_unverified"


def test_database_rejects_unpriced_attempt_with_numeric_cost() -> None:
    # Even direct SQL bypassing Pydantic cannot launder an old amount into a
    # supposedly unpriced row.
    with music_db_session() as session:
        insert_generation_attempt(
            session, _attempt(actual_cost=0.5, money_basis="legacy_unverified"),
        )
        session.flush()
        with pytest.raises(IntegrityError):
            session.execute(
                update(generation_attempts_table)
                .where(generation_attempts_table.c.id == "a1")
                .values(money_basis="unpriced")
            )
        session.rollback()


def test_database_rejects_provider_reported_without_receipt() -> None:
    with music_db_session() as session:
        insert_cost_record(
            session, _cost(
                actual_amount=0.5, currency="USD",
                money_basis="legacy_unverified", pricing_version="provider-rate-v2",
            ),
        )
        session.flush()
        with pytest.raises(IntegrityError):
            session.execute(
                update(provider_cost_records_table)
                .where(provider_cost_records_table.c.id == "c1")
                .values(money_basis="provider_reported")
            )
        session.rollback()
