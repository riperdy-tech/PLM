"""Unit tests for outcome evaluation and the reward proxy (Ruling R11, C7 leg 2, ADR-0031).

The reward is ``engagement-contribution-proxy-v1``: a bounded proxy, not expected
economic value. The sensitivity tests pin its shape: bounded in [0, 1], non-decreasing in
net contribution, views and watch time (others fixed), and independent of attribution
confidence, which is passed through to P2 instead of folded in. Maturity is derived from
the publication time; provisional outcomes are still produced (P2 records them without
applying them).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from analytics.domain.enums import ObservationWindow
from analytics.evaluation.feedback import (
    REWARD_CONTRIBUTION_SCALE,
    REWARD_VERSION,
    compute_normalized_reward,
    evaluate_experiment_outcome,
    generate_and_post_reward,
)
from analytics.storage.repositories import get_experiment_outcomes
from plm_contracts.money import CurrencyMismatchError, FxRate, FxRateMissingError, FxTable, Money
from plm_contracts.seams.c7 import ObservationMaturity
from sqlalchemy.orm import Session

from tests.support.analytics_db import analytics_db_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SCALE = REWARD_CONTRIBUTION_SCALE  # USD 50


def usd(amount: str | int) -> Money:
    return Money.of(amount, "USD")


def reward(net: str | int = 0, views: int = 0, watch: float = 0.0) -> float:
    return compute_normalized_reward(usd(net), views, watch, contribution_scale=SCALE)


def _evaluate(session: Session, **overrides: object):
    args: dict[str, object] = {
        "lineage": sample_lineage(),
        "unit_id": "vid_eval_01",
        "net_contribution": usd("85"),
        "reporting_currency": "USD",
        "fx": FxTable(),
        "views": 12000,
        "watch_time_minutes": 350.0,
        "window": ObservationWindow.D7,
        "published_at": NOW - timedelta(days=8),
        "attribution_confidence": 1.0,
        "quality_status": None,
        "observed_at": NOW,
    }
    args.update(overrides)
    return evaluate_experiment_outcome(session, **args)  # type: ignore[arg-type]


# --- the proxy's shape -----------------------------------------------------------------


def test_reward_version_names_a_proxy() -> None:
    assert REWARD_VERSION == "engagement-contribution-proxy-v1"
    assert "proxy" in REWARD_VERSION


def test_break_even_without_engagement_is_one_half() -> None:
    assert reward(0) == 0.5


@pytest.mark.parametrize(
    ("net", "views", "watch"),
    [("-1000000000", 0, 0.0), ("1000000000", 10**12, 1e12), ("-50", 0, 0.0), ("0", 10**9, 0.0)],
)
def test_reward_is_bounded_even_at_extremes(net: str, views: int, watch: float) -> None:
    assert 0.0 <= reward(net, views, watch) <= 1.0


def test_reward_is_non_decreasing_in_net_contribution() -> None:
    values = [reward(n, 1000, 60.0) for n in (-500, -50, -1, 0, 1, 50, 500)]
    assert values == sorted(values)
    assert values[0] < values[-1]


def test_reward_is_non_decreasing_in_views() -> None:
    values = [reward(10, v, 60.0) for v in (0, 1, 100, 10_000, 1_000_000)]
    assert values == sorted(values)
    assert values[0] < values[-1]


def test_reward_is_non_decreasing_in_watch_time() -> None:
    values = [reward(10, 1000, w) for w in (0.0, 1.0, 100.0, 10_000.0, 1_000_000.0)]
    assert values == sorted(values)
    assert values[0] < values[-1]


def test_reward_refuses_negative_engagement_and_foreign_currency() -> None:
    with pytest.raises(ValueError, match="negative"):
        reward(0, -1, 0.0)
    with pytest.raises(CurrencyMismatchError):
        compute_normalized_reward(Money.of(1, "KRW"), 0, 0.0, contribution_scale=SCALE)


def test_reward_is_unchanged_by_attribution_confidence_which_is_passed_through() -> None:
    with analytics_db_session() as session:
        high = _evaluate(session, attribution_confidence=1.0)
        low = _evaluate(session, lineage=sample_lineage(content_arm_id="arm_low"),
                        attribution_confidence=0.2)
    assert high.normalized_reward == low.normalized_reward
    assert (high.attribution_confidence, low.attribution_confidence) == (1.0, 0.2)


def test_scale_converts_into_the_reporting_currency_so_the_proxy_keeps_its_shape() -> None:
    fx = FxTable([FxRate(base="USD", quote="KRW", rate=Decimal(1400), source="test",
                         as_of=NOW - timedelta(days=1))])
    with analytics_db_session() as session:
        in_usd = _evaluate(session)
        in_krw = _evaluate(session, lineage=sample_lineage(content_arm_id="arm_krw"),
                           net_contribution=Money.of(85 * 1400, "KRW"),
                           reporting_currency="KRW", fx=fx)
    assert in_krw.normalized_reward == in_usd.normalized_reward
    assert in_krw.primary_value_currency == "KRW"


def test_missing_fx_rate_for_the_scale_is_an_error() -> None:
    with analytics_db_session() as session, pytest.raises(FxRateMissingError):
        _evaluate(session, net_contribution=Money.of(1000, "KRW"), reporting_currency="KRW")


def test_net_contribution_must_already_be_in_the_reporting_currency() -> None:
    with analytics_db_session() as session, pytest.raises(CurrencyMismatchError):
        _evaluate(session, net_contribution=Money.of(1000, "KRW"), reporting_currency="USD")


# --- derived maturity ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("window", "age", "closed", "expected"),
    [
        (ObservationWindow.D7, timedelta(days=3), False, ObservationMaturity.PROVISIONAL),
        (ObservationWindow.D7, timedelta(days=7), False, ObservationMaturity.MATURE),
        (ObservationWindow.H24, timedelta(hours=30), False, ObservationMaturity.MATURE),
        (ObservationWindow.LIFETIME, timedelta(days=400), False, ObservationMaturity.PROVISIONAL),
        (ObservationWindow.LIFETIME, timedelta(days=400), True, ObservationMaturity.CLOSED),
    ],
)
def test_maturity_is_derived_from_publication_time(
    window: ObservationWindow, age: timedelta, closed: bool, expected: ObservationMaturity
) -> None:
    with analytics_db_session() as session:
        outcome = _evaluate(session, window=window, published_at=NOW - age,
                            closed_by_source=closed)
    assert outcome.maturity is expected
    assert outcome.published_at == NOW - age


def test_observation_before_publication_is_refused() -> None:
    with analytics_db_session() as session, pytest.raises(ValueError, match="predate"):
        _evaluate(session, published_at=NOW + timedelta(hours=1))


def test_provisional_outcome_is_still_produced_and_posted_as_provisional() -> None:
    with analytics_db_session() as session:
        outcome = _evaluate(session, window=ObservationWindow.D7,
                            published_at=NOW - timedelta(days=2))
        payload = generate_and_post_reward(
            session, outcome, views=12000, watch_time_minutes=350.0,
            average_percentage_viewed=None, subscribers_gained=None, estimated_revenue=None,
            now=NOW,
        )
    assert outcome.maturity is ObservationMaturity.PROVISIONAL
    assert 0.0 <= outcome.normalized_reward <= 1.0
    assert payload.status == "provisional"


# --- recording and posting -------------------------------------------------------------


def test_evaluate_and_post_reward_flow() -> None:
    with analytics_db_session() as session:
        outcome = _evaluate(
            session,
            lineage=sample_lineage(
                experiment_id="exp_rnb_01", experiment_family_id="fam_rnb_01",
                content_arm_id="arm_rnb_01", content_genome_id="gen_rnb_01",
            ),
            unit_id="vid_rnb_01",
        )

        assert outcome.normalized_reward > 0.5
        assert outcome.primary_value == 85.0
        assert outcome.primary_value_currency == "USD"
        assert outcome.maturity is ObservationMaturity.MATURE

        payload = generate_and_post_reward(
            session,
            outcome,
            views=12000,
            watch_time_minutes=350.0,
            average_percentage_viewed=65.0,
            subscribers_gained=28,
            estimated_revenue=usd("95"),
            now=NOW,
        )

        assert payload.experiment_id == "exp_rnb_01"
        assert payload.arm_id == "arm_rnb_01"
        assert payload.content_id == "vid_rnb_01"
        assert payload.observation_window == "7d"
        assert payload.status == "mature"
        assert payload.normalized_reward == outcome.normalized_reward
        assert payload.raw_metrics["views"] == 12000.0
        assert payload.raw_metrics["estimated_revenue"] == 95.0
        assert payload.schema_version == "1.0"
        assert payload.reward_version == "engagement-contribution-proxy-v1"

        outcomes = get_experiment_outcomes(session, "exp_rnb_01")
        assert len(outcomes) == 1
        assert outcomes[0].id == outcome.id
        assert outcomes[0].published_at == NOW - timedelta(days=8)


def test_estimated_revenue_must_be_in_the_outcome_currency() -> None:
    with analytics_db_session() as session:
        outcome = _evaluate(session)
        with pytest.raises(CurrencyMismatchError):
            generate_and_post_reward(
                session, outcome, views=1, watch_time_minutes=1.0,
                average_percentage_viewed=None, subscribers_gained=None,
                estimated_revenue=Money.of(1000, "KRW"), now=NOW,
            )


def test_unmeasured_metrics_travel_as_null_not_zero() -> None:
    with analytics_db_session() as session:
        outcome = _evaluate(session, window=ObservationWindow.H24,
                            published_at=NOW - timedelta(hours=2), attribution_confidence=0.8)
        payload = generate_and_post_reward(
            session, outcome, views=100, watch_time_minutes=5.0,
            average_percentage_viewed=None, subscribers_gained=None, estimated_revenue=None,
            now=NOW,
        )
    wire = payload.to_wire()
    assert wire["raw_metrics"]["subscriber_gain"] is None
    assert wire["raw_metrics"]["estimated_revenue"] is None
    assert wire["status"] == "provisional"
