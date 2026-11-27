"""R6 step 1: the YouTube Data client against docs-derived Google responses.

Proves the client follows Google's documented resumable-upload protocol
(session ``Location``, 308 with ``Range``, 201 with the video resource), reads
``videos.list`` and ``thumbnails.set`` shapes, and maps the documented error
reasons to the right exception. It does not prove Google still answers this
way; see ``tests/support/provider_fixtures.py``. Whether a write may happen at
all is the engine's ``ExternalWriteGuard`` decision, tested elsewhere.
"""
from __future__ import annotations

import httpx
import pytest
from youtube_publishing.client.base import (
    YouTubeAuthError,
    YouTubeClientError,
    YouTubeQuotaExceededError,
    YouTubeTransientError,
)
from youtube_publishing.client.real import GOOGLE_TOKEN_URL, RealYouTubeClient

from tests.support.http import MockHTTP
from tests.support.provider_fixtures import enqueue

P = "youtube_data"
SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=xa298sd_f"
BEARER = "Bearer 1/fFAGRNJru1FTz70BzhT3Zg"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8


def _client(mock: MockHTTP) -> RealYouTubeClient:
    return RealYouTubeClient(client_id="cid", client_secret="secret", refresh_token="refresh",
                             http_client=mock.client())


def _chunk(client: RealYouTubeClient, start: int) -> dict | None:
    return client.upload_video_chunk(session_uri=SESSION, chunk=b"v" * 500,
                                     start_byte=start, total_bytes=1000)


def test_localizations_are_requested_as_their_own_part(mock_http: MockHTTP) -> None:
    """videos.insert writes ``localizations`` only when ``part`` names it (ADR-0045)."""
    enqueue(mock_http, P, "oauth_token", "session_created")
    texts = {"ja": {"title": "サーバールームの夜勤｜作業用BGM", "description": "説明"}}
    _client(mock_http).insert_resumable_session(
        snippet={"title": "t", "defaultLanguage": "en"}, status={}, content_length=1,
        localizations=texts)
    _, init = mock_http.requests
    assert init.param("part") == "snippet,status,localizations"
    assert init.json()["localizations"] == texts


def test_resumable_upload_follows_the_documented_protocol(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token", "session_created", "chunk_308_all_received",
            "upload_complete")
    client = _client(mock_http)
    uri = client.insert_resumable_session(snippet={"title": "t"}, status={"privacyStatus": "private"},
                                          content_length=1000)
    assert uri == SESSION
    assert _chunk(client, 0) is None
    video = _chunk(client, 500)
    assert video is not None and video["id"] == "Ks-_Mh1QhMc"

    token, init, first, last = mock_http.requests
    assert token.url == GOOGLE_TOKEN_URL
    assert init.method == "POST" and init.param("uploadType") == "resumable"
    assert init.headers["Authorization"] == BEARER
    assert init.headers["X-Upload-Content-Length"] == "1000"
    assert init.json() == {"snippet": {"title": "t"}, "status": {"privacyStatus": "private"}}
    assert init.param("part") == "snippet,status"  # no localizations, no extra part
    assert (first.method, first.url) == ("PUT", SESSION)
    assert first.headers["Content-Range"] == "bytes 0-499/1000"
    assert last.headers["Content-Range"] == "bytes 500-999/1000"


@pytest.mark.parametrize("reply", ["chunk_308_partial", "chunk_308_none_received"])
def test_a_short_receipt_stops_the_upload(mock_http: MockHTTP, reply: str) -> None:
    """The engine advances by the whole chunk, so skipping unreceived bytes would corrupt it."""
    enqueue(mock_http, P, reply)
    with pytest.raises(YouTubeTransientError, match="resumes from the server's offset"):
        _chunk(_client(mock_http), 0)


def test_a_timeout_is_transient_not_permanent(mock_http: MockHTTP) -> None:
    """A lost response may still have reached YouTube: retry and ask, never give up."""
    mock_http.enqueue_timeout()
    with pytest.raises(YouTubeTransientError, match="did not complete"):
        _chunk(_client(mock_http), 0)


def test_the_upload_status_query_reads_what_youtube_holds(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "chunk_308_partial", "chunk_308_none_received", "upload_complete")
    client = _client(mock_http)
    assert client.query_upload_status(session_uri=SESSION, total_bytes=1000) == (300, None)
    assert client.query_upload_status(session_uri=SESSION, total_bytes=1000) == (0, None)
    total, video = client.query_upload_status(session_uri=SESSION, total_bytes=1000)
    assert total == 1000 and video is not None and video.get("id")
    assert mock_http.requests[0].headers["Content-Range"] == "bytes */1000"


