# tests/unit/ar/test_domain_posterior.py
from datetime import UTC, datetime

import pytest

from services.autonomous_ar.domain.posterior import PosteriorState, PriorParameters


def test_posterior_refuses_negative_variance() -> None:
    with pytest.raises(ValueError, match="variance"):
        PosteriorState(
            arm_id="a", model_type="normal-normal", model_version="p2-nn-v1",
            prior_mean=0.5, prior_variance=0.04, posterior_mean=0.6,
            posterior_variance=-0.01, observation_count=1,
            effective_observation_count=1.0, update_version=1,
            last_update_at=datetime(2026, 9, 20, 6, 0, tzinfo=UTC),
        )


def test_prior_refuses_confidence_outside_zero_to_one() -> None:
    with pytest.raises(ValueError, match="confidence"):
        PriorParameters(mean=0.5, variance=0.04, confidence=1.1, prior_version="p2-prior-v1")
