import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from plm_platform.config import Secret

from services.market_intelligence.collection.rate_limit import (
    QuotaBudget,
    QuotaExceededError,
    RequestBudget,
)
from services.market_intelligence.connectors.youtube import (
    SEARCH_QUOTA_UNITS,
    YouTubeCredentials,
    YouTubeDataAPIConnector,
    parse_iso8601_duration,
    parse_search_hits,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def connector(mock_http) -> YouTubeDataAPIConnector:
    return YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        quota=QuotaBudget(limit_units=1000),
        request_budget=RequestBudget(limit_requests=10),
        observed_at=lambda: FIXED_NOW,
    )


def test_search_sends_the_documented_request_and_returns_raw_objects(mock_http) -> None:
    mock_http.enqueue_json(load("search_lofi_p1.json"))
    result = connector(mock_http).search_videos("lofi", max_results=2)
    assert [o.provider_object_id for o in result.objects] == ["vid000000001", "vid000000002"]
    assert result.quota_units == SEARCH_QUOTA_UNITS
    assert result.next_page_token == "CAoQAA"
    request = mock_http.requests[0]
    assert request.path == "/youtube/v3/search"
    mock_http.assert_query_params(0, part="snippet", type="video", q="lofi", maxResults="2")
    hits = parse_search_hits(result)
    assert hits[0].video_id == "vid000000001"
    assert hits[0].published_at == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    assert hits[1].channel_title == "StudyLoops"


def test_pagination_follows_next_page_token_and_respects_max_pages(mock_http) -> None:
    mock_http.enqueue_json(load("search_lofi_p1.json")).enqueue_json(load("search_lofi_p2.json"))
    pages = list(connector(mock_http).iter_search_videos("lofi", max_results=2, max_pages=2))
    assert len(pages) == 2
    assert mock_http.requests[1].param("pageToken") == "CAoQAA"
    assert sum(p.quota_units for p in pages) == 2 * SEARCH_QUOTA_UNITS


def test_quota_exhaustion_stops_before_the_request_is_sent(mock_http) -> None:
    # The plan used limit_units=100, but its own budget semantics allow a call whose
    # cost equals the remaining budget, so a 100-unit budget affords one search.list.
    # Exhaustion needs a budget below the operation's cost.
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        quota=QuotaBudget(limit_units=SEARCH_QUOTA_UNITS - 1),
        observed_at=lambda: FIXED_NOW,
    )
    with pytest.raises(QuotaExceededError):
        active.search_videos("lofi")
        # the budget refuses *before* the HTTP call; no canned response is enqueued
    assert mock_http.requests == ()


def test_iso8601_durations_are_parsed_and_unparseable_values_are_none() -> None:
    assert parse_iso8601_duration("PT1H2M") == 3720.0
    assert parse_iso8601_duration("PT3M30S") == 210.0
    assert parse_iso8601_duration("") is None
    assert parse_iso8601_duration("P1D") is None  # day-granularity is not a video duration
