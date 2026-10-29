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


def test_v2_measured_duration_arm_cannot_rewrite_same_shaped_v1_arm(
    market_session,
) -> None:
    from services.market_intelligence.storage import repositories

    source = [
        ClassificationDraft(
            dimensions={D.GENRE: ("rnb",), D.DURATION_BUCKET: ("45_75m",)},
            confidence=0.8,
        ),
        ClassificationDraft(
            dimensions={D.GENRE: ("rnb",), D.DURATION_BUCKET: ("45_75m",)},
            confidence=0.7,
        ),
    ]
    old = generate_candidate_arms(source, min_support=2)[0]
    current = generate_candidate_arms(
        source, min_support=2, taxonomy_version="p1-taxonomy-v2",
    )[0]
    assert old.profile == current.profile
    assert old.fingerprint != current.fingerprint
    assert old.arm_id != current.arm_id
    assert current.arm_id.startswith("p1_taxonomy_v2_")
    old_id, old_insert = repositories.upsert_arm(
        market_session, old, brand_id="neon_seoul",
    )
    new_id, new_insert = repositories.upsert_arm(
        market_session, current, brand_id="neon_seoul",
    )
    assert old_insert and new_insert
    assert old_id != new_id
    stored = {row["arm_id"]: row for row in repositories.load_arms(market_session)}
    assert stored[old_id]["fingerprint"] == old.fingerprint
    assert stored[new_id]["fingerprint"] == current.fingerprint
    assert stored[old_id]["support_count"] == 2


def test_versioned_arm_slug_collision_refuses_misattributed_score_evidence(
    market_session,
) -> None:
    import pytest

    from services.market_intelligence.storage import repositories

    # The human-readable slug omits moods and other identity dimensions.
    # Two different full taxonomy profiles therefore share one slug when
    # materialized in separate daily runs, even without truncation.
    calm = generate_candidate_arms(
        [ClassificationDraft(
            dimensions={
                D.GENRE: ("lofi",), D.MOOD: ("calm",),
                D.DURATION_BUCKET: ("45_75m",),
            },
            confidence=0.9,
        )], min_support=1, taxonomy_version="p1-taxonomy-v2",
    )[0]
    dreamy = generate_candidate_arms(
        [ClassificationDraft(
            dimensions={
                D.GENRE: ("lofi",), D.MOOD: ("dreamy",),
                D.DURATION_BUCKET: ("45_75m",),
            },
            confidence=0.9,
        )], min_support=1, taxonomy_version="p1-taxonomy-v2",
    )[0]
    assert calm.arm_id == dreamy.arm_id
    assert calm.fingerprint != dreamy.fingerprint
    original, created = repositories.upsert_arm(
        market_session, calm, brand_id="neon_seoul", refuse_slug_collision=True,
    )
    assert created
    replay, created = repositories.upsert_arm(
        market_session, calm, brand_id="neon_seoul", refuse_slug_collision=True,
    )
    assert (replay, created) == (original, False)
    with pytest.raises(ValueError, match="arm_slug_identity_conflict"):
        repositories.upsert_arm(
            market_session, dreamy, brand_id="neon_seoul",
            refuse_slug_collision=True,
        )
    stored = repositories.load_arms(market_session)
    assert len(stored) == 1
    assert stored[0]["fingerprint"] == calm.fingerprint
    assert stored[0]["taxonomy_json"]["mood"] == "calm"


def test_generation_is_deterministic_and_refuses_a_zero_support() -> None:
    classifications = [
        ClassificationDraft(dimensions={D.GENRE: ("rnb",)}, confidence=0.5) for _ in range(2)
    ]
    assert generate_candidate_arms(classifications, min_support=2) == \
        generate_candidate_arms(classifications, min_support=2)
    import pytest

    with pytest.raises(ValueError):
        generate_candidate_arms(classifications, min_support=0)


def test_v3_opt_in_identity_is_stable_across_batch_growth_and_slug_collisions(
    market_session,
) -> None:
    import hashlib

    from services.market_intelligence.storage import repositories

    calm = ClassificationDraft(
        dimensions={
            D.GENRE: ("lofi",), D.MOOD: ("calm",),
            D.DURATION_BUCKET: ("45_75m",),
        }, confidence=0.9,
    )
    dreamy = ClassificationDraft(
        dimensions={
            D.GENRE: ("lofi",), D.MOOD: ("dreamy",),
            D.DURATION_BUCKET: ("45_75m",),
        }, confidence=0.8,
    )
    one = generate_candidate_arms(
        [calm], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )[0]
    other = generate_candidate_arms(
        [dreamy], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )[0]
    together = generate_candidate_arms(
        [dreamy, calm], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )
    reversed_batch = generate_candidate_arms(
        [calm, dreamy], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )
    assert one.arm_id != other.arm_id
    assert {a.arm_id for a in together} == {one.arm_id, other.arm_id}
    assert together == reversed_batch
    assert one.arm_id.startswith("p1_taxonomy_v3_lofi_45_")
    assert one.arm_id.endswith(
        hashlib.sha256(one.fingerprint.encode("utf-8")).hexdigest()[:40]
    )
    assert len(one.arm_id) <= 64 and len(other.arm_id) <= 64
    for draft in (one, other):
        identity, inserted = repositories.upsert_arm(
            market_session, draft, brand_id="neon_seoul", refuse_slug_collision=True,
        )
        assert inserted and identity == draft.arm_id
    replay, inserted = repositories.upsert_arm(
        market_session, one, brand_id="neon_seoul", refuse_slug_collision=True,
    )
    assert replay == one.arm_id and not inserted
    records = {record["arm_id"]: record for record in repositories.load_arms(market_session)}
    assert len(records) == 2
    assert records[one.arm_id]["taxonomy_json"]["mood"] == "calm"
    assert records[other.arm_id]["taxonomy_json"]["mood"] == "dreamy"


def test_v3_opt_in_does_not_change_existing_v2_arm_identity() -> None:
    draft = ClassificationDraft(
        dimensions={D.GENRE: ("rnb",), D.DURATION_BUCKET: ("45_75m",)},
        confidence=0.8,
    )
    v2 = generate_candidate_arms(
        [draft], min_support=1, taxonomy_version="p1-taxonomy-v2",
    )[0]
    v3 = generate_candidate_arms(
        [draft], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )[0]
    assert v2.arm_id == "p1_taxonomy_v2_rnb_45_75m"
    assert v3.arm_id.startswith("p1_taxonomy_v3_rnb_45_")
    assert len(v3.arm_id) <= 64
    assert v2.arm_id != v3.arm_id and v2.fingerprint != v3.fingerprint
