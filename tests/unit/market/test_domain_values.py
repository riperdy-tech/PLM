# tests/unit/market/test_domain_values.py
import pytest
from plm_contracts import Tri, TriState, TriStateError

from services.market_intelligence.domain.enums import ScoringComponent, TaxonomyDimension
from services.market_intelligence.domain.value_objects import (
    EvidenceSummary, ScoreComponents, TaxonomyProfile, fingerprint, slug_token,
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
        EvidenceSummary(observation_count=-1, source_count=1, top_observation_ids=())
