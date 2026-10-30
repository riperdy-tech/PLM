"""R6 step 1: the Analytics client against docs-derived Google responses.

Proves the client handles Google's documented OAuth refresh and
``youtubeAnalytics#resultTable`` shapes (including omitted ``rows``) and the
Google error envelope, and sends one token refresh followed by authenticated,
read-only GETs. It does not prove Google still answers this way; see
``tests/support/provider_fixtures.py``.
"""
from __future__ import annotations

import pytest
from analytics.ingestion.youtube_client import (
    GOOGLE_TOKEN_URL,
    YOUTUBE_ANALYTICS_API_BASE,
    RealYouTubeAnalyticsClient,
    YouTubeAnalyticsError,
)

from tests.support.http import MockHTTP
from tests.support.provider_fixtures import enqueue

P = "youtube_analytics"
WINDOW = {"video_id": "lPYmaPHPQr4", "start_date": "2026-10-01", "end_date": "2026-10-28"}


def _client(mock: MockHTTP) -> RealYouTubeAnalyticsClient:
    return RealYouTubeAnalyticsClient(client_id="cid", client_secret="secret",
                                      refresh_token="refresh", http_client=mock.client())


def _assert_refresh_then_reads(mock: MockHTTP) -> None:
    token, *reads = mock.requests
    assert token.method == "POST" and token.url == GOOGLE_TOKEN_URL
    assert b"grant_type=refresh_token" in token.content
    for request in reads:
        assert request.method == "GET"
        assert request.url.startswith(YOUTUBE_ANALYTICS_API_BASE)
        assert request.headers["Authorization"] == "Bearer 1/fFAGRNJru1FTz70BzhT3Zg"


def test_video_metrics_map_the_documented_result_table(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token", "metrics_video")
    client = _client(mock_http)
    metrics = client.fetch_video_metrics(**WINDOW)
    assert (metrics.views, metrics.watch_time_minutes, metrics.average_view_duration_seconds,
            metrics.average_percentage_viewed, metrics.subscribers_gained,
            metrics.subscribers_lost) == (1520, 3410.0, 134.0, 41.7, 12, 1)
    mock_http.assert_query_params(1, ids="channel==MINE", filters="video==lPYmaPHPQr4",
                                  startDate="2026-10-01", endDate="2026-10-28")
    assert client.reporting_scope_status() == "required_scope_reported"
    _assert_refresh_then_reads(mock_http)


def test_omitted_rows_are_no_data_not_zero(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token", "metrics_no_rows")
    metrics = _client(mock_http).fetch_video_metrics(**WINDOW)
    assert metrics.views is None and metrics.watch_time_minutes is None


def test_one_token_refresh_serves_several_reads(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token", "traffic_sources", "retention")
    client = _client(mock_http)
    sources = client.fetch_video_traffic_sources(**WINDOW)
    retention = client.fetch_video_retention(**WINDOW)
    assert [(s.source_type, s.views) for s in sources.rows] == [
        ("YT_SEARCH", 820), ("SUBSCRIBER", 410), ("RELATED_VIDEO", 290)]
    assert [p.elapsed_video_fraction for p in retention.points] == [0.01, 0.25, 0.5, 1.0]
    mock_http.assert_query_params(1, dimensions="insightTrafficSourceType", sort="-views")
    mock_http.assert_query_params(2, dimensions="elapsedVideoTimeRatio")
    assert mock_http.request_count == 3
    _assert_refresh_then_reads(mock_http)


def test_a_refresh_without_scope_is_not_proof_of_permission(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token_no_scope", "metrics_no_rows")
    client = _client(mock_http)
    client.fetch_video_metrics(**WINDOW)
    assert client.reporting_scope_status() == "not_reported_by_oauth"


def test_revoked_refresh_token_stops_before_any_read(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_invalid_grant")
    with pytest.raises(YouTubeAnalyticsError, match="HTTP 400"):
        _client(mock_http).fetch_video_metrics(**WINDOW)
    assert mock_http.request_count == 1


@pytest.mark.parametrize("error", ["error_403_forbidden", "error_403_quota",
                                   "error_400_bad_query"])
@pytest.mark.parametrize("method", ["fetch_video_metrics", "fetch_video_traffic_sources",
                                    "fetch_video_retention"])
def test_google_error_envelopes_fail_closed(mock_http: MockHTTP, error: str,
                                            method: str) -> None:
    enqueue(mock_http, P, "oauth_token", error)
    with pytest.raises(YouTubeAnalyticsError, match="HTTP 4"):
        getattr(_client(mock_http), method)(**WINDOW)
