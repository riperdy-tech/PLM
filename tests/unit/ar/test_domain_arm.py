# tests/unit/ar/test_domain_arm.py
from datetime import UTC, datetime

import pytest

from services.autonomous_ar.domain.arm import (
    ArmDimensions,
    ContentArm,
    canonical_fingerprint,
)

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _dims() -> ArmDimensions:
    return ArmDimensions(
        genre="rnb", moods=("melancholic", "dreamy"), use_cases=("late_night_drive",),
        language="english", vocal_mode="vocal", duration_bucket="45_75m",
        visual_world="rainy_asian_city", title_archetype="time_place_mood",
    )


def test_fingerprint_is_order_stable_and_distinguishes_dimensions() -> None:
    same = ArmDimensions(genre="rnb", moods=("dreamy", "melancholic"),
                         use_cases=("late_night_drive",), language="english",
                         vocal_mode="vocal", duration_bucket="45_75m",
                         visual_world="rainy_asian_city", title_archetype="time_place_mood")
    other = ArmDimensions(genre="city_pop", moods=("melancholic", "dreamy"))
    assert canonical_fingerprint(_dims()) == canonical_fingerprint(same), (
        "mood token order must not change identity (§7: normalized canonical representation)"
    )
    assert canonical_fingerprint(_dims()) != canonical_fingerprint(other)


def test_null_component_is_never_a_zero() -> None:
    arm = ContentArm(
        arm_id="arm-1", dimensions=_dims(), p1_score=82.0, p1_confidence=0.7,
        component_scores={"demand": 70.0, "growth": None, "competitor_velocity": 55.0,
                          "competition": None, "audience_quality": None,
                          "cross_platform": None, "production_fit": 60.0},
        observation_count=4, source_count=2, as_of=AS_OF,
        score_version="p1-v1", freshness_seconds=3600.0,
    )
    # demand + competitor_velocity + production_fit are measured; four keys are null.
    assert arm.measured_component_count == 3
    assert arm.is_rankable() is True
    assert arm.component_scores["demand"] == 70.0 and arm.component_scores["growth"] is None


def test_rankability_requires_two_measured_components() -> None:
    scores = {"demand": 70.0, "growth": None, "competitor_velocity": None,
              "competition": None, "audience_quality": None, "cross_platform": None,
              "production_fit": None}
    arm = ContentArm(
        arm_id="arm-2", dimensions=_dims(), p1_score=90.0, p1_confidence=0.9,
        component_scores=scores, observation_count=1, source_count=1,
        as_of=AS_OF, score_version="p1-v1", freshness_seconds=0.0,
    )
    assert arm.measured_component_count == 1
    assert arm.is_rankable() is False, "ADR-0023 #5: one component is not enough to be ranked"


def test_naive_as_of_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ContentArm(
            arm_id="arm-3", dimensions=_dims(), p1_score=50.0, p1_confidence=0.5,
            component_scores={k: None for k in
                              ("demand", "growth", "competitor_velocity", "competition",
                               "audience_quality", "cross_platform", "production_fit")},
            observation_count=0, source_count=0,
            as_of=datetime(2026, 9, 20),  # noqa: DTZ001 — deliberately naive
            score_version="p1-v1", freshness_seconds=0.0,
        )
