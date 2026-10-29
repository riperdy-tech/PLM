"""Unit tests for RealYouTubeClient adapter (P7 / R6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from plm_contracts import ProviderMode
from youtube_publishing.client.base import (
    YouTubeAuthError,
    YouTubeClientError,
    YouTubeClientProtocol,
    YouTubeQuotaExceededError,
    YouTubeTransientError,
)
from youtube_publishing.client.real import RealYouTubeClient


def test_real_client_satisfies_protocol() -> None:
    client = RealYouTubeClient(
        client_id="test_id",
        client_secret="test_secret",
        refresh_token="test_token",
    )
    assert isinstance(client, YouTubeClientProtocol)
    assert client.provider_mode == ProviderMode.REAL
    assert client.is_configured is True


def test_real_client_unconfigured() -> None:
    client = RealYouTubeClient(client_id="", client_secret="", refresh_token="")
    assert client.is_configured is False
    with pytest.raises(YouTubeAuthError, match="not configured"):
        client.insert_resumable_session(snippet={}, status={}, content_length=100)


def test_token_refresh_and_caching() -> None:
    client = RealYouTubeClient(
        client_id="cid", client_secret="csec", refresh_token="reftok"
    )

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "ya29.test_token", "expires_in": 3600}

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        token1 = client._get_access_token()
        assert token1 == "ya29.test_token"
        assert mock_post.call_count == 1

        # Second call within validity should use cached token
        token2 = client._get_access_token()
        assert token2 == "ya29.test_token"
        assert mock_post.call_count == 1


def test_token_refresh_errors() -> None:
    client = RealYouTubeClient(
        client_id="cid", client_secret="csec", refresh_token="reftok"
    )

    mock_resp_401 = MagicMock(spec=httpx.Response)
    mock_resp_401.status_code = 401
    mock_resp_401.text = "invalid_grant"

    with patch("httpx.Client.post", return_value=mock_resp_401), pytest.raises(YouTubeAuthError, match="rejected"):
        client._get_access_token()

    mock_resp_500 = MagicMock(spec=httpx.Response)
    mock_resp_500.status_code = 500
    mock_resp_500.text = "Internal Server Error"

    with patch("httpx.Client.post", return_value=mock_resp_500), pytest.raises(YouTubeTransientError, match="endpoint error"):
        client._get_access_token()


def test_insert_resumable_session() -> None:
    client = RealYouTubeClient(
        client_id="cid", client_secret="csec", refresh_token="reftok"
    )
    client._access_token = "valid_tok"
    client._access_token_expires_at = 9999999999.0

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {"Location": "https://upload.youtube.com/upload/session123"}

    with patch("httpx.Client.post", return_value=mock_resp):
        uri = client.insert_resumable_session(
            snippet={"title": "Test"},
            status={"privacyStatus": "private"},
            content_length=1024,
        )
        assert uri == "https://upload.youtube.com/upload/session123"


def test_upload_video_chunk() -> None:
    client = RealYouTubeClient(
        client_id="cid", client_secret="csec", refresh_token="reftok"
    )

    # 308 Resume Incomplete
    mock_resp_308 = MagicMock(spec=httpx.Response)
    mock_resp_308.status_code = 308

    with patch("httpx.Client.put", return_value=mock_resp_308):
        res1 = client.upload_video_chunk(
            session_uri="https://upload.youtube.com/upload/session123",
            chunk=b"0" * 500,
            start_byte=0,
            total_bytes=1000,
        )
        assert res1 is None

    # 200 Finished
    mock_resp_200 = MagicMock(spec=httpx.Response)
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"id": "vid_xyz123", "status": {"uploadStatus": "uploaded"}}

    with patch("httpx.Client.put", return_value=mock_resp_200):
        res2 = client.upload_video_chunk(
            session_uri="https://upload.youtube.com/upload/session123",
            chunk=b"0" * 500,
            start_byte=500,
            total_bytes=1000,
        )
        assert res2 is not None
        assert res2.get("id") == "vid_xyz123"


def test_quota_exceeded_error() -> None:
    client = RealYouTubeClient(
        client_id="cid", client_secret="csec", refresh_token="reftok"
    )
    client._access_token = "valid_tok"
    client._access_token_expires_at = 9999999999.0

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 403
    mock_resp.text = '{"error": {"errors": [{"reason": "quotaExceeded"}]}}'

    with patch("httpx.Client.post", return_value=mock_resp), pytest.raises(YouTubeQuotaExceededError, match="quota exceeded"):
        client.insert_resumable_session(snippet={}, status={}, content_length=100)


def test_get_video_and_playlists() -> None:
    client = RealYouTubeClient(
        client_id="cid", client_secret="csec", refresh_token="reftok"
    )
    client._access_token = "valid_tok"
    client._access_token_expires_at = 9999999999.0

    # get_video
    mock_resp_video = MagicMock(spec=httpx.Response)
    mock_resp_video.status_code = 200
    mock_resp_video.json.return_value = {"items": [{"id": "vid_01", "status": {"uploadStatus": "processed"}}]}

    with patch("httpx.Client.get", return_value=mock_resp_video) as api_get:
        item = client.get_video("vid_01")
        assert item is not None
        assert item["id"] == "vid_01"
        assert "contentDetails" in api_get.call_args.kwargs["params"]["part"]

    # set_thumbnail
    mock_resp_thumb = MagicMock(spec=httpx.Response)
    mock_resp_thumb.status_code = 200
    mock_resp_thumb.json.return_value = {"kind": "youtube#thumbnailSetResponse"}

    with patch("httpx.Client.post", return_value=mock_resp_thumb) as upload:
        thumb_res = client.set_thumbnail("vid_01", bytes.fromhex("ffd8ff") + b"image")
        assert thumb_res.get("kind") == "youtube#thumbnailSetResponse"
        assert upload.call_args.kwargs["headers"]["Content-Type"] == "image/jpeg"

    with patch("httpx.Client.post", return_value=mock_resp_thumb) as upload:
        png = bytes.fromhex("89504e470d0a1a0a") + b"image"
        client.set_thumbnail("vid_01", png)
        assert upload.call_args.kwargs["headers"]["Content-Type"] == "image/png"

    with patch("httpx.Client.post") as upload, pytest.raises(
        YouTubeClientError, match="not a PNG or JPEG"
    ):
        client.set_thumbnail("vid_01", b"fake_jpeg")
    upload.assert_not_called()

    # insert_playlist
    mock_resp_pl = MagicMock(spec=httpx.Response)
    mock_resp_pl.status_code = 201
    mock_resp_pl.json.return_value = {"id": "pl_01", "snippet": {"title": "Lofi Chill"}}

    with patch("httpx.Client.post", return_value=mock_resp_pl):
        pl_res = client.insert_playlist("Lofi Chill", "Description", "private")
        assert pl_res.get("id") == "pl_01"

    # insert_playlist_item
    mock_resp_item = MagicMock(spec=httpx.Response)
    mock_resp_item.status_code = 201
    mock_resp_item.json.return_value = {"id": "item_01", "snippet": {"playlistId": "pl_01"}}

    with patch("httpx.Client.post", return_value=mock_resp_item):
        item_res = client.insert_playlist_item("pl_01", "vid_01", 0)
        assert item_res.get("id") == "item_01"

