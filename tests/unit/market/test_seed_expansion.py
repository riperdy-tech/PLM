from services.market_intelligence.collection.seed_expansion import (
    SeedProposal,
    propose_from_observations,
)
from services.market_intelligence.collection.seeds import validate_candidate_seed
from services.market_intelligence.domain.enums import ConnectorType, ProviderName, QueryType
from services.market_intelligence.normalization.canonical import text_fingerprint
from services.market_intelligence.storage import repositories

# `market_seed_evidence.raw_observation_id` is a Uuid column: it must round-trip a
# real uuid string (the bind/result processors strip and re-hyphenate).
RAW_OBSERVATION_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"

PROPOSALS = (
    SeedProposal(term="late night drive", query_type=QueryType.USE_CASE,
                 source_observation_ids=(RAW_OBSERVATION_ID,), proposed_by_model="rule_based_v1",
                 model_name="rule-based", model_version="1", confidence=0.8),
)


def test_validation_accepts_a_good_term_and_rejects_with_reasons() -> None:
    good = PROPOSALS[0]
    assert validate_candidate_seed(
        good, existing_fingerprints=frozenset(), min_confidence=0.5
    ).accepted
    duplicate = validate_candidate_seed(
        good, existing_fingerprints=frozenset({text_fingerprint("late night drive")}),
        min_confidence=0.5,
    )
    assert duplicate.accepted is False and "duplicate" in (duplicate.reason or "")
    low = validate_candidate_seed(good, existing_fingerprints=frozenset(), min_confidence=0.9)
    assert low.accepted is False and "confidence" in (low.reason or "")


def test_expansion_persists_lineage_for_accepted_terms(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    parent_id, _ = repositories.upsert_query(
        market_session, source_id=source_id, query_text="lofi", query_type=QueryType.MUSIC_GENRE,
        locale="en", country_code="US", language="english", seed_group="bootstrap",
        priority=50, query_version="p1-queries-v1",
    )
    decisions = propose_from_observations(
        market_session, source_id=source_id, proposer=lambda texts: PROPOSALS,
        titles=("2AM Seoul — Rainy Night R&B Mix",), descriptions=("Late-night city vibes",),
        parent_query_id=parent_id, settings=None,
    )
    assert [decision.accepted for decision in decisions] == [True]
    stored = repositories.load_queries(market_session, source_id=source_id, active_only=False)
    created = [row for row in stored if row["query_text"] == "late night drive"]
    assert len(created) == 1
    assert created[0]["parent_query_id"] == parent_id
    assert created[0]["proposed_by_model"] == "rule_based_v1"
    assert created[0]["model_version"] == "1"
    assert repositories.load_seed_evidence(market_session, str(created[0]["id"])) == [
        RAW_OBSERVATION_ID
    ]


def test_the_expansion_cap_is_enforced_with_a_reason(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    proposals = tuple(
        SeedProposal(term=f"term {index}", query_type=QueryType.LONG_TAIL, confidence=0.9)
        for index in range(3)
    )
    decisions = propose_from_observations(
        market_session, source_id=source_id, proposer=lambda texts: proposals,
        titles=(), descriptions=(), parent_query_id=None, settings=None, max_new_seeds=1,
    )
    assert [d.accepted for d in decisions] == [True, False, False]
    assert "cap" in (decisions[1].reason or "")
