# tests/unit/market/test_reproducibility.py
"""§30/AC-08: same observations + same versions ⇒ identical scores.

The integration suite repeats this through PostgreSQL (`test_scenario_postgres.py`):
proving reproducibility only on SQLite would not prove it on the real store.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation, OpportunityScore
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.opportunity import score_all_arms
from tests.fixtures.market.scenario import build_scenario

SETTINGS = load_market_settings()


def _serialised(score: OpportunityScore) -> dict[str, Any]:
    """A JSON-safe view of the whole score object (versions and evidence included)."""
    return {
        "arm_id": score.arm_id,
        "market_opportunity_score": score.market_opportunity_score,
        "confidence": score.confidence,
        "basis_weight": score.basis_weight,
        "components": {component.value: str(value)
                       for component, value in score.components.as_mapping().items()},
        "evidence": {"observation_count": score.evidence.observation_count,
                     "source_count": score.evidence.source_count,
                     "observation_ids": list(score.evidence.observation_ids)},
        "as_of": score.as_of.isoformat(),
        "score_version": score.score_version,
        "feature_version": score.feature_version,
        "normalization_version": score.normalization_version,
    }


def _scores(observations_by_arm: Mapping[str, tuple[NormalizedObservation, ...]],
            as_of: datetime, settings=SETTINGS) -> dict[str, dict[str, Any]]:
    features = compute_arm_features(observations_by_arm, as_of=as_of, settings=settings)
    return {arm: _serialised(score)
            for arm, score in score_all_arms(features, as_of=as_of, settings=settings).items()}


def _dumped(scores: dict[str, dict[str, Any]]) -> str:
    return json.dumps(scores, sort_keys=True, default=str)


def test_replaying_the_same_observations_produces_identical_scores() -> None:
    scenario = build_scenario()
    first = _scores(scenario.observations_by_arm, scenario.now)
    second = _scores(scenario.observations_by_arm, scenario.now)
    assert _dumped(first) == _dumped(second)


def test_a_new_score_version_label_changes_the_version_not_the_arithmetic() -> None:
    scenario = build_scenario()
    from services.market_intelligence.config import load_market_settings as load

    bumped = load(score_version="p1-v2")
    bumped_scores = _scores(scenario.observations_by_arm, scenario.now, settings=bumped)
    assert bumped_scores, "the scenario must produce scores"
    assert all(score["score_version"] == "p1-v2" for score in bumped_scores.values())
    baseline = _scores(scenario.observations_by_arm, scenario.now)
    assert _dumped(bumped_scores) != _dumped(baseline), "the version is part of the replay key"
    for arm, score in bumped_scores.items():
        assert score["market_opportunity_score"] == baseline[arm]["market_opportunity_score"]


def test_the_evidence_set_is_the_full_input_not_a_truncated_top_five() -> None:
    """§30: a stored score whose evidence stopped at five could not be recomputed."""
    scenario = build_scenario()
    scores = _scores(scenario.observations_by_arm, scenario.now)
    for arm, score in scores.items():
        assert score["evidence"]["observation_count"] == len(scenario.observations_by_arm[arm])
