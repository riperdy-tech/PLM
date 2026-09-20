# tests/unit/market/test_entity_resolution.py
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.entities import QuarantinedRecord
from services.market_intelligence.domain.enums import (
    ConnectorType,
    EntityType,
    ProviderName,
    ProviderObjectType,
)
from services.market_intelligence.normalization.youtube import normalize_video_object
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import observations

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
# raw_observation_id is a uuid column (FK to market_raw_observations); reading it back
# runs through SQLAlchemy's Uuid type, so placeholders must be uuid-shaped.
RAW_ONE = "00000000-0000-4000-8000-000000000001"
RAW_TWO = "00000000-0000-4000-8000-000000000002"
PAYLOAD = {"kind": "youtube#video", "id": "vid00000001",
           "snippet": {"publishedAt": "2026-08-01T10:00:00Z", "channelId": "UC" + "a" * 22,
                       "title": "2AM Seoul", "description": ""},
           "statistics": {"viewCount": "120000"}}


@pytest.fixture
def source_id(market_session) -> str:
    return repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )


def test_the_same_provider_video_resolves_to_one_entity_across_collections(
    market_session, source_id
) -> None:
    first = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=FIXED_NOW,
    )
    second = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul (remastered)", normalized_name="2am seoul remastered",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=LATER,
    )
    assert first == second
    entity = repositories.load_entity(market_session, first)
    assert entity["last_seen_at"] is not None


def test_rerunning_the_same_query_on_a_later_date_adds_a_second_observation(
    market_session, source_id
) -> None:
    entity_id = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=FIXED_NOW,
    )
    fetched = FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                            provider_object_id="vid00000001", observed_at=FIXED_NOW,
                            payload=PAYLOAD)
    first = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                   raw_observation_id=RAW_ONE, observed_at=FIXED_NOW)
    second = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                    raw_observation_id=RAW_TWO, observed_at=LATER)
    first_id, first_created = repositories.insert_normalized_observation(
        market_session, first.observation)
    second_id, second_created = repositories.insert_normalized_observation(
        market_session, second.observation)
    assert (first_created, second_created) == (True, True)
    assert first_id != second_id
    observations = repositories.load_observations(market_session, entity_id=entity_id)
    assert len(observations) == 2  # AC-03: history is added to, never overwritten
    assert observations[0].views == observations[1].views  # the provider value repeated


def test_inserting_the_same_observation_point_twice_is_idempotent(
    market_session, source_id
) -> None:
    entity_id = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=FIXED_NOW,
    )
    fetched = FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                            provider_object_id="vid00000001", observed_at=FIXED_NOW,
                            payload=PAYLOAD)
    draft = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                   raw_observation_id=RAW_ONE, observed_at=FIXED_NOW)
    first_id, first_created = repositories.insert_normalized_observation(
        market_session, draft.observation)
    again_id, again_created = repositories.insert_normalized_observation(
        market_session, draft.observation)
    assert (first_created, again_created) == (True, False)
    assert first_id == again_id


def test_an_observation_records_the_normalization_version_that_derived_it(
    market_session, source_id
) -> None:
    entity_id = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=FIXED_NOW,
    )
    fetched = FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                            provider_object_id="vid00000001", observed_at=FIXED_NOW,
                            payload=PAYLOAD)
    first = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                   raw_observation_id=RAW_ONE, observed_at=FIXED_NOW)
    first_id, _ = repositories.insert_normalized_observation(market_session, first.observation)
    second = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                    raw_observation_id=RAW_TWO, observed_at=LATER)
    second_id, _ = repositories.insert_normalized_observation(
        market_session, second.observation, normalization_version="p1-norm-v9")

    def _version(observation_id: str) -> str:
        return market_session.execute(
            sa.select(observations.c.normalization_version)
            .where(observations.c.id == observation_id)
        ).scalar_one()

    assert _version(first_id) == "p1-norm-v1"  # the phase's default transformation
    assert _version(second_id) == "p1-norm-v9"  # callers may stamp their own


def test_quarantine_records_are_written_not_dropped(market_session, source_id) -> None:
    record_id = repositories.quarantine_record(market_session, QuarantinedRecord(
        reason_code="negative_count", detail="views=-5", payload_uri="plm://bucket/key",
    ))
    assert record_id
    assert repositories.count_quarantined(market_session, run_id=None) == 1
    with pytest.raises(ValueError, match="unknown quarantine reason code"):
        repositories.quarantine_record(market_session, QuarantinedRecord(
            reason_code="just_felt_wrong", detail="no reason code",
        ))
