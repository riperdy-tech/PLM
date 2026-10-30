"""R6 step 1: the Suno client against its recorded-live responses.

Suno publishes no API documentation, so the only conformance evidence is what
Suno actually returned (``tests/fixtures/providers/suno/``, recorded-live).
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from music_factory.providers.adapters import SunoAdapter
from music_factory.providers.suno_client import SunoClient, SunoClientError

from tests.support.http import MockHTTP
from tests.support.provider_fixtures import enqueue


def _jwt(*, exp: float, iat: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp, "iat": iat}).encode()).decode()
    return f"e30.{body.rstrip('=')}.sig"


def _client(mock: MockHTTP) -> SunoClient:
    return SunoClient(_jwt(exp=time.time() + 3600, iat=time.time()),
                      http_client=mock.client())


def test_recorded_401_fails_closed(mock_http: MockHTTP) -> None:
    enqueue(mock_http, "suno", "billing_401_expired_token")
    with pytest.raises(SunoClientError, match="HTTP 401"):
        _client(mock_http).get_billing_info()
    request = mock_http.request(0)
    assert (request.method, request.url) == (
        "GET", "https://studio-api-prod.suno.com/api/billing/info/")


def test_recorded_401_is_unhealthy(mock_http: MockHTTP) -> None:
    enqueue(mock_http, "suno", "billing_401_expired_token")
    assert _client(mock_http).check_health() is False


def test_recorded_billing_reply_is_healthy(mock_http: MockHTTP) -> None:
    enqueue(mock_http, "suno", "billing_info")
    assert _client(mock_http).check_health() is True


def test_recorded_billing_reply_gives_an_observed_quota(mock_http: MockHTTP) -> None:
    """The recorded reply carries `total_credits_left` (an int), not `credits_left`."""
    enqueue(mock_http, "suno", "billing_info")
    adapter = SunoAdapter(api_key="configured")
    adapter._client = _client(mock_http)
    status = adapter.get_quota_status()
    assert (status["quota_status"], status["credits_remaining"],
            status["quota_exhausted"]) == ("observed", 50, False)


def test_recorded_401_leaves_the_quota_unknown(mock_http: MockHTTP) -> None:
    enqueue(mock_http, "suno", "billing_401_expired_token")
    adapter = SunoAdapter(api_key="configured")
    adapter._client = _client(mock_http)
    assert adapter.get_quota_status()["quota_status"] == "unknown"


def test_a_token_saved_after_its_expiry_counts_as_expired() -> None:
    """The recorded 401 came from a JWT that had expired 11 days earlier and was never refreshed."""
    now = time.time()
    stale = SunoClient(_jwt(exp=now - 11 * 86400, iat=now - 11 * 86400 - 3600))
    fresh = SunoClient(_jwt(exp=now + 3600, iat=now))
    assert stale._is_token_expired()
    assert not fresh._is_token_expired()
