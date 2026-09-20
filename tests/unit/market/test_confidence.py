# tests/unit/market/test_confidence.py
from datetime import UTC, datetime

import pytest

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.features.confidence import ConfidenceBreakdown, confidence
from services.market_intelligence.normalization.youtube import normalize_video_object
from services.market_intelligence.storage import repositories


def test_confidence_is_bounded_and_independent_of_any_score() -> None:
    assert confidence(ConfidenceBreakdown(1.0, 1.0, 1.0, 1.0, 1.0)) == 1.0
    assert confidence(ConfidenceBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)) == 0.0
    middle = confidence(ConfidenceBreakdown(0.5, 0.0, 0.0, 1.0, 0.5))
    assert 0.0 < middle < 1.0


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
