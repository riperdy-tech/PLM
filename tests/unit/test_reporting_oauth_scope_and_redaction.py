"""OAuth scope diagnostics and Google response secrecy for read-only analytics."""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from analytics.ingestion.youtube_client import RealYouTubeAnalyticsClient, YouTubeAnalyticsError

from scripts import check_reporting_environment as readiness


@pytest.mark.parametrize(("scope", "expected"), [
    ("https://www.googleapis.com/auth/yt-analytics.readonly", "required_scope_reported"),
    ("https://www.googleapis.com/auth/yt-analytics-monetary.readonly", "required_scope_reported"),
    ("https://www.googleapis.com/auth/youtube.readonly", "required_scope_not_reported"),
    (None, "not_reported_by_oauth"),
    (123, "not_reported_by_oauth"),
])
def test_oauth_refresh_scopes_are_classified_without_requesting_more(
    scope: object, expected: str,
) -> None:
    analytics = RealYouTubeAnalyticsClient(
        client_id="private-id", client_secret="private-secret", refresh_token="private-refresh",
    )
    assert analytics.reporting_scope_status() == "not_reported_by_oauth"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.host == "oauth2.googleapis.com"
        assert b"scope=" not in request.content
        payload: dict[str, object] = {"access_token": "private-access", "expires_in": 3600}
        if scope is not None:
            payload["scope"] = scope
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
        assert analytics._get_access_token() == "private-access"
    assert analytics.reporting_scope_status() == expected


@pytest.mark.parametrize("operation", [
    "fetch_video_metrics", "fetch_video_traffic_sources", "fetch_video_retention",
])
def test_google_error_bodies_are_never_echoed_for_any_analytics_read(operation: str) -> None:
    analytics = RealYouTubeAnalyticsClient(
        client_id="private-id", client_secret="private-secret", refresh_token="private-refresh",
    )
    analytics._access_token = "private-access"
    analytics._access_token_expires_at = 9999999999.0

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(403, text="private-access account-private-data")

    with (
        patch("httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handler))),
        pytest.raises(YouTubeAnalyticsError) as exc,
    ):
        getattr(analytics, operation)(
            video_id="video_1", start_date="2026-10-01", end_date="2026-10-02",
        )
    assert "HTTP 403" in str(exc.value)
    assert "private-access" not in str(exc.value)
    assert "account-private-data" not in str(exc.value)


def test_google_denial_reports_scope_status_without_sensitive_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    class TokenHolder:
        is_configured = True

        def _get_access_token(self) -> str:
            return "private-access"

        def reporting_scope_status(self) -> str:
            return "required_scope_not_reported"

    monkeypatch.setattr(readiness, "load_env_file", lambda _: {})
    monkeypatch.setattr(readiness.os, "environ", {})
    monkeypatch.setattr(readiness, "RealYouTubeAnalyticsClient", TokenHolder)
    with patch(
        "httpx.Client",
        return_value=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(403, text="private-access account-private-data"),
        )),
    ):
        assert readiness.main(["--probe-google-catalog"]) == 2

    printed = capsys.readouterr().out
    result = json.loads(printed)
    assert result["google_catalog"] == "permission_denied"
    assert result["google_oauth_scopes"] == "required_scope_not_reported"
    assert "private-access" not in printed
    assert "account-private-data" not in printed
