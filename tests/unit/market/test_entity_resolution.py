# tests/unit/market/test_entity_resolution.py
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from plm_contracts import Tri, TriState

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.entities import QuarantinedRecord
from services.market_intelligence.domain.enums import (
    ConnectorType,
    EntityType,
    ProviderName,
    ProviderObjectType,
)
from services.market_intelligence.normalization.youtube import (
    normalize_search_hit,
    normalize_video_object,
)
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import entity_identities, observations

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
NEWEST = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
# raw_observation_id is a uuid column (FK to market_raw_observations); reading it back
# runs through SQLAlchemy's Uuid type, so placeholders must be uuid-shaped.
RAW_ONE = "00000000-0000-4000-8000-000000000001"
RAW_TWO = "00000000-0000-4000-8000-000000000002"
PAYLOAD = {"kind": "youtube#video", "id": "vid00000001",
           "snippet": {"publishedAt": "2026-08-01T10:00:00Z", "channelId": "UC" + "a" * 22,
                       "title": "2AM Seoul", "description": ""},
           "statistics": {"viewCount": "120000"}}
# The discovery shape: text and a publish date, no metrics — `normalize_search_hit`'s input.
SEARCH_PAYLOAD = {"kind": "youtube#searchResult",
                  "id": {"kind": "youtube#video", "videoId": "vid00000001"},
                  "snippet": {"publishedAt": "2026-08-01T10:00:00Z",
                              "channelId": "UC" + "a" * 22, "title": "2AM Seoul",
                              "description": ""}}


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


def test_a_backfill_cannot_regress_the_entity_name_or_last_seen(
    market_session, source_id
) -> None:
    entity_id = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul (remastered)", normalized_name="2am seoul remastered",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=LATER,
    )
    before = repositories.load_entity(market_session, entity_id)

    def _identity_last_seen() -> object:
        return market_session.execute(
            sa.select(entity_identities.c.last_seen_at).where(
                entity_identities.c.entity_id == entity_id)
        ).scalar_one()

    identity_before = _identity_last_seen()

    same = repositories.resolve_entity(  # a backfill sighting, older than the last one
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=FIXED_NOW,
    )
    assert same == entity_id
    after = repositories.load_entity(market_session, entity_id)
    assert after["canonical_name"] == "2AM Seoul (remastered)"
    assert after["normalized_name"] == "2am seoul remastered"
    assert after["last_seen_at"] == before["last_seen_at"]  # monotonic, not regressed
    assert _identity_last_seen() == identity_before

    repositories.resolve_entity(  # a genuinely newer sighting still moves the state
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul (2M remaster)", normalized_name="2am seoul 2m remaster",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=NEWEST,
    )
    final = repositories.load_entity(market_session, entity_id)
    assert final["canonical_name"] == "2AM Seoul (2M remaster)"


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


def test_hydration_enriches_the_discovery_sighting_instead_of_being_dropped(
    market_session, source_id
) -> None:
    """Discovery stores text only; hydration carries the metrics for the same
    (entity, source, instant). The idempotent insert used to discard the richer
    draft silently — every metric stayed unknown and nothing downstream could
    score — so the second write now fills what the first lacked: one row, the
    union of the payloads, no order dependence, never a second sighting."""
    entity_id = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid00000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid00000001", observed_at=FIXED_NOW,
    )
    discovery = normalize_search_hit(
        FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                      provider_object_id="vid00000001", observed_at=FIXED_NOW,
                      payload=SEARCH_PAYLOAD),
        entity_id=entity_id, source_id=source_id, raw_observation_id=RAW_ONE,
        observed_at=FIXED_NOW,
    )
    hydration = normalize_video_object(
        FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                      provider_object_id="vid00000001", observed_at=FIXED_NOW,
                      payload=PAYLOAD),
        entity_id=entity_id, source_id=source_id, raw_observation_id=RAW_TWO,
        observed_at=FIXED_NOW,
    )
    first_id, first_created = repositories.insert_normalized_observation(
        market_session, discovery.observation)
    second_id, second_created = repositories.insert_normalized_observation(
        market_session, hydration.observation)
    assert (first_created, second_created) == (True, False)
    assert first_id == second_id
    stored = repositories.load_observations(market_session, entity_id=entity_id)
    assert len(stored) == 1  # one sighting, enriched — not a second row
    assert stored[0].views.is_known and stored[0].views.unwrap() == 120000
    assert stored[0].video_duration_seconds.is_unknown  # absence stays unknown
    assert stored[0].raw_observation_id == RAW_TWO  # the payload the metrics came from
    # a plain replay of the discovery payload adds nothing back
    assert repositories.insert_normalized_observation(
        market_session, discovery.observation) == (first_id, False)
    assert repositories.load_observations(
        market_session, entity_id=entity_id)[0].views.is_known


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


def test_not_ready_metrics_survive_the_database_round_trip(market_session, source_id) -> None:
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
    # the payload has viewCount; likes/comments are absent (unknown); subscriber_count
    # is expected later — a window that has not completed is not_ready, never unknown
    pending = replace(draft.observation, likes=Tri.not_ready())
    observation_id, _ = repositories.insert_normalized_observation(market_session, pending)
    loaded = repositories.load_observations(market_session, entity_id=entity_id)
    assert len(loaded) == 1
    assert loaded[0].views.state is TriState.KNOWN
    assert loaded[0].likes.state is TriState.NOT_READY
    assert loaded[0].comments.state is TriState.UNKNOWN
    assert str(market_session.execute(
        sa.select(observations.c.id).where(observations.c.id == observation_id)
    ).scalar_one())


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
