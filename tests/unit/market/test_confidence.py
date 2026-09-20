# tests/unit/market/test_confidence.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.enums import ProviderObjectType, ScoringComponent
from services.market_intelligence.domain.value_objects import ScoreComponents
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.features.confidence import ConfidenceBreakdown, confidence
from services.market_intelligence.normalization.youtube import normalize_video_object
from services.market_intelligence.scoring.calibration import default_weights
from services.market_intelligence.scoring.opportunity import score_arm
from services.market_intelligence.storage import repositories


def test_confidence_is_the_weighted_breakdown_of_its_own_inputs() -> None:
    """Review fix 4: exact values, not bounds that construction already guarantees.

    Default weights: sample 0.35, source diversity 0.20, time-series depth 0.15,
    taxonomy 0.15, freshness 0.15.
    """
    assert confidence(ConfidenceBreakdown(1.0, 0.0, 0.0, 0.0, 0.0)) == pytest.approx(0.35)
    assert confidence(ConfidenceBreakdown(0.5, 0.0, 0.0, 1.0, 0.5)) == pytest.approx(0.4)
    assert confidence(ConfidenceBreakdown(1.0, 1.0, 1.0, 1.0, 1.0)) == 1.0
    assert confidence(ConfidenceBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)) == 0.0


def test_a_bad_component_reduces_confidence_even_when_the_data_looks_strong() -> None:
    strong = ConfidenceBreakdown(1.0, 1.0, 1.0, 1.0, 1.0)
    thin = ConfidenceBreakdown(0.2, 1.0, 0.0, 1.0, 1.0)
    assert confidence(thin) < confidence(strong)


def test_out_of_range_components_are_refused() -> None:
    with pytest.raises(ValueError):
        ConfidenceBreakdown(1.5, 0.0, 0.0, 0.0, 0.0)


def test_quarantined_record_never_becomes_an_observation(market_session) -> None:
    """Review Focus 5: a quarantined record cannot reach features or scores."""
    outcome = normalize_video_object(
        FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                      provider_object_id="short", observed_at=AS_OF,
                      payload={"id": "short"}),
        entity_id="entity-1", source_id="source-1", raw_observation_id="raw-1",
        observed_at=AS_OF,
    )
    assert outcome.observation is None and outcome.quarantine is not None
    with pytest.raises(ValueError, match="quarantined"):
        repositories.insert_normalized_observation(market_session, outcome.observation)
    assert repositories.load_observations(market_session) == []


AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def test_no_observations_means_no_features_not_a_zero_score() -> None:
    assert compute_arm_features({}, as_of=AS_OF, settings=SETTINGS) == {}


def test_confidence_is_independent_of_the_score_for_real() -> None:
    """Review fix 4: the old test's name claimed independence and tested bounds.

    Both directions, at the arm level: two arms fed the *same* components but with
    materially different confidence (24 sightings vs one) get the same score; and
    one arm fed *different* components keeps the same confidence while the score
    moves. Confidence measures evidence quality and never touches the number.
    """
    dense = tuple(
        NormalizedObservation(
            entity_id="dense-e", source_id="s1", raw_observation_id=f"d{index}",
            observed_at=AS_OF - timedelta(days=24 - index),
            published_at=AS_OF - timedelta(days=90),
            views=Tri.known(1_000 + index * 50), subscriber_count=Tri.known(20_000),
        )
        for index in range(24)
    )
    sparse = (
        NormalizedObservation(
            entity_id="sparse-e", source_id="s1", raw_observation_id="s1",
            observed_at=AS_OF - timedelta(days=3), published_at=AS_OF - timedelta(days=30),
            views=Tri.known(500), subscriber_count=Tri.known(2_000),
        ),
    )
    features = compute_arm_features({"dense": dense, "sparse": sparse}, as_of=AS_OF,
                                    settings=SETTINGS)
    fifty = ScoreComponents.from_mapping({name.value: 50.0 for name in ScoringComponent})

    def scored(arm: str, components: ScoreComponents):
        return score_arm(features[arm], components, as_of=AS_OF, weights=default_weights(),
                         score_version=SETTINGS.score_version,
                         feature_version=SETTINGS.feature_version,
                         normalization_version=SETTINGS.normalization_version)

    dense_score = scored("dense", fifty)
    sparse_score = scored("sparse", fifty)
    assert dense_score.confidence - sparse_score.confidence > 0.20  # materially different
    assert dense_score.market_opportunity_score == sparse_score.market_opportunity_score

    ninety = ScoreComponents.from_mapping({name.value: 90.0 for name in ScoringComponent})
    stronger = scored("dense", ninety)
    assert stronger.confidence == dense_score.confidence  # …and the other direction
    assert stronger.market_opportunity_score > dense_score.market_opportunity_score
