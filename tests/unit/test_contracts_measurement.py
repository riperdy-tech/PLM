"""Observation kinds: each aggregation accepts only the records it is valid for."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from plm_contracts import ObservationWindow
from plm_contracts.measurement import (
    MeasurementKindError,
    MetricRecord,
    ObservationKind,
    effective_outcome,
    latest_snapshot,
    maturity_at,
    sum_incremental,
)
from plm_contracts.seams.c7 import ObservationMaturity

PUBLISHED = datetime(2026, 9, 1, tzinfo=UTC)


def _snapshot(record_id: str, window: ObservationWindow, value: int, days: int) -> MetricRecord:
    return MetricRecord(
        record_id=record_id, kind=ObservationKind.CUMULATIVE_SNAPSHOT, entity_id="vid_1",
        metric="estimated_revenue", value=Decimal(value), window=window,
        as_of=PUBLISHED + timedelta(days=days),
    )


def _outcome(record_id: str, window: ObservationWindow, value: str, *,
             supersedes: str | None = None) -> MetricRecord:
    return MetricRecord(
        record_id=record_id,
        kind=ObservationKind.REVISION if supersedes else ObservationKind.EXPERIMENT_OUTCOME,
        entity_id="exp_1/pkg_1", metric="normalized_reward", value=Decimal(value),
        window=window, as_of=PUBLISHED + timedelta(days=30), supersedes=supersedes,
    )


def test_24h_7d_and_lifetime_snapshots_count_revenue_once() -> None:
    """Gate R2: three overlapping snapshots of one video never sum."""
    records = [
        _snapshot("r24h", ObservationWindow.H24, 10, 1),
        _snapshot("r7d", ObservationWindow.D7, 60, 7),
        _snapshot("rlife", ObservationWindow.LIFETIME, 100, 30),
    ]
    with pytest.raises(MeasurementKindError):
        sum_incremental(records)
    lifetime = latest_snapshot(records, window=ObservationWindow.LIFETIME)
    assert lifetime is not None and lifetime.value == Decimal(100)


def test_latest_snapshot_of_a_window_wins_and_unobserved_is_none() -> None:
    records = [
        _snapshot("a", ObservationWindow.LIFETIME, 50, 10),
        _snapshot("b", ObservationWindow.LIFETIME, 90, 20),
    ]
    latest = latest_snapshot(records, window=ObservationWindow.LIFETIME)
    assert latest is not None and latest.record_id == "b"
    assert latest_snapshot(records, window=ObservationWindow.D90) is None


def test_incremental_periods_sum_and_overlap_is_refused() -> None:
    def line(record_id: str, period: str, value: str) -> MetricRecord:
        return MetricRecord(
            record_id=record_id, kind=ObservationKind.INCREMENTAL, entity_id="isrc_1",
            metric="revenue", value=Decimal(value), period=period, as_of=PUBLISHED,
        )

    assert sum_incremental([line("s1", "2026-09", "1.10"), line("s2", "2026-10", "2.20")]) == (
        Decimal("3.30")
    )
    with pytest.raises(MeasurementKindError):
        sum_incremental([line("s1", "2026-09", "1.10"), line("s3", "2026-09", "1.10")])


def test_a_revision_replaces_the_amount_it_supersedes() -> None:
    original = MetricRecord(
        record_id="s1", kind=ObservationKind.INCREMENTAL, entity_id="isrc_1", metric="revenue",
        value=Decimal(5), period="2026-09", as_of=PUBLISHED,
    )
    revised = MetricRecord(
        record_id="s1r", kind=ObservationKind.REVISION, entity_id="isrc_1", metric="revenue",
        value=Decimal(4), period="2026-09", as_of=PUBLISHED, supersedes="s1",
    )
    assert sum_incremental([original, revised]) == Decimal(4)


def test_three_windows_of_one_content_give_one_effective_outcome() -> None:
    """Gate R2: only the outcome at the declared horizon is effective."""
    outcomes = [
        _outcome("o24h", ObservationWindow.H24, "0.9"),
        _outcome("o7d", ObservationWindow.D7, "0.6"),
        _outcome("o28d", ObservationWindow.D28, "0.4"),
    ]
    chosen = effective_outcome(outcomes, horizon=ObservationWindow.D28)
    assert chosen is not None and chosen.record_id == "o28d"
    assert effective_outcome(outcomes[:2], horizon=ObservationWindow.D28) is None


def test_two_live_outcomes_at_the_horizon_are_refused_unless_one_revises() -> None:
    first = _outcome("o28d", ObservationWindow.D28, "0.4")
    duplicate = _outcome("o28d_again", ObservationWindow.D28, "0.5")
    with pytest.raises(MeasurementKindError):
        effective_outcome([first, duplicate], horizon=ObservationWindow.D28)
    revision = _outcome("o28d_rev", ObservationWindow.D28, "0.5", supersedes="o28d")
    chosen = effective_outcome([first, revision], horizon=ObservationWindow.D28)
    assert chosen is not None and chosen.record_id == "o28d_rev"


def test_maturity_is_derived_from_publication_and_window() -> None:
    day = timedelta(days=1)
    assert maturity_at(published_at=PUBLISHED, window=ObservationWindow.D7,
                       as_of=PUBLISHED + 6 * day) is ObservationMaturity.PROVISIONAL
    assert maturity_at(published_at=PUBLISHED, window=ObservationWindow.D7,
                       as_of=PUBLISHED + 7 * day) is ObservationMaturity.MATURE
    assert maturity_at(published_at=PUBLISHED, window=ObservationWindow.LIFETIME,
                       as_of=PUBLISHED + 400 * day) is ObservationMaturity.PROVISIONAL
    assert maturity_at(published_at=PUBLISHED, window=ObservationWindow.LIFETIME,
                       as_of=PUBLISHED + 400 * day,
                       closed_by_source=True) is ObservationMaturity.CLOSED
    with pytest.raises(ValueError):
        maturity_at(published_at=PUBLISHED, window=ObservationWindow.D7,
                    as_of=PUBLISHED - day)


def test_records_must_carry_the_facts_their_kind_needs() -> None:
    with pytest.raises(ValueError):
        MetricRecord(record_id="x", kind=ObservationKind.INCREMENTAL, entity_id="e",
                     metric="m", value=Decimal(1), as_of=PUBLISHED)
    with pytest.raises(ValueError):
        MetricRecord(record_id="x", kind=ObservationKind.CUMULATIVE_SNAPSHOT, entity_id="e",
                     metric="m", value=Decimal(1), as_of=PUBLISHED)
    with pytest.raises(ValueError):
        MetricRecord(record_id="x", kind=ObservationKind.REVISION, entity_id="e",
                     metric="m", value=Decimal(1), as_of=PUBLISHED, window=ObservationWindow.D7)
