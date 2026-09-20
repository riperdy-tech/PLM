import json
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
from plm_platform.config import Secret

from services.market_intelligence.collection.rate_limit import (
    QuotaBudget,
    QuotaExceededError,
    RequestBudget,
)
from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.connectors.youtube import (
    LIST_QUOTA_UNITS,
    SEARCH_QUOTA_UNITS,
    YouTubeCredentials,
    YouTubeDataAPIConnector,
    parse_channel_stats,
    parse_iso8601_duration,
    parse_search_hits,
    parse_video_stats,
)
from services.market_intelligence.domain.enums import ConnectorErrorClass

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


def test_video_hydration_maps_counts_and_durations_without_inventing_missing_values(mock_http) -> None:
    mock_http.enqueue_json(load("videos_lofi_batch.json"))
    result = connector(mock_http).fetch_videos(["vid000000001", "vid000000002"])
    assert result.quota_units == LIST_QUOTA_UNITS
    mock_http.assert_query_params(0, part="snippet,statistics,contentDetails")
    stats = {s.video_id: s for s in parse_video_stats(result)}
    assert stats["vid000000001"].view_count == 120000
    assert stats["vid000000001"].duration_seconds == 3720.0
    assert stats["vid000000002"].comment_count is None  # absent ⇒ None, never 0
    assert stats["vid000000002"].like_count == 210


def test_channel_hydration_keeps_hidden_subscriber_counts_unknown(mock_http) -> None:
    mock_http.enqueue_json(load("channels_lofi_batch.json"))
    result = connector(mock_http).fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa",
                                                  "UCbbbbbbbbbbbbbbbbbbbbbb"])
    stats = {s.channel_id: s for s in parse_channel_stats(result)}
    assert stats["UCaaaaaaaaaaaaaaaaaaaaaa"].subscriber_count == 820000
    assert stats["UCbbbbbbbbbbbbbbbbbbbbbb"].subscriber_count is None
    assert stats["UCaaaaaaaaaaaaaaaaaaaaaa"].country == "KR"


def test_a_5xx_is_retried_then_succeeds(mock_http) -> None:
    mock_http.enqueue_server_error(status=503).enqueue_json(load("videos_lofi_batch.json"))
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        sleep=lambda _seconds: None, rng=Random(0), observed_at=lambda: FIXED_NOW,
    )
    result = active.fetch_videos(["vid000000001", "vid000000002"])
    assert len(result.objects) == 2
    assert len(mock_http.requests) == 2


def test_a_429_honours_retry_after_and_is_not_retried_forever(mock_http) -> None:
    mock_http.enqueue_rate_limit(retry_after="30").enqueue_json(load("channels_lofi_batch.json"))
    naps: list[float] = []
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        sleep=naps.append, rng=Random(0), observed_at=lambda: FIXED_NOW,
    )
    active.fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa"])
    assert naps == [30.0]


def test_an_invalid_request_is_never_retried(mock_http) -> None:
    mock_http.enqueue_status(400, payload={"error": {"code": 400, "message": "bad"}})
    active = connector(mock_http)
    with pytest.raises(ConnectorError) as excinfo:
        active.fetch_videos(["vid000000001"])
    assert excinfo.value.error_class is ConnectorErrorClass.INVALID_REQUEST
    assert len(mock_http.requests) == 1


def test_a_malformed_body_is_a_parser_failure_and_a_missing_items_key_is_a_schema_change(mock_http) -> None:
    mock_http.enqueue_malformed_json()
    with pytest.raises(ConnectorError) as excinfo:
        connector(mock_http).fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa"])
    assert excinfo.value.error_class is ConnectorErrorClass.PARSER_FAILURE

    mock_http.enqueue_json({"kind": "youtube#channelListResponse"})
    with pytest.raises(ConnectorError) as excinfo:
        connector(mock_http).fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa"])
    assert excinfo.value.error_class is ConnectorErrorClass.SCHEMA_CHANGE
