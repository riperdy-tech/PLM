"""Unit tests for unit economics and entity aggregation (P9 §19–§23, §96, ADR-0031).

Gate R2 (ADR-0031) is checked here by
``test_gate_r2_overlapping_window_snapshots_count_revenue_once`` and
``test_gate_r2_money_converts_reproducibly_from_the_same_fx_table``. Both run the real
ingestion, storage and aggregation code on in-memory SQLite; they prove the arithmetic,
not the production database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from analytics.domain.enums import CostCategory, ObservationWindow, PaybackStatus
from analytics.economics.calculator import compute_unit_economics
from analytics.economics.ledger import entity_economics
from analytics.ingestion.cost import ingest_cost_entry
from analytics.ingestion.distributor import ingest_distributor_statement
from analytics.ingestion.youtube import ingest_youtube_metrics
from analytics.storage.models import CostItemRow
from analytics.storage.repositories import (
    get_costs_for_entity,
    get_track_metrics,
    get_video_metrics,
    insert_fx_rate,
    load_fx_table,
)
from plm_contracts.money import CurrencyMismatchError, FxRate, FxRateMissingError, FxTable, Money
from sqlalchemy.orm import Session

from tests.support.analytics_db import analytics_db_session

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PUBLISHED = NOW - timedelta(days=30)


def usd(amount: str) -> Money:
    return Money.of(amount, "USD")


def krw(amount: str) -> Money:
    return Money.of(amount, "KRW")


# --- calculator ------------------------------------------------------------------------


def test_profitable_unit_economics_on_realized_revenue() -> None:
    econ = compute_unit_economics(
        "trk_001", "track", reporting_currency="USD", window=ObservationWindow.LIFETIME,
        estimated_revenue=None, realized_revenue=usd("100"), allocated_cost=usd("25"),
        streams=20000, daily_realized_revenue_rate=usd("5"), as_of=NOW,
    )
    assert econ.net_contribution == usd("75")
    assert econ.profit_margin == Decimal("0.75")
    assert econ.roi == Decimal(3)
    assert econ.realized_revenue_per_stream == usd("0.005")
    assert econ.payback_status is PaybackStatus.REPAID
    assert econ.payback_days == 5
    assert econ.as_of == NOW


def test_unprofitable_unit_economics() -> None:
    econ = compute_unit_economics(
        "trk_002", "track", reporting_currency="USD", window=ObservationWindow.LIFETIME,
        estimated_revenue=None, realized_revenue=usd("10"), allocated_cost=usd("50"), as_of=NOW,
    )
    assert econ.net_contribution == usd("-40")
    assert econ.profit_margin == Decimal(-4)
    assert econ.roi == Decimal("-0.8")
    assert econ.payback_status is PaybackStatus.NOT_REPAID
    assert econ.payback_days is None


def test_undefined_ratios_are_none_not_sentinels() -> None:
    free = compute_unit_economics(
        "trk_003", "track", reporting_currency="USD", window=ObservationWindow.LIFETIME,
        estimated_revenue=None, realized_revenue=usd("15"), allocated_cost=usd("0"), as_of=NOW,
    )
    assert free.net_contribution == usd("15")
    assert free.profit_margin == Decimal(1)
    assert free.roi is None  # zero cost: ROI is undefined, not 1.0
    assert free.payback_status is PaybackStatus.NOT_APPLICABLE

    nothing = compute_unit_economics(
        "trk_004", "track", reporting_currency="USD", window=ObservationWindow.LIFETIME,
        estimated_revenue=None, realized_revenue=usd("0"), allocated_cost=usd("0"), as_of=NOW,
    )
    assert nothing.net_contribution == usd("0")
    assert nothing.profit_margin is None
    assert nothing.roi is None


def test_estimated_revenue_is_reported_but_never_booked() -> None:
    econ = compute_unit_economics(
        "vid_001", "video", reporting_currency="USD", window=ObservationWindow.LIFETIME,
        estimated_revenue=usd("100"), realized_revenue=None, allocated_cost=usd("25"),
        views=20000, as_of=NOW,
    )
    assert econ.estimated_revenue == usd("100")
    assert econ.estimated_revenue_status.value == "estimated"
    assert econ.estimated_rpm == usd("5")
    assert econ.realized_revenue is None
    assert econ.net_contribution is None
    assert econ.net_contribution_blocked_reason == "realized_revenue_not_reported"
    assert econ.payback_status is None


def test_unknown_cost_withholds_net_contribution() -> None:
    econ = compute_unit_economics(
        "trk_005", "track", reporting_currency="USD", window=ObservationWindow.LIFETIME,
        estimated_revenue=None, realized_revenue=usd("10"), allocated_cost=None, as_of=NOW,
    )
    assert econ.net_contribution is None
    assert econ.net_contribution_blocked_reason == "cost_not_recorded"


def test_calculator_refuses_amounts_outside_the_reporting_currency() -> None:
    with pytest.raises(CurrencyMismatchError):
        compute_unit_economics(
            "trk_006", "track", reporting_currency="KRW", window=ObservationWindow.LIFETIME,
            estimated_revenue=None, realized_revenue=usd("10"), allocated_cost=krw("1"), as_of=NOW,
        )


# --- aggregation over stored facts ------------------------------------------------------


def _snapshot(session: Session, window: ObservationWindow, *, views: int, revenue: str,
              at: datetime, video_id: str = "vid_gate") -> None:
    ingest_youtube_metrics(
        session, video_id=video_id, channel_id="chan_gate", date_str=at.date().isoformat(),
        window=window, observed_at=at, views=views, watch_time_minutes=views / 10,
        estimated_revenue=usd(revenue),
    )


def _video_economics(session: Session, window: ObservationWindow, fx: FxTable,
                     currency: str = "USD", video_id: str = "vid_gate"):
    return entity_economics(
        entity_id=video_id, entity_type="video",
        video_metrics=get_video_metrics(session, video_id), track_metrics=[],
        costs=get_costs_for_entity(session, video_id), window=window,
        reporting_currency=currency, fx=fx, as_of=NOW,
    )


def test_gate_r2_overlapping_window_snapshots_count_revenue_once() -> None:
    """24h, 7d and lifetime snapshots of one video: revenue counted once, not summed."""
    with analytics_db_session() as session:
        _snapshot(session, ObservationWindow.H24, views=1000, revenue="2.00",
                  at=PUBLISHED + timedelta(days=1))
        _snapshot(session, ObservationWindow.D7, views=5000, revenue="9.00",
                  at=PUBLISHED + timedelta(days=7))
        _snapshot(session, ObservationWindow.LIFETIME, views=12000, revenue="20.00",
                  at=PUBLISHED + timedelta(days=30))
        session.flush()

        lifetime = _video_economics(session, ObservationWindow.LIFETIME, FxTable())
        seven_day = _video_economics(session, ObservationWindow.D7, FxTable())

    assert lifetime.estimated_revenue == usd("20.00")  # not 31.00
    assert lifetime.views == 12000  # not 18000
    assert seven_day.estimated_revenue == usd("9.00")
    assert seven_day.views == 5000


def test_latest_snapshot_of_a_window_replaces_earlier_ones() -> None:
    with analytics_db_session() as session:
        _snapshot(session, ObservationWindow.LIFETIME, views=8000, revenue="12.00",
                  at=PUBLISHED + timedelta(days=20))
        _snapshot(session, ObservationWindow.LIFETIME, views=12000, revenue="20.00",
                  at=PUBLISHED + timedelta(days=30))
        session.flush()
        econ = _video_economics(session, ObservationWindow.LIFETIME, FxTable())
    assert econ.estimated_revenue == usd("20.00")
    assert econ.views == 12000


def _fx_session(session: Session) -> None:
    insert_fx_rate(
        session,
        FxRate(base="USD", quote="KRW", rate=Decimal(1300), source="test_bank",
               as_of=NOW - timedelta(days=60)),
        recorded_at=NOW,
    )
    insert_fx_rate(
        session,
        FxRate(base="USD", quote="KRW", rate=Decimal(1400), source="test_bank",
               as_of=NOW - timedelta(days=5)),
        recorded_at=NOW,
    )


def _track_with_statement_and_cost(session: Session) -> None:
    ingest_distributor_statement(
        session, provider="spotify", period="2026-08", currency="USD",
        raw_content="isrc,track_id,streams,revenue\nUSPLM2600001,trk_fx,10000,40.00\n",
        source_reference="aug.csv", now=NOW - timedelta(days=30),
    )
    ingest_distributor_statement(
        session, provider="spotify", period="2026-09", currency="USD",
        raw_content="isrc,track_id,streams,revenue\nUSPLM2600001,trk_fx,5000,20.00\n",
        source_reference="sep.csv", now=NOW,
    )
    ingest_cost_entry(
        session, entity_id="trk_fx", entity_type="track", category=CostCategory.COMPUTE,
        amount=krw("13000"), period="2026-08", incurred_at=NOW - timedelta(days=40),
    )
    session.flush()


def _track_economics(session: Session, currency: str = "KRW"):
    return entity_economics(
        entity_id="trk_fx", entity_type="track", video_metrics=[],
        track_metrics=get_track_metrics(session, "trk_fx"),
        costs=get_costs_for_entity(session, "trk_fx"), window=ObservationWindow.LIFETIME,
        reporting_currency=currency, fx=load_fx_table(session), as_of=NOW,
    )


def test_statement_lines_and_costs_sum_per_period_in_the_reporting_currency() -> None:
    with analytics_db_session() as session:
        _fx_session(session)
        _track_with_statement_and_cost(session)
        econ = _track_economics(session)

    # Aug line converts at the rate in force on its receipt (1300), Sep at 1400.
    assert econ.realized_revenue == krw(str(40 * 1300 + 20 * 1400))
    assert econ.allocated_cost == krw("13000")
    assert econ.net_contribution == krw(str(40 * 1300 + 20 * 1400 - 13000))
    assert econ.streams == 15000
    assert econ.reporting_currency == "KRW"
    assert econ.fx_method == "transaction_date"
    assert len(econ.fx_rate_ids) == 2
    assert {c.rate_id for c in econ.conversions} == {*econ.fx_rate_ids, None}


def test_gate_r2_money_converts_reproducibly_from_the_same_fx_table() -> None:
    with analytics_db_session() as session:
        _fx_session(session)
        _track_with_statement_and_cost(session)
        first = _track_economics(session)
        second = _track_economics(session)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_missing_fx_rate_is_an_error_never_one() -> None:
    with analytics_db_session() as session:
        _track_with_statement_and_cost(session)  # USD lines, no rates recorded
        with pytest.raises(FxRateMissingError):
            _track_economics(session, currency="KRW")


def test_inverse_rate_is_applied_and_flagged() -> None:
    with analytics_db_session() as session:
        insert_fx_rate(
            session,
            FxRate(base="KRW", quote="USD", rate=Decimal("0.0008"), source="test_bank",
                   as_of=NOW - timedelta(days=90)),
            recorded_at=NOW,
        )
        _track_with_statement_and_cost(session)
        econ = _track_economics(session, currency="KRW")
    # Only KRW->USD is recorded, so USD->KRW applies its inverse: 60 USD / 0.0008.
    assert econ.realized_revenue == krw("75000")
    assert {c.inverted for c in econ.conversions if c.rate_id is not None} == {True}


def test_legacy_amount_without_currency_is_unreconciled_not_usd() -> None:
    with analytics_db_session() as session:
        _fx_session(session)
        _track_with_statement_and_cost(session)
        session.add(
            CostItemRow(
                id="cost_legacy", entity_id="trk_fx", entity_type="track", category="storage",
                amount=Decimal(5), currency=None, period="2026-07", description="legacy",
                incurred_at=NOW - timedelta(days=80),
            )
        )
        session.flush()
        econ = _track_economics(session)
    assert econ.unreconciled_record_ids == ["cost_legacy"]
    assert econ.net_contribution is None
    assert econ.net_contribution_blocked_reason == "unreconciled_records"
