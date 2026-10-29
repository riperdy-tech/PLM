"""Read-only discovery of the authenticated channel's latest uploaded videos."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from youtube_publishing.client.base import YouTubeClientError, YouTubeQuotaExceededError
from youtube_publishing.client.real import RealYouTubeClient


def _client() -> RealYouTubeClient:
    client = RealYouTubeClient(client_id="id", client_secret="secret", refresh_token="refresh")
    client._access_token = "cached"
    client._access_token_expires_at = 9999999999.0
    return client


def _call(
    channels: dict, playlist: dict, *, expected_channel: str | None = "UC_test",
    channel_status: int = 200, playlist_status: int = 200,
) -> tuple[list[dict[str, str]], list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels"):
            return httpx.Response(channel_status, json=channels if channel_status == 200 else
                                  {"error": {"errors": [{"reason": "quotaExceeded"}]}})
        if request.url.path.endswith("/playlistItems"):
            return httpx.Response(playlist_status, json=playlist)
        raise AssertionError("unexpected external request")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch("httpx.Client", return_value=http_client):
        items = _client().list_recent_uploads(max_results=10, channel_id=expected_channel)
    return items, requests


CHANNELS = {
    "items": [{"id": "UC_test", "contentDetails": {"relatedPlaylists": {"uploads": "UU_test"}}}]
}
PLAYLIST = {
    "items": [
        {"snippet": {"title": "Latest", "resourceId": {"videoId": "video_new"},
                     "publishedAt": "2026-10-22T00:00:00Z"},
         "contentDetails": {"videoId": "video_new", "videoPublishedAt": "2026-10-21T20:00:00Z"}},
        {"snippet": {"title": "Earlier", "resourceId": {"videoId": "video_old"},
                     "publishedAt": "2026-10-15T00:00:00Z"},
         "contentDetails": {"videoId": "video_old"}},
    ]
}


def test_lists_newest_real_uploads_from_authenticated_channel_not_search() -> None:
    items, requests = _call(CHANNELS, PLAYLIST)
    assert [item["video_id"] for item in items] == ["video_new", "video_old"]
    assert items[0]["published_at"] == "2026-10-21T20:00:00Z"
    assert len(requests) == 2
    assert requests[0].url.params["mine"] == "true"
    assert requests[1].url.params["playlistId"] == "UU_test"
    assert requests[1].url.params["maxResults"] == "10"
    assert all(request.method == "GET" for request in requests)
    assert not any("search" in str(request.url) for request in requests)


def test_channel_mismatch_refused_before_opening_uploads_playlist() -> None:
    with pytest.raises(YouTubeClientError, match="does not match"):
        _call(CHANNELS, PLAYLIST, expected_channel="UC_other")


@pytest.mark.parametrize("channels", [
    {}, {"items": []}, {"items": [{"id": "UC_test"}]},
    {"items": [{"id": "UC_test"}, {"id": "UC_other"}]},
])
def test_missing_channel_or_upload_playlist_is_not_false_success(channels: dict) -> None:
    with pytest.raises(YouTubeClientError):
        _call(channels, PLAYLIST)


def test_empty_uploads_playlist_is_real_empty_not_a_fixed_fallback() -> None:
    items, _ = _call(CHANNELS, {"items": []})
    assert items == []


@pytest.mark.parametrize("item", [
    {}, {"snippet": {"title": "Video"}},
    {"snippet": {"title": " ", "resourceId": {"videoId": "xx"}}},
    {"snippet": {"title": "Title", "resourceId": {"videoId": "xx"}}},
])
def test_malformed_playlist_item_is_rejected(item: dict) -> None:
    with pytest.raises(YouTubeClientError):
        _call(CHANNELS, {"items": [item]})


def test_quota_failure_remains_error_not_empty_playlist() -> None:
    with pytest.raises(YouTubeQuotaExceededError):
        _call(CHANNELS, PLAYLIST, channel_status=403)


@pytest.mark.parametrize("bad_limit", [-1, 0, 51])
def test_limits_checked_before_any_network(bad_limit: int) -> None:
    with patch("httpx.Client") as constructor:
        with pytest.raises(ValueError, match="max_results"):
            _client().list_recent_uploads(max_results=bad_limit)
        constructor.assert_not_called()
