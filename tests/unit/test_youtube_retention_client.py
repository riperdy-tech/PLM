"""Retention API shape and truthful missing/invalid evidence handling."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from analytics.ingestion.youtube_client import RealYouTubeAnalyticsClient, YouTubeAnalyticsError


def _client() -> RealYouTubeAnalyticsClient:
    client = RealYouTubeAnalyticsClient(
        client_id="id", client_secret="secret", refresh_token="ref",
    )
    client._access_token = "mock_token"
    client._access_token_expires_at = 9999999999.0
    return client


def _fetch(json_payload: dict) -> tuple:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=json_payload)

    client = _client()
    with patch("httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handler))):
        result = client.fetch_video_retention(
            video_id="vid_test", start_date="2026-10-01", end_date="2026-10-23",
        )
    return result, seen


_HEADERS = [
    {"name": "elapsedVideoTimeRatio"},
    {"name": "audienceWatchRatio"},
    {"name": "relativeRetentionPerformance"},
]


def test_retention_curve_maps_ordered_ratios_without_clamping_replays() -> None:
    result, requests = _fetch({
        "columnHeaders": _HEADERS,
        "rows": [[0.0, 1.0, 0.6], [0.5, 1.2, 0.4], [0.99, 0.3, 0.7]],
    })
    assert len(result.points) == 3
    assert result.points[1].audience_watch_ratio == 1.2
    assert result.points[2].relative_retention_performance == 0.7
    assert requests[0].url.params["dimensions"] == "elapsedVideoTimeRatio"
    assert requests[0].url.params["filters"] == "video==vid_test"


def test_retention_unavailable_is_empty_not_zero() -> None:
    result, _ = _fetch({"columnHeaders": _HEADERS, "rows": []})
    assert result.points == ()


@pytest.mark.parametrize("rows", [
    [[0.0, 1.0, 0.5], [0.0, 0.8, 0.5]],
    [[0.0, -0.1, 0.5]],
    [[1.1, 0.5, 0.5]],
    [[0.0, 0.5, 1.1]],
    [[0.0, None, 0.5]],
])
def test_retention_refuses_corrupt_series(rows: list) -> None:
    with pytest.raises(YouTubeAnalyticsError):
        _fetch({"columnHeaders": _HEADERS, "rows": rows})


def test_missing_headers_never_infer_a_watch_curve() -> None:
    with pytest.raises(YouTubeAnalyticsError, match="missing required"):
        _fetch({"columnHeaders": [{"name": "views"}], "rows": [[300]]})
