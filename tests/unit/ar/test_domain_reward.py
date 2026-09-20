# tests/unit/ar/test_domain_reward.py
from datetime import UTC, datetime

import pytest
from plm_contracts import ObservationWindow, Tri

from services.autonomous_ar.domain.enums import ObservationMaturity
from services.autonomous_ar.domain.reward import (
    RESERVED_VALUE_TERMS,
    RewardObservation,
    observation_key,
    tri_from_optional,
)


def test_tri_from_optional_is_the_only_null_path_and_zero_is_a_measurement() -> None:
    assert tri_from_optional(None).state.value == "unknown"
    known = tri_from_optional(0.0)
    assert known.state.value == "known" and known.unwrap() == 0.0


def test_reserved_economic_terms_are_kept_and_never_invented() -> None:
    assert set(RESERVED_VALUE_TERMS) == {
        "revenue", "subscriber_value", "catalog_value", "spotify_conversion",
        "future_discovery_value", "production_cost",
    }
    observation = RewardObservation(
        experiment_id="exp-1", arm_id="a", content_id="c",
        observation_window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
        observed_at=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        raw_metrics={"views": Tri.known(42000.0)}, normalized_reward=Tri.known(0.74),
        reward_version="p9-reward-v1", attribution_confidence=0.99,
        economic_terms={"revenue": Tri.unknown()},
    )
    assert observation.economic_terms["revenue"].state.value == "unknown"
    assert observation.key == observation_key("exp-1", ObservationWindow.D7)
    with pytest.raises(ValueError, match="reserved"):
        RewardObservation(
            experiment_id="exp-1", arm_id="a", content_id="c",
            observation_window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
            observed_at=observation.observed_at, raw_metrics={},
            normalized_reward=Tri.known(0.5), reward_version="p9-reward-v1",
            attribution_confidence=0.9, economic_terms={"made_up_term": Tri.known(1.0)},
        )
