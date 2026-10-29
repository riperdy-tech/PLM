"""Official YouTube source breakdown: traffic is not thumbnail CTR."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from analytics.ingestion.youtube_client import (
    RealYouTubeAnalyticsClient,
    YouTubeAnalyticsError,
)

_HEADERS = [
    {"name": "insightTrafficSourceType"},
    {"name": "views"},
    {"name": "estimatedMinutesWatched"},
]


def _fetch(payload: dict, status: int = 200):
    requests: list[httpx.Request] = []
    client = RealYouTubeAnalyticsClient(client_id="id", client_secret="s", refresh_token="r")
    client._access_token = "token"
    client._access_token_expires_at = 9999999999.0

    def respond(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return httpx.Response(status, json=payload)

    with patch("httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(respond))):
        result = client.fetch_video_traffic_sources(
            video_id="video_test", start_date="2026-10-01", end_date="2026-10-21",
        )
    return result, requests


def test_supported_source_dimension_and_measured_rows() -> None:
    report, requests = _fetch({
        "columnHeaders": _HEADERS,
        "rows": [["YT_SEARCH", 120, 580.5], ["RELATED_VIDEO", 40, 70.25]],
    })
    assert report.video_id == "video_test"
    assert report.rows[0].source_type == "YT_SEARCH"
    assert report.rows[0].views == 120
    assert report.rows[1].watch_time_minutes == 70.25
    params = requests[0].url.params
    assert params["dimensions"] == "insightTrafficSourceType"
    assert params["metrics"] == "views,estimatedMinutesWatched"
    assert params["filters"] == "video==video_test"
    assert params["startDate"] == "2026-10-01"
    assert "Impressions" not in str(requests[0].url)
    assert "ClickRate" not in str(requests[0].url)


def test_empty_source_report_is_unknown_not_zero() -> None:
    report, _ = _fetch({"columnHeaders": _HEADERS, "rows": []})
    assert report.rows == ()


@pytest.mark.parametrize("rows", [
    [["YT_SEARCH", -2, 1]],
    [["YT_SEARCH", 1, -4]],
    [["YT_SEARCH", 1.5, 0]],
    [["YT_SEARCH", None, 1]],
    [["YT_SEARCH", 0, "NaN"]],
    [["YT_SEARCH", 3, 1], ["YT_SEARCH", 2, 2]],
    [["", 3, 1]],
    [["YT_SEARCH", 2]],
])
def test_malformed_sources_not_silently_accepted(rows) -> None:
    with pytest.raises(YouTubeAnalyticsError):
        _fetch({"columnHeaders": _HEADERS, "rows": rows})


def test_unsupported_headers_and_status_refuse() -> None:
    with pytest.raises(YouTubeAnalyticsError, match="missing required"):
        _fetch({"columnHeaders": [{"name": "views"}], "rows": [[42]]})
    with pytest.raises(YouTubeAnalyticsError, match="HTTP 403"):
        _fetch({"error": {"message": "no access"}}, status=403)


def test_window_and_video_required_before_network() -> None:
    client = RealYouTubeAnalyticsClient(client_id="id", client_secret="s", refresh_token="r")
    with patch("httpx.Client") as mocked:
        with pytest.raises(ValueError, match="video_id"):
            client.fetch_video_traffic_sources(video_id="", start_date="2026-01-01", end_date="2026-01-02")
        with pytest.raises(ValueError, match="start_date"):
            client.fetch_video_traffic_sources(video_id="vid", start_date="2026-01-10", end_date="2026-01-02")
        mocked.assert_not_called()
