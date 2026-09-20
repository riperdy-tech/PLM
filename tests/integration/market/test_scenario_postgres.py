# tests/integration/market/test_scenario_postgres.py
"""§30/AC-08 and §35 through PostgreSQL — the real store, not a SQLite stand-in.

The unit suite asserts determinism over in-memory objects; the storage suite exercises
SQLite. Neither proves the store the pipeline actually writes. This module stores
§35's whole scenario through the real repositories, **reads it back through the real
read path** and re-scores it:

* two independent read-backs produce byte-identical scores (§30/AC-08);
* the read-back preserves the in-memory values (storage adds no drift);
* §35's three-channel ranking holds on the stored data;
* a persisted score reads back with the same value it was computed with.

Raw payload artifacts are not materialised here (that path belongs to
`test_raw_store_minio.py` and the P1.14 acceptance run); this fixture isolates the
observation → feature → score round-trip.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

import pytest
import sqlalchemy as sa
from plm_platform.db import build_engine, scoped_session
from sqlalchemy.orm import sessionmaker

from services.market_intelligence.config import MarketSettings, load_market_settings
from services.market_intelligence.domain.entities import OpportunityScore
from services.market_intelligence.domain.enums import (
    ConnectorType,
    EntityType,
    ProviderName,
    ProviderObjectType,
)
from services.market_intelligence.domain.value_objects import TaxonomyProfile
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.normalization.canonical import normalize_text, text_fingerprint
from services.market_intelligence.scoring.opportunity import persist_scores, score_all_arms
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import raw_observations
from services.market_intelligence.storage.raw_store import SCHEMA_VERSION
from services.market_intelligence.taxonomy.arms import ArmDraft
from tests.fixtures.market.scenario import (
    MID_ARM_ID,
    PRIMARY_ARM_ID,
    SECONDARY_ARM_ID,
    MarketScenario,
    build_scenario,
)

pytestmark = pytest.mark.integration

ARMS = (PRIMARY_ARM_ID, SECONDARY_ARM_ID, MID_ARM_ID)


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    try:
        resolved = build_engine()
        with resolved.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"postgres unreachable ({type(exc).__name__}); "
                    "run: docker compose up -d --wait")
    yield resolved
    resolved.dispose()


def _serialised(score: OpportunityScore) -> dict[str, Any]:
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
        "score_version": score.score_version,
        "feature_version": score.feature_version,
        "normalization_version": score.normalization_version,
    }


def _store_scenario(engine: sa.Engine, settings: MarketSettings) -> dict[str, list[str]]:
    """Store §35's observations through the repositories; return entity ids per arm."""
    scenario = build_scenario()
    entity_ids: dict[str, list[str]] = {arm: [] for arm in ARMS}
    with scoped_session("market", factory=sessionmaker(bind=engine)) as session:
        source_id = repositories.upsert_source(
            session, provider_name=ProviderName.YOUTUBE_DATA_API,
            connector_type=ConnectorType.DATA_API, base_identifier="x",
            configuration_version="p1-sources-v1",
        )
        run_id = repositories.start_collection_run(
            session, source_id=source_id, query_id=None, software_version=settings.score_version,
            started_at=scenario.now,
        )
        for arm, observations in scenario.observations_by_arm.items():
            for index, observation in enumerate(observations):
                entity_id = repositories.resolve_entity(
                    session, entity_type=EntityType.VIDEO, source_id=source_id,
                    provider_object_type=ProviderObjectType.VIDEO,
                    provider_object_id=observation.entity_id,
                    canonical_name=f"scenario {observation.entity_id}",
                    normalized_name=normalize_text(observation.entity_id),
                    canonical_url=None, observed_at=observation.observed_at,
                )
                raw_id = repositories.deterministic_id(
                    "raw", observation.entity_id, str(index), observation.observed_at.isoformat())
                payload = {"views": observation.views.unwrap() if observation.views.is_known
                           else None}
                try:
                    with session.begin_nested():
                        session.execute(sa.insert(raw_observations).values(
                            id=raw_id, collection_run_id=run_id, source_id=source_id,
                            provider_object_type=ProviderObjectType.VIDEO.value,
                            provider_object_id=observation.entity_id,
                            observed_at=observation.observed_at,
                            request_context_hash=text_fingerprint(
                                f"scenario|{observation.entity_id}|{index}"),
                            payload_json=payload,
                            payload_hash=text_fingerprint(json.dumps(payload, sort_keys=True)),
                            payload_uri=f"plm://plm-media/market/scenario/{raw_id}",
                            schema_version=SCHEMA_VERSION,
                        ))
                except sa.exc.IntegrityError:
                    pass  # a replay stores the identical raw row; the idempotent case
                observation_id, _created = repositories.insert_normalized_observation(
                    session, dataclasses.replace(observation, entity_id=entity_id,
                                                 source_id=source_id,
                                                 raw_observation_id=raw_id))
                assert observation_id
                entity_ids[arm].append(entity_id)
        session.commit()
    return entity_ids


