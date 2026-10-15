"""Unit tests for Phase P9 domain models, enums and deterministic IDs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from analytics.domain.enums import (
    CostCategory,
    DataFreshness,
    MetricSource,
    MetricStatus,
    ObservationWindow,
    PaybackStatus,
    RevenueStatus,
)
from analytics.domain.ids import (
    build_cost_id,
    build_observation_id,
    build_outcome_id,
    build_statement_id,
)
from analytics.domain.models import (
    RevenueStatement,
    VideoMetricObservation,
)
from plm_contracts.money import Money


def test_enums_values() -> None:
    assert MetricSource.YOUTUBE_ANALYTICS.value == "youtube_analytics"
    assert MetricSource.DISTRIBUTOR_STATEMENT.value == "distributor_statement"
    assert MetricStatus.VALID.value == "valid"
    assert ObservationWindow.D7.value == "7d"
    assert RevenueStatus.REPORTED.value == "reported"
    assert PaybackStatus.REPAID.value == "repaid"
    assert DataFreshness.FRESH.value == "fresh"
    assert CostCategory.COMPUTE.value == "compute"


def test_deterministic_ids_generation() -> None:
    obs1 = build_observation_id("vid_01", "views", "24h", "2026-09-26")
    obs2 = build_observation_id("vid_01", "views", "24h", "2026-09-26")
    obs3 = build_observation_id("vid_01", "views", "7d", "2026-09-26")
    assert obs1 == obs2
    assert obs1 != obs3
    assert obs1.startswith("obs_")

    stmt1 = build_statement_id("spotify", "2026-09", "hash123")
    assert stmt1.startswith("stmt_")

    cost1 = build_cost_id("vid_01", "compute", "2026-09")
    assert cost1.startswith("cost_")

    outc1 = build_outcome_id("exp_01", "vid_01", "7d", "mature")
    assert outc1.startswith("outc_")
    assert build_outcome_id("exp_01", "vid_01", "7d", "mature") == outc1
    # Two units (two treatments' videos) of one experiment are two outcomes, and a
    # provisional and a mature measurement of one window are two facts.
    assert build_outcome_id("exp_01", "vid_02", "7d", "mature") != outc1
    assert build_outcome_id("exp_01", "vid_01", "7d", "provisional") != outc1


def test_domain_models_creation() -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    obs = VideoMetricObservation(
        id="obs_01",
        video_id="vid_01",
        channel_id="chan_01",
        window=ObservationWindow.H24,
        date_str="2026-09-26",
        views=1200,
        watch_time_minutes=45.2,
        average_view_duration_seconds=None,
        average_percentage_viewed=None,
        subscribers_gained=None,
        subscribers_lost=None,
        estimated_revenue=None,
        observed_at=now,
    )
    assert obs.views == 1200
    assert obs.observation_kind.value == "cumulative_snapshot"

    stmt = RevenueStatement(
        id="stmt_01",
        provider="spotify",
        period="2026-09",
        currency="USD",
        total_revenue_amount=Decimal("150.10"),
        total_streams=40000,
        file_hash="chk_123",
        source_reference="statement.csv",
        received_at=now,
        unattributed_revenue_amount=Decimal(0),
        unattributed_row_count=0,
    )
    assert stmt.total_revenue == Money.of("150.10", "USD")


def test_statement_without_declared_currency_has_no_money() -> None:
    stmt = RevenueStatement(
        id="stmt_legacy", provider="spotify", period="2026-08", currency=None,
        total_revenue_amount=Decimal(1), total_streams=1, file_hash="h",
        source_reference="legacy.csv", received_at=datetime(2026, 9, 1, tzinfo=UTC),
        unattributed_revenue_amount=Decimal(0), unattributed_row_count=0,
    )
    assert stmt.total_revenue is None
