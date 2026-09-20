# tests/unit/market/test_data_quality_gates.py
"""§24's gate matrix: every forbidden value quarantines with its own reason code.

The provider-id fixtures are 11 URL-safe characters because `VIDEO_ID_RE` is exactly
`^[A-Za-z0-9_-]{11}$` — the plan's fixture used a 12-character id, which would have
been quarantined as `malformed_provider_id` and made its other assertions vacuous.
"""

from datetime import UTC, datetime, timedelta

import pytest

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.normalization.youtube import (
    QUARANTINE_REASON_CODES,
    normalize_video_object,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
VIDEO_ID = "vid00000001"  # exactly 11 characters


def _fetched(**statistics) -> FetchedObject:
    payload = {
        "kind": "youtube#video", "id": VIDEO_ID,
        "snippet": {"publishedAt": "2026-08-01T10:00:00Z", "channelId": "UC" + "a" * 22,
                    "title": "fixture", "description": ""},
        "statistics": statistics,
    }
    return FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                         provider_object_id=VIDEO_ID, observed_at=NOW, payload=payload)


@pytest.mark.parametrize(("payload_id", "reason"), [
    ("short", "malformed_provider_id"),
])
def test_malformed_ids_are_quarantined(payload_id: str, reason: str) -> None:
    fetched = _fetched(viewCount="10")
    fetched = FetchedObject(provider_object_type=fetched.provider_object_type,
                            provider_object_id=payload_id, observed_at=NOW,
                            payload=fetched.payload)
    outcome = normalize_video_object(fetched, entity_id="e", source_id="s",
                                     raw_observation_id="r", observed_at=NOW)
    assert outcome.quarantine is not None
    assert outcome.quarantine.reason_code == reason
    assert reason in QUARANTINE_REASON_CODES


def test_negative_counts_and_impossible_timestamps_are_quarantined() -> None:
    negative = normalize_video_object(_fetched(viewCount="-5"), entity_id="e", source_id="s",
                                      raw_observation_id="r", observed_at=NOW)
    assert negative.quarantine is not None
    assert negative.quarantine.reason_code == "negative_count"

    future = _fetched(viewCount="5")
    future_payload = dict(future.payload)
    future_payload["snippet"] = dict(future.payload["snippet"])
    future_payload["snippet"]["publishedAt"] = (NOW + timedelta(days=1)).isoformat()
    impossible = normalize_video_object(
        FetchedObject(provider_object_type=future.provider_object_type,
                      provider_object_id=future.provider_object_id, observed_at=NOW,
                      payload=future_payload),
        entity_id="e", source_id="s", raw_observation_id="r", observed_at=NOW,
    )
    assert impossible.quarantine is not None
    assert impossible.quarantine.reason_code == "impossible_timestamp_order"


def test_a_non_numeric_count_is_a_value_error_at_the_boundary() -> None:
    """A count the parser cannot turn into an int fails loudly at the boundary;
    `non_finite_number` is reserved for floats (durations) that survive parsing."""
    assert "non_finite_number" in QUARANTINE_REASON_CODES
    with pytest.raises(ValueError):
        normalize_video_object(_fetched(viewCount="NaN"), entity_id="e", source_id="s",
                               raw_observation_id="r", observed_at=NOW)
