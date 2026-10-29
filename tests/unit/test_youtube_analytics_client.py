"""Unit tests for RealYouTubeAnalyticsClient (P8/P9 / R6)."""

from __future__ import annotations

import httpx
import pytest
from analytics.ingestion.youtube_client import (
    RealYouTubeAnalyticsClient,
    YouTubeAnalyticsError,
    YouTubeVideoMetrics,
)


def test_unconfigured_client_raises() -> None:
    client = RealYouTubeAnalyticsClient(client_id="", client_secret="", refresh_token="")
    assert not client.is_configured
    with pytest.raises(YouTubeAnalyticsError, match="YouTube OAuth credentials not configured"):
        client._get_access_token()


def test_oauth_failure_never_echoes_provider_response_or_secrets() -> None:
    from unittest.mock import patch

    client = RealYouTubeAnalyticsClient(
        client_id="client_secret_id", client_secret="private_secret",
        refresh_token="private_refresh",
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            401, text="private_secret private_refresh confidential-oauth-response",
        ),
    )
    with (
        patch("httpx.Client", return_value=httpx.Client(transport=transport)),
        pytest.raises(YouTubeAnalyticsError) as exc,
    ):
        client._get_access_token()
    error = str(exc.value)
    assert "HTTP 401" in error
    assert "private_secret" not in error
    assert "private_refresh" not in error
    assert "confidential-oauth-response" not in error


def test_fetch_video_metrics_parses_response() -> None:
    client = RealYouTubeAnalyticsClient(
        client_id="test_id",
        client_secret="test_secret",
        refresh_token="test_refresh",
    )
    client._access_token = "mock_token"
    client._access_token_expires_at = 9999999999.0

    mock_json = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
            {"name": "averageViewDuration"},
            {"name": "averageViewPercentage"},
            {"name": "subscribersGained"},
            {"name": "subscribersLost"},
        ],
        "rows": [[1420, 245.5, 185.2, 54.8, 12, 1]],
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "reports" in str(request.url):
            return httpx.Response(200, json=mock_json)
        return httpx.Response(404)

    # Monkeypatch httpx.Client to use mock transport
    transport = httpx.MockTransport(mock_handler)
    client._orig_client = httpx.Client  # type: ignore[attr-defined]

    import unittest.mock

    with unittest.mock.patch("httpx.Client", return_value=httpx.Client(transport=transport)):
        metrics = client.fetch_video_metrics(
            video_id="test_vid_123",
            start_date="2026-10-01",
            end_date="2026-10-19",
        )

    assert isinstance(metrics, YouTubeVideoMetrics)
    assert metrics.video_id == "test_vid_123"
    assert metrics.views == 1420
    assert metrics.watch_time_minutes == 245.5
    assert metrics.average_view_duration_seconds == 185.2
    assert metrics.average_percentage_viewed == 54.8
    assert metrics.subscribers_gained == 12
    assert metrics.subscribers_lost == 1


def test_fetch_video_metrics_empty_rows_returns_none_fields() -> None:
    client = RealYouTubeAnalyticsClient(
        client_id="test_id",
        client_secret="test_secret",
        refresh_token="test_refresh",
    )
    client._access_token = "mock_token"
    client._access_token_expires_at = 9999999999.0

    mock_json = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
            {"name": "averageViewDuration"},
            {"name": "averageViewPercentage"},
            {"name": "subscribersGained"},
            {"name": "subscribersLost"},
        ],
        "rows": [],
    }

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=mock_json))

    import unittest.mock

    with unittest.mock.patch("httpx.Client", return_value=httpx.Client(transport=transport)):
        metrics = client.fetch_video_metrics(
            video_id="new_vid_001",
            start_date="2026-10-01",
            end_date="2026-10-19",
        )

    assert metrics.views is None
    assert metrics.watch_time_minutes is None
    assert metrics.average_percentage_viewed is None

