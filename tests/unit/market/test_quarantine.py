# tests/unit/market/test_quarantine.py
"""§24 quarantine gates and Review Focus 1: an unobserved metric is never a zero.

The plan's Files list and Review Focus name this file/test but provide no step body;
the assertions pin the gates implemented in `normalization/youtube.py`.
"""

from datetime import UTC, datetime

from plm_contracts import TriState

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.normalization.youtube import (
    QUARANTINE_REASON_CODES,
    normalize_video_object,
)

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
RAW = "00000000-0000-4000-8000-000000000001"


def _fetched(video_id: str, *, statistics: dict | None = None,
             published_at: str = "2026-08-01T10:00:00Z") -> FetchedObject:
    snippet = {"title": "2AM Seoul", "publishedAt": published_at}
    payload: dict = {"id": video_id, "snippet": snippet}
    if statistics is not None:
        payload["statistics"] = statistics
    return FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                         provider_object_id=video_id, observed_at=FIXED_NOW, payload=payload)


def _normalize(fetched: FetchedObject):
    return normalize_video_object(fetched, entity_id="entity-1", source_id="source-1",
                                  raw_observation_id=RAW, observed_at=FIXED_NOW)


def test_malformed_provider_id_is_quarantined() -> None:
    for bad_id in ("vid1", "vid" + "0" * 10):  # too short, and a 13-character id
        outcome = _normalize(_fetched(bad_id))
        assert outcome.observation is None
        assert outcome.quarantine is not None
        assert outcome.quarantine.reason_code == "malformed_provider_id"


def test_negative_view_count_is_quarantined_with_a_reason_code() -> None:
    outcome = _normalize(_fetched("vid000000001", statistics={"viewCount": "-5"}))
    assert outcome.observation is None
    assert outcome.quarantine.reason_code == "negative_count"
    assert outcome.quarantine.reason_code in QUARANTINE_REASON_CODES


def test_impossible_timestamp_order_is_quarantined() -> None:
    outcome = _normalize(_fetched("vid000000001", statistics={"viewCount": "1"},
                                  published_at="2027-01-01T00:00:00Z"))
    assert outcome.observation is None
    assert outcome.quarantine.reason_code == "impossible_timestamp_order"


def test_unobserved_metric_is_not_a_measured_zero() -> None:
    unobserved = _normalize(_fetched("vid000000001"))
    assert unobserved.observation.views.state is TriState.UNKNOWN
    assert unobserved.observation.views.is_zero is False

    measured_zero = _normalize(_fetched("vid000000001", statistics={"viewCount": "0"}))
    assert measured_zero.observation.views.state is TriState.KNOWN
    assert measured_zero.observation.views.is_zero is True
    assert measured_zero.observation.views.unwrap() == 0
