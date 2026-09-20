"""Defect 1: ids are derived from stable identity, never minted with ``uuid4``.

Resolving the same fixture corpus into two *independently created* databases must
yield identical ids: the determinism constraint demands that a fresh schema plus
the stored inputs reproduces the same values. The older tests only proved that one
database is stable while it lives (a re-select of a random id is still that id).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from plm_platform.storage import StoredObject, sha256_digest
from sqlalchemy.orm import Session

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import (
    ConnectorType,
    EntityType,
    ProviderName,
    ProviderObjectType,
    QueryType,
)
from services.market_intelligence.normalization.canonical import text_fingerprint
from services.market_intelligence.normalization.youtube import normalize_video_object
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.raw_store import store_raw_observation
from tests.support.market_db import create_market_engine

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingObjectStore:
    """Minimal ``put_object`` double: content-addressed, like the real store."""

    def __call__(self, prefix, resource_path, data, *, content_type="application/octet-stream",
                 settings=None, client=None) -> StoredObject:
        digest = sha256_digest(data)
        return StoredObject(
            key=f"{prefix}{resource_path}/{digest}", digest=digest, size=len(data)
        )


def _resolve_corpus(engine: sa.Engine) -> dict[str, str]:
    """Run the committed fixture corpus through the phase's write path once."""
    search_page = _fixture("search_lofi_p1.json")
    video_item = _fixture("videos_lofi_batch.json")["items"][0]
    video_id = video_item["id"]
    title = video_item["snippet"]["title"]

    with Session(engine) as session:
        source_id = repositories.upsert_source(
            session, provider_name=ProviderName.YOUTUBE_DATA_API,
            connector_type=ConnectorType.DATA_API,
            base_identifier="https://www.googleapis.com/youtube/v3",
            configuration_version="p1-sources-v1",
        )
        run_id = repositories.start_collection_run(
            session, source_id=source_id, query_id=None, software_version="p1-v1",
            started_at=FIXED_NOW,
        )
        query_id, _ = repositories.upsert_query(
            session, source_id=source_id, query_text=search_page["items"][0]["snippet"]["title"],
            query_type=QueryType.MUSIC_MOOD, locale="en-US", country_code="US",
            language="en", seed_group="study-session", priority=90,
            query_version="p1-queries-v1",
        )
        fetched = FetchedObject(
            provider_object_type=ProviderObjectType.VIDEO, provider_object_id=video_id,
            observed_at=FIXED_NOW, payload=video_item,
        )
        stored = store_raw_observation(
            session, run_id=run_id, source_id=source_id, fetched=fetched,
            request_context_hash="c" * 64, put_object=RecordingObjectStore(),
        )
        entity_id = repositories.resolve_entity(
            session, entity_type=EntityType.VIDEO, source_id=source_id,
            provider_object_type=ProviderObjectType.VIDEO, provider_object_id=video_id,
            canonical_name=title, normalized_name="2am seoul rainy night r&b mix",
            canonical_url=f"https://www.youtube.com/watch?v={video_id}", observed_at=FIXED_NOW,
        )
        outcome = normalize_video_object(
            fetched, entity_id=entity_id, source_id=source_id,
            raw_observation_id=stored.raw_observation_id, observed_at=FIXED_NOW,
        )
        observation_id, created = repositories.insert_normalized_observation(
            session, outcome.observation
        )
        assert created is True
        assert repositories.insert_seed_evidence(
            session, query_id=query_id, raw_observation_ids=[stored.raw_observation_id]
        ) == 1
        seed_evidence_id = str(session.execute(
            sa.text("SELECT id FROM market.market_seed_evidence")
        ).scalar_one())
        classification_id = repositories.insert_classification(
            session, observation_id=observation_id, entity_id=entity_id,
            content_fingerprint=text_fingerprint(title), taxonomy_version="p1-taxonomy-v1",
            classifier_model="rule_based", classifier_version="p1-v1",
            classification_prompt_version="p1-prompt-v1",
            classification_schema_version="p1-schema-v1", dimensions={"genre": "lo-fi"},
            confidence=0.6, unmapped_terms=[],
        )
        return {
            "source_id": source_id, "run_id": run_id, "query_id": query_id,
            "raw_observation_id": stored.raw_observation_id, "entity_id": entity_id,
            "observation_id": observation_id, "seed_evidence_id": seed_evidence_id,
            "classification_id": classification_id,
        }


def test_the_same_corpus_into_two_fresh_databases_yields_identical_ids() -> None:
    first = _resolve_corpus(create_market_engine())
    second = _resolve_corpus(create_market_engine())
    assert first == second