def _read_back_scores(engine: sa.Engine, entity_ids: Mapping[str, list[str]],
                      settings: MarketSettings) -> dict[str, OpportunityScore]:
    """One read-back: stored observations → features → scores (the real read path)."""
    scenario = build_scenario()
    with scoped_session("market", factory=sessionmaker(bind=engine)) as session:
        grouped = {arm: tuple(repositories.observations_for_entities(session, ids))
                   for arm, ids in entity_ids.items()}
    features = compute_arm_features(
        grouped, as_of=scenario.now, settings=settings, arm_ids={arm: arm for arm in ARMS},
    )
    return score_all_arms(features, as_of=scenario.now, settings=settings)


def _persist_arms_and_scores(engine: sa.Engine, scores: Mapping[str, OpportunityScore],
                             scenario: MarketScenario, settings: MarketSettings) -> None:
    """The write leg a replay exercises twice: arms, then scores, at the fixed instant."""
    with scoped_session("market", factory=sessionmaker(bind=engine)) as session:
        for arm in ARMS:
            repositories.upsert_arm(
                session,
                ArmDraft(arm_id=arm, fingerprint=f"scenario-{arm}",
                         profile=TaxonomyProfile(genre=arm.split("_")[0]),
                         source_observation_ids=scores[arm].evidence.observation_ids,
                         support_count=scores[arm].evidence.observation_count),
                brand_id=settings.default_brand_id,
            )
        session.flush()
        persist_scores(session, scores, computed_at=scenario.now)
        session.commit()


def test_the_stored_scenario_replays_into_identical_scores_on_postgres(
        engine: sa.Engine) -> None:
    """§30/AC-08 on the real store: same stored rows + same versions ⇒ same scores."""
    settings = load_market_settings()
    entity_ids = _store_scenario(engine, settings)
    first = _read_back_scores(engine, entity_ids, settings)
    second = _read_back_scores(engine, entity_ids, settings)
    assert set(first) == set(ARMS), "every §35 arm must score from the stored rows"
    for arm in ARMS:
        assert _serialised(first[arm]) == _serialised(second[arm]), arm


def test_storage_round_trip_preserves_the_in_memory_values(engine: sa.Engine) -> None:
    settings = load_market_settings()
    scenario = build_scenario()
    entity_ids = _store_scenario(engine, settings)
    memory = score_all_arms(
        compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                             settings=settings),
        as_of=scenario.now, settings=settings,
    )
    stored = _read_back_scores(engine, entity_ids, settings)
    for arm in ARMS:
        assert stored[arm].market_opportunity_score == memory[arm].market_opportunity_score, arm
        assert stored[arm].confidence == memory[arm].confidence, arm
        assert stored[arm].basis_weight == memory[arm].basis_weight, arm
        assert ({component.value: str(value)
                 for component, value in stored[arm].components.as_mapping().items()}
                == {component.value: str(value)
                    for component, value in memory[arm].components.as_mapping().items()}), arm


def test_section_35_ranking_holds_on_the_stored_data(engine: sa.Engine) -> None:
    """§35 in the integration suite: raw popularity must not decide the ranking."""
    settings = load_market_settings()
    entity_ids = _store_scenario(engine, settings)
    scores = _read_back_scores(engine, entity_ids, settings)
    fast = scores[PRIMARY_ARM_ID].market_opportunity_score
    mega = scores[SECONDARY_ARM_ID].market_opportunity_score
    mid = scores[MID_ARM_ID].market_opportunity_score
    assert fast >= mega, "the fast small arm must not lose to the 10M-subscriber arm"
    assert mega < max(score.market_opportunity_score for score in scores.values()), (
        "the mega channel must not be the top-ranked arm by construction (§35)"
    )
    assert mid >= mega, "the mid-sized growing arm must not lose to the mega channel (§35)"


def test_a_persisted_score_reads_back_with_the_value_it_was_computed_with(
        engine: sa.Engine) -> None:
    """AC-08's persistence leg: the stored number is the computed number, exactly."""
    settings = load_market_settings()
    scenario = build_scenario()
    entity_ids = _store_scenario(engine, settings)
    scores = _read_back_scores(engine, entity_ids, settings)
    _persist_arms_and_scores(engine, scores, scenario, settings)
    with scoped_session("market", factory=sessionmaker(bind=engine)) as session:
        for arm in ARMS:
            rows = repositories.scores_for_arm(session, arm)
            assert rows, arm
            assert float(rows[0]["market_opportunity_score"]) == \
                scores[arm].market_opportunity_score, arm
            assert float(rows[0]["confidence"]) == scores[arm].confidence, arm


def test_a_replayed_scenario_is_idempotent_against_one_database(engine: sa.Engine) -> None:
    """The same scenario, twice, against one database.

    The suite replays its fixed instants — the write leg previously raised
    `UniqueViolation` on `market_arms.arm_id` (the deterministic ids resolve a replay
    to the same arm), so a second run of the same corpus could not run at all. It now
    succeeds and leaves exactly one row per arm; scores follow the sibling test's
    read-back rule.
    """
    settings = load_market_settings()
    scenario = build_scenario()
    entity_ids = _store_scenario(engine, settings)
    scores = _read_back_scores(engine, entity_ids, settings)
    _persist_arms_and_scores(engine, scores, scenario, settings)
    _persist_arms_and_scores(engine, scores, scenario, settings)  # the replay
    with scoped_session("market", factory=sessionmaker(bind=engine)) as session:
        stored = {row["arm_id"] for row in repositories.load_arms(session)
                  if row["arm_id"] in ARMS}
    assert stored == set(ARMS)  # one row per arm: the replay neither raised nor doubled
