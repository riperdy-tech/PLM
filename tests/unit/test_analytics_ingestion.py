"""Unit tests for metrics, statement, cost and FX ingestion (P9 §16, §23, §53, §54, R10, ADR-0031)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from analytics.domain.enums import CostCategory, ObservationWindow, RevenueStatus
from analytics.ingestion.cost import ingest_cost_entry
from analytics.ingestion.distributor import StatementRejected, ingest_distributor_statement
from analytics.ingestion.youtube import ingest_youtube_metrics
from analytics.storage.models import VideoMetricRow
from analytics.storage.repositories import (
    FxRateConflictError,
    get_costs_for_entity,
    get_track_metrics,
    get_video_metrics,
    insert_fx_rate,
    load_fx_table,
)
from plm_contracts.measurement import ObservationKind
from plm_contracts.money import FxRate, Money

from tests.support.analytics_db import analytics_db_session

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_youtube_metrics_ingestion_keeps_amount_and_currency() -> None:
    with analytics_db_session() as session:
        obs = ingest_youtube_metrics(
            session,
            video_id="vid_yt_001",
            channel_id="chan_001",
            date_str="2026-09-26",
            window=ObservationWindow.H24,
            observed_at=NOW,
            views=25000,
            watch_time_minutes=850.2,
            average_view_duration_seconds=122.5,
            average_percentage_viewed=68.2,
            subscribers_gained=45,
            subscribers_lost=3,
            estimated_revenue=Money.of("82.40", "USD"),
        )

        assert obs.video_id == "vid_yt_001"
        assert obs.observation_kind is ObservationKind.CUMULATIVE_SNAPSHOT
        assert obs.revenue_status is RevenueStatus.ESTIMATED
        assert obs.observed_at == NOW

        fetched = get_video_metrics(session, "vid_yt_001")
        assert len(fetched) == 1
        assert fetched[0].views == 25000
        assert fetched[0].estimated_revenue == Money.of("82.40", "USD")
        assert fetched[0].observed_at == NOW


def test_unmeasured_youtube_metrics_are_stored_as_null_not_zero() -> None:
    with analytics_db_session() as session:
        ingest_youtube_metrics(
            session, video_id="vid_null", channel_id="chan_001", date_str="2026-09-26",
            window=ObservationWindow.D7, observed_at=NOW, views=10,
        )
        session.flush()
        row = session.get(VideoMetricRow, get_video_metrics(session, "vid_null")[0].id)
        assert row is not None
        assert row.views == 10
        assert row.watch_time_minutes is None
        assert row.subscribers_gained is None
        assert row.estimated_revenue_amount is None
        assert row.estimated_revenue_currency is None
        fetched = get_video_metrics(session, "vid_null")[0]
    assert fetched.estimated_revenue is None
    assert fetched.estimated_revenue_unreconciled is None


def test_legacy_youtube_revenue_without_currency_loads_as_unreconciled() -> None:
    with analytics_db_session() as session:
        session.add(
            VideoMetricRow(
                id="obs_legacy", video_id="vid_legacy", channel_id="c", window="lifetime",
                date_str="2026-09-01", observation_kind="cumulative_snapshot", views=0,
                estimated_revenue_amount=Decimal("3.5"), estimated_revenue_currency=None,
                revenue_status="estimated", source="youtube_analytics", status="valid",
                observed_at=NOW,
            )
        )
        session.flush()
        fetched = get_video_metrics(session, "vid_legacy")[0]
    assert fetched.estimated_revenue is None
    assert fetched.estimated_revenue_unreconciled == Decimal("3.5")


def test_distributor_statement_ingestion_and_deduplication() -> None:
    with analytics_db_session() as session:
        csv_data = (
            "isrc,track_id,streams,revenue\n"
            "USPLM2600001,trk_01,10000,40.10\n"
            "USPLM2600002,trk_02,5000,20.20\n"
        )

        stmt1, tracks1 = ingest_distributor_statement(
            session, provider="spotify", period="2026-09", currency="USD",
            raw_content=csv_data, source_reference="spotify_statement_09.csv", now=NOW,
        )

        assert stmt1.total_revenue == Money.of("60.30", "USD")  # exact, no float drift
        assert stmt1.total_streams == 15000
        assert len(tracks1) == 2
        assert stmt1.received_at == NOW
        assert all(t.observed_at == NOW for t in tracks1)
        assert all(t.observation_kind is ObservationKind.INCREMENTAL for t in tracks1)
        assert all(t.window is None for t in tracks1)

        stmt2, tracks2 = ingest_distributor_statement(
            session, provider="spotify", period="2026-09", currency="USD",
            raw_content=csv_data, source_reference="spotify_statement_09.csv", now=NOW,
        )
        assert stmt2.id == stmt1.id
        assert len(tracks2) == 0  # no duplicates inserted

        t_metrics = get_track_metrics(session, "trk_01")
        assert len(t_metrics) == 1
        assert t_metrics[0].streams == 10000
        assert t_metrics[0].reported_revenue == Money.of("40.10", "USD")
        assert t_metrics[0].date_str == "2026-09"


def test_statement_revenue_column_named_for_the_declared_currency_is_accepted() -> None:
    with analytics_db_session() as session:
        stmt, _ = ingest_distributor_statement(
            session, provider="melon", period="2026-09", currency="KRW",
            raw_content="isrc,streams,revenue_krw\nKRPLM2600001,100,1500\n",
            source_reference="melon.csv", now=NOW,
        )
    assert stmt.total_revenue == Money.of(1500, "KRW")


@pytest.mark.parametrize(
    ("csv_data", "code"),
    [
        ("isrc,streams,revenue_usd\nUSPLM2600001,1,1.00\n", "currency_mismatch"),
        ("isrc,streams,revenue\nUSPLM2600001,1,\n", "missing_required_field"),
        ("isrc,streams\nUSPLM2600001,1\n", "missing_required_field"),
        ("isrc,streams,revenue\nUSPLM2600001,1,abc\n", "invalid_amount"),
        ("isrc,streams,revenue\nUSPLM2600001,1.5,1\n", "invalid_count"),
    ],
)
def test_statement_with_a_bad_line_is_refused_before_anything_is_stored(
    csv_data: str, code: str
) -> None:
    with analytics_db_session() as session:
        with pytest.raises(StatementRejected) as exc:
            ingest_distributor_statement(
                session, provider="melon", period="2026-09", currency="KRW",
                raw_content=csv_data, source_reference="bad.csv", now=NOW,
            )
        assert get_track_metrics(session, "trk_x") == []
    assert exc.value.error.code == code


def test_statement_rows_with_float_amounts_are_refused() -> None:
    with analytics_db_session() as session, pytest.raises(StatementRejected) as exc:
        ingest_distributor_statement(
            session, provider="mock", period="2026-09", currency="USD", raw_content="x",
            source_reference="rows", rows=[{"isrc": "USPLM2600001", "streams": 1, "revenue": 1.5}],
            now=NOW,
        )
    assert exc.value.error.code == "invalid_amount"


def test_statement_needs_a_currency_and_a_realized_status() -> None:
    with analytics_db_session() as session:
        with pytest.raises(StatementRejected) as no_currency:
            ingest_distributor_statement(
                session, provider="mock", period="2026-09", currency="", raw_content="x",
                source_reference="r", rows=[], now=NOW,
            )
        with pytest.raises(StatementRejected) as estimated:
            ingest_distributor_statement(
                session, provider="mock", period="2026-09", currency="USD", raw_content="x",
                source_reference="r", rows=[], revenue_status=RevenueStatus.ESTIMATED, now=NOW,
            )
    assert no_currency.value.error.code == "invalid_currency"
    assert estimated.value.error.code == "invalid_revenue_status"


def test_cost_item_ingestion() -> None:
    with analytics_db_session() as session:
        cost = ingest_cost_entry(
            session,
            entity_id="vid_yt_001",
            entity_type="video",
            category=CostCategory.COMPUTE,
            amount=Money.of("15.75", "USD"),
            period="2026-09",
            description="GPU rendering compute cost",
            incurred_at=NOW,
        )

        assert cost.amount == Money.of("15.75", "USD")
        assert cost.incurred_at == NOW

        costs = get_costs_for_entity(session, "vid_yt_001")
        assert len(costs) == 1
        assert costs[0].category == CostCategory.COMPUTE
        assert costs[0].amount == Money.of("15.75", "USD")


def test_fx_rates_are_recorded_idempotently_and_conflicts_refused() -> None:
    rate = FxRate(base="USD", quote="KRW", rate=Decimal("1385.25"), source="bok",
                  as_of=NOW - timedelta(days=1))
    with analytics_db_session() as session:
        first = insert_fx_rate(session, rate, recorded_at=NOW)
        again = insert_fx_rate(session, rate, recorded_at=NOW + timedelta(hours=1))
        assert first == again
        with pytest.raises(FxRateConflictError):
            insert_fx_rate(session, rate.model_copy(update={"rate": Decimal(1390)}),
                           recorded_at=NOW)
        table = load_fx_table(session)
    assert table.rates == (rate,)
    converted = table.convert(Money.of(10, "USD"), "KRW", as_of=NOW)
    assert converted.converted == Money.of("13852.5", "KRW")
    assert converted.rate is not None and converted.rate.rate_id == rate.rate_id


def test_ingestion_refuses_a_naive_timestamp() -> None:
    with analytics_db_session() as session, pytest.raises(ValueError, match="timezone-aware"):
        ingest_youtube_metrics(
            session,
            video_id="vid_naive",
            channel_id="chan_001",
            date_str="2026-09-26",
            window=ObservationWindow.H24,
            views=1,
            watch_time_minutes=1.0,
            observed_at=datetime(2026, 10, 1, 12, 0),
        )