def test_session_without_location_is_refused(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token")
    mock_http.enqueue_text("", status=200)
    with pytest.raises(YouTubeClientError, match="Location"):
        _client(mock_http).insert_resumable_session(snippet={}, status={}, content_length=1)


def test_get_video_reads_processing_details(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token", "videos_list", "videos_list_empty")
    client = _client(mock_http)
    video = client.get_video("Ks-_Mh1QhMc")
    assert video is not None
    assert video["processingDetails"]["processingStatus"] == "succeeded"
    assert video["contentDetails"]["duration"] == "PT58M12S"
    mock_http.assert_query_params(1, id="Ks-_Mh1QhMc",
                                  part="snippet,status,processingDetails,contentDetails")
    assert client.get_video("missing") is None


def test_set_thumbnail_returns_the_set_response(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_token", "thumbnails_set")
    result = _client(mock_http).set_thumbnail("Ks-_Mh1QhMc", PNG)
    assert result["kind"] == "youtube#thumbnailSetResponse"
    assert mock_http.request(1).headers["Content-Type"] == "image/png"


def test_revoked_refresh_token_is_an_auth_error(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_invalid_grant")
    with pytest.raises(YouTubeAuthError):
        _client(mock_http).get_video("Ks-_Mh1QhMc")
    assert mock_http.request_count == 1


@pytest.mark.parametrize(("error", "expected"), [
    ("error_403_quota", YouTubeQuotaExceededError),
    ("error_401", YouTubeAuthError),
    ("error_503", YouTubeTransientError),
])
def test_documented_errors_map_to_their_exception(mock_http: MockHTTP, error: str,
                                                  expected: type[Exception]) -> None:
    enqueue(mock_http, P, "oauth_token", error)
    with pytest.raises(expected):
        _client(mock_http).insert_resumable_session(snippet={}, status={}, content_length=1)


@pytest.mark.parametrize("error", ["error_403_forbidden", "error_400_upload_limit"])
def test_permanent_refusals_are_neither_quota_nor_transient(mock_http: MockHTTP,
                                                            error: str) -> None:
    enqueue(mock_http, P, "oauth_token", error)
    with pytest.raises(YouTubeClientError) as raised:
        _client(mock_http).insert_resumable_session(snippet={}, status={}, content_length=1)
    assert type(raised.value) is YouTubeClientError


@pytest.mark.parametrize("error", ["error_403_rate_limit", "error_429_too_many_requests"])
def test_a_rate_limit_is_transient_and_refused(mock_http: MockHTTP, error: str) -> None:
    """A short-term rate limit is retried later; YouTube refused it, so nothing happened."""
    enqueue(mock_http, P, "oauth_token", error)
    with pytest.raises(YouTubeTransientError) as raised:
        _client(mock_http).insert_resumable_session(snippet={}, status={}, content_length=1)
    assert raised.value.refused


@pytest.mark.parametrize(("error", "refused"), [
    ("error_403_quota", True), ("error_401", True), ("error_403_forbidden", True),
    ("error_400_upload_limit", True), ("error_503", False),
])
def test_a_4xx_answer_marks_the_write_as_refused(mock_http: MockHTTP, error: str,
                                                 refused: bool) -> None:
    """A 4xx is a definite "not performed"; a 5xx leaves the outcome unknown (review #31)."""
    enqueue(mock_http, P, "oauth_token", error)
    with pytest.raises(YouTubeClientError) as raised:
        _client(mock_http).insert_resumable_session(snippet={}, status={}, content_length=1)
    assert raised.value.refused is refused


def test_one_connection_pool_serves_every_chunk(mock_http: MockHTTP,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """A new ``httpx.Client`` per chunk cost ~800 TLS handshakes per video (review P2)."""
    import youtube_publishing.client.real as real

    built: list[object] = []
    original = httpx.Client

    def counting(**options: object) -> httpx.Client:
        client = original(transport=mock_http._transport, **options)  # type: ignore[arg-type]
        built.append(client)
        return client

    monkeypatch.setattr(real.httpx, "Client", counting)
    enqueue(mock_http, P, "oauth_token", "session_created", "chunk_308_all_received",
            "upload_complete")
    client = RealYouTubeClient(client_id="cid", client_secret="secret", refresh_token="r")
    client.insert_resumable_session(snippet={}, status={}, content_length=1000)
    assert _chunk(client, 0) is None
    assert _chunk(client, 500) is not None
    assert len(built) == 1
    client.close()


def test_the_engine_uploads_in_8_mib_chunks() -> None:
    """Google wants multiples of 256 KiB; 8 MiB keeps an 821 MB master to ~100 requests."""
    from inspect import signature

    from youtube_publishing.engine import YouTubePublishingEngine

    assert signature(YouTubePublishingEngine).parameters["chunk_size"].default == 8 * 1024 * 1024


def test_a_rejected_token_refresh_is_refused(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "oauth_invalid_grant")
    with pytest.raises(YouTubeAuthError) as raised:
        _client(mock_http).insert_resumable_session(snippet={}, status={}, content_length=1)
    assert raised.value.refused
