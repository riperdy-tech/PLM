import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from plm_platform.config import Secret

from services.market_intelligence.collection.rate_limit import QuotaBudget
from services.market_intelligence.connectors.base import ConnectorError, hash_request_context
from services.market_intelligence.connectors.youtube import (
    YouTubeCredentials,
    YouTubeDataAPIConnector,
    parse_video_stats,
)

SENTINEL = "yt-secret-sentinel-0123456789"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_the_credential_never_reaches_a_log_line_repr_or_payload(mock_http, caplog) -> None:
    payload = json.loads((FIXTURES / "videos_lofi_batch.json").read_text(encoding="utf-8"))
    mock_http.enqueue_json(payload)
    credentials = YouTubeCredentials(api_key=Secret(SENTINEL))
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=credentials, quota=QuotaBudget(10), observed_at=lambda: FIXED_NOW,
    )
    with caplog.at_level(logging.DEBUG):
        result = active.fetch_videos(["vid000000001"])
    assert SENTINEL not in repr(credentials)
    assert SENTINEL not in caplog.text
    assert SENTINEL not in str(result)
    assert SENTINEL not in str(parse_video_stats(result))


def test_a_connector_without_credentials_fails_closed(mock_http) -> None:
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(),
    )
    with pytest.raises(ConnectorError, match="AUTH_FAILURE"):
        active.fetch_videos(["vid000000001"])
    assert mock_http.requests == ()


def test_request_context_rejects_credential_parameters() -> None:
    with pytest.raises(ValueError, match="credential"):
        hash_request_context("youtube_data_api", "search.list", {"key": SENTINEL})
