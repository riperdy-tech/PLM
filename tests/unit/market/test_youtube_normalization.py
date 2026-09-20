# tests/unit/market/test_youtube_normalization.py
import json
from datetime import UTC, datetime

from plm_contracts import TriState

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.normalization.youtube import (
    normalize_channel_object,
    normalize_video_object,
)
from tests.support.golden import assert_golden, golden_path

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
VIDEO_PAYLOAD = {
    "kind": "youtube#video", "id": "vid00000001",
    "snippet": {"publishedAt": "2026-08-01T10:00:00Z", "channelId": "UC" + "a" * 22,
                "title": "2AM Seoul — Rainy Night R&B Mix",
                "description": "Late-night Korean city vibes",
                "defaultLanguage": "en"},
    "statistics": {"viewCount": "120000", "likeCount": "8400", "commentCount": "410"},
    "contentDetails": {"duration": "PT1H2M"},
}
CHANNEL_PAYLOAD = {
    "kind": "youtube#channel", "id": "UC" + "b" * 22,
    "snippet": {"title": "StudyLoops"},
    "statistics": {"videoCount": "88"},  # subscriberCount is absent, not zero
}


def _video():
    return FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                         provider_object_id="vid00000001", observed_at=FIXED_NOW,
                         payload=VIDEO_PAYLOAD)


def test_video_payload_maps_to_metric_values_and_a_retained_title() -> None:
    outcome = normalize_video_object(
        _video(), entity_id="entity-1", source_id="source-1",
        raw_observation_id="raw-1", observed_at=FIXED_NOW,
    )
    assert outcome.quarantine is None
    draft = outcome.observation
    assert draft.views.unwrap() == 120000
    assert draft.comments.unwrap() == 410
    assert draft.video_duration_seconds.unwrap() == 3720.0
    assert draft.text_title == "2AM Seoul — Rainy Night R&B Mix"
    assert draft.language_signal == "en"
    assert draft.published_at == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def test_hidden_subscriber_count_stays_unknown() -> None:
    outcome = normalize_channel_object(
        FetchedObject(provider_object_type=ProviderObjectType.CHANNEL,
                      provider_object_id="UC" + "b" * 22, observed_at=FIXED_NOW,
                      payload=CHANNEL_PAYLOAD),
        entity_id="entity-2", source_id="source-1", raw_observation_id="raw-2",
        observed_at=FIXED_NOW,
    )
    draft = outcome.observation
    assert draft.subscriber_count.state is TriState.UNKNOWN
    assert draft.channel_video_count.unwrap() == 88
    assert draft.country_signal is None


def test_golden_normalized_video_pins_the_whole_mapping() -> None:
    outcome = normalize_video_object(
        _video(), entity_id="entity-1", source_id="source-1",
        raw_observation_id="raw-1", observed_at=FIXED_NOW,
    )
    payload = {name: getattr(outcome.observation, name).to_json_value()
               if hasattr(getattr(outcome.observation, name), "to_json_value")
               else getattr(outcome.observation, name).isoformat()
               if isinstance(getattr(outcome.observation, name), datetime)
               else getattr(outcome.observation, name)
               for name in outcome.observation.__dataclass_fields__}
    assert_golden(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                  golden_path("market", "normalized_video.json"))
