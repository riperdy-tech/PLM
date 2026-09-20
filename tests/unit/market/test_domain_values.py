# tests/unit/market/test_domain_values.py
from datetime import UTC, datetime

import pytest
from plm_contracts import Tri, TriState, TriStateError

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation, OpportunityScore
from services.market_intelligence.domain.enums import ScoringComponent, TaxonomyDimension
from services.market_intelligence.domain.value_objects import (
    EvidenceSummary,
    ScoreComponents,
    TaxonomyProfile,
    fingerprint,
    slug_token,
    tri_from_optional,
)


def test_fingerprint_is_dimension_ordered_and_stable() -> None:
    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive", mood=("melancholic",))
    same = TaxonomyProfile(mood=("melancholic",), use_case="late_night_drive", genre="rnb")
    assert fingerprint(profile) == fingerprint(same)
    assert fingerprint(profile).startswith("genre=rnb|")
    assert "|vocal_gender=none|" in fingerprint(profile)


def test_fingerprint_covers_every_dimension() -> None:
    parts = fingerprint(TaxonomyProfile()).split("|")
    assert len(parts) == len(TaxonomyDimension)
    assert [p.split("=", 1)[0] for p in parts] == [d.value for d in TaxonomyDimension]


def test_slug_token_is_bounded_and_safe() -> None:
    assert slug_token("Late Night Drive!!") == "late_night_drive"
    assert slug_token("rnb  x") == "rnb_x"
    assert len(slug_token("x" * 200)) == 48


def test_tri_from_optional_keeps_missing_data_out_of_arithmetic() -> None:
    assert tri_from_optional(None).state is TriState.UNKNOWN
    assert tri_from_optional(0) == Tri.known(0)
    assert tri_from_optional(7).unwrap() == 7
    with pytest.raises(TriStateError):
        tri_from_optional(None).unwrap()


def test_score_components_reject_out_of_range_values() -> None:
    with pytest.raises(ValueError):
        ScoreComponents.from_mapping({c: 50.0 for c in ScoringComponent} | {"demand": 101.0})
    ok = ScoreComponents.from_mapping({c.value: 50.0 for c in ScoringComponent})
    assert ok.as_mapping()[ScoringComponent.COMPETITION] == 50.0


def test_evidence_summary_counts_are_non_negative_and_top_ids_are_strings() -> None:
    with pytest.raises(ValueError):
        EvidenceSummary(observation_count=-1, source_count=1, observation_ids=())
    summary = EvidenceSummary(observation_count=7, source_count=1,
                              observation_ids=tuple(f"r{index}" for index in range(7)))
    assert summary.top_observation_ids == ("r0", "r1", "r2", "r3", "r4")


def test_normalized_observation_keeps_unknown_metrics_unknown() -> None:
    observation = NormalizedObservation(
        entity_id="ent-1", source_id="src-1", raw_observation_id="raw-1",
        observed_at=datetime(2026, 9, 20, tzinfo=UTC),
        views=tri_from_optional(None), likes=tri_from_optional(0),
        comments=tri_from_optional(None), subscriber_count=tri_from_optional(None),
    )
    assert observation.views.is_unknown
    assert observation.likes == Tri.known(0)
    assert observation.comments.is_unknown


def test_opportunity_score_enforces_bounds() -> None:
    with pytest.raises(ValueError, match="market_opportunity_score"):
        OpportunityScore(
            arm_id="a", market_opportunity_score=101.0, confidence=0.5,
            components=ScoreComponents.from_mapping({c.value: 50.0 for c in ScoringComponent}),
            evidence=EvidenceSummary(1, 1, ()), as_of=datetime(2026, 9, 20, tzinfo=UTC),
            score_version="p1-v1", feature_version="p1-features-v1",
            normalization_version="p1-norm-v1",
        )


def test_market_settings_defaults_are_versioned_and_config_driven() -> None:
    settings = load_market_settings(platform=None, score_version="p1-v2")
    assert settings.score_version == "p1-v2"
    assert settings.default_brand_id == "neon_seoul"
    assert settings.freshness_fresh_hours == 48.0
    assert settings.quota_units_per_run > 0
