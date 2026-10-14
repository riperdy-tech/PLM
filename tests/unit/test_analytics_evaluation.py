"""Unit tests for experiment evaluation and feedback reward posting (Ruling R11, Seam C7 Leg 2)."""

from __future__ import annotations

from datetime import UTC, datetime

from analytics.domain.enums import ObservationWindow
from analytics.evaluation.feedback import (
    compute_normalized_reward,
    evaluate_experiment_outcome,
    generate_and_post_reward,
)
from analytics.storage.repositories import get_experiment_outcomes
from plm_contracts.seams.c7 import ObservationMaturity

from tests.support.analytics_db import analytics_db_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

def test_normalized_reward_bounds_and_monotonicity() -> None:
    # Break-even maps near ~0.50
    reward_neutral = compute_normalized_reward(0.0)
    assert 0.45 <= reward_neutral <= 0.55

    # Positive net contribution yields higher reward
    reward_high = compute_normalized_reward(150.0, views=10000)
    assert reward_high > reward_neutral
    assert reward_high <= 1.0

    # Negative net contribution yields lower reward
    reward_low = compute_normalized_reward(-150.0)
    assert reward_low < reward_neutral
    assert reward_low >= 0.0


def test_evaluate_and_post_reward_flow() -> None:
    with analytics_db_session() as session:
        outcome = evaluate_experiment_outcome(
            session,
            lineage=sample_lineage(
                experiment_id="exp_rnb_01", experiment_family_id="fam_rnb_01",
                content_arm_id="arm_rnb_01", content_genome_id="gen_rnb_01",
            ),
            unit_id="vid_rnb_01",
            net_contribution_usd=85.0,
            views=12000,
            watch_time_minutes=350.0,
            window=ObservationWindow.D7,
            maturity=ObservationMaturity.MATURE,
            attribution_confidence=1.0,
            quality_status=None,
            now=NOW,
        )

        assert outcome.normalized_reward > 0.5
        assert outcome.primary_value == 85.0

        # Post reward (Ruling R11)
        payload = generate_and_post_reward(
            session,
            outcome,
            views=12000,
            watch_time_minutes=350.0,
            average_percentage_viewed=65.0,
            subscribers_gained=28,
            estimated_revenue=95.0,
            now=NOW,
        )

        assert payload.experiment_id == "exp_rnb_01"
        assert payload.arm_id == "arm_rnb_01"
        assert payload.content_id == "vid_rnb_01"
        assert payload.observation_window == "7d"
        assert payload.status == "mature"
        assert payload.normalized_reward == outcome.normalized_reward
        assert payload.raw_metrics["views"] == 12000.0
        assert payload.schema_version == "1.0"
        assert payload.reward_version == "engagement-contribution-proxy-v1"

        # Check repository persistence
        outcomes = get_experiment_outcomes(session, "exp_rnb_01")
        assert len(outcomes) == 1
        assert outcomes[0].id == outcome.id


def test_unmeasured_metrics_travel_as_null_not_zero() -> None:
    with analytics_db_session() as session:
        outcome = evaluate_experiment_outcome(
            session,
            lineage=sample_lineage(),
            unit_id="vid_null_01",
            net_contribution_usd=10.0,
            views=100,
            watch_time_minutes=5.0,
            window=ObservationWindow.H24,
            maturity=ObservationMaturity.PROVISIONAL,
            attribution_confidence=0.8,
            quality_status=None,
            now=NOW,
        )
        payload = generate_and_post_reward(
            session, outcome, views=100, watch_time_minutes=5.0,
            average_percentage_viewed=None, subscribers_gained=None, estimated_revenue=None,
            now=NOW,
        )
    wire = payload.to_wire()
    assert wire["raw_metrics"]["subscriber_gain"] is None
    assert wire["raw_metrics"]["estimated_revenue"] is None
    assert wire["status"] == "provisional"
