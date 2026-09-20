# tests/unit/market/test_arm_identity.py
from services.market_intelligence.domain.enums import TaxonomyDimension as D
from services.market_intelligence.domain.value_objects import TaxonomyProfile, fingerprint
from services.market_intelligence.taxonomy.arms import arm_id_for, generate_candidate_arms
from services.market_intelligence.taxonomy.validators import ClassificationDraft

RNB = TaxonomyProfile(genre="rnb", use_case="late_night_drive", language="english",
                      vocal_mode="vocal", duration_bucket="45_75m")


def test_arm_id_is_the_spec_shaped_slug_and_is_stable() -> None:
    first = arm_id_for(RNB)
    assert first == "rnb_late_night_drive_english_vocal_45_75m"
    assert arm_id_for(RNB) == first


def test_a_slug_collision_gets_a_deterministic_suffix() -> None:
    assert arm_id_for(RNB, taken=frozenset({"rnb_late_night_drive_english_vocal_45_75m"})) == \
        "rnb_late_night_drive_english_vocal_45_75m_2"


def test_arms_come_from_co_occurring_attributes_with_support() -> None:
    drafts = generate_candidate_arms(
        [
            ClassificationDraft(dimensions={D.GENRE: ("rnb",), D.USE_CASE: ("late_night_drive",)},
                                confidence=0.8),
            ClassificationDraft(dimensions={D.GENRE: ("rnb",), D.USE_CASE: ("late_night_drive",)},
                                confidence=0.7),
            ClassificationDraft(dimensions={D.GENRE: ("lofi",)}, confidence=0.9),
        ],
        min_support=2,
    )
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.support_count == 2
    assert draft.fingerprint == fingerprint(RNB.__class__(genre="rnb", use_case="late_night_drive"))
    assert draft.profile.genre == "rnb" and draft.profile.use_case == "late_night_drive"


def test_generation_is_deterministic_and_refuses_a_zero_support() -> None:
    classifications = [
        ClassificationDraft(dimensions={D.GENRE: ("rnb",)}, confidence=0.5) for _ in range(2)
    ]
    assert generate_candidate_arms(classifications, min_support=2) == \
        generate_candidate_arms(classifications, min_support=2)
    import pytest

    with pytest.raises(ValueError):
        generate_candidate_arms(classifications, min_support=0)
