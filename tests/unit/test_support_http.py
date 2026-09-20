"""Unit tests for the mock-HTTP harness (``tests/support/http.py``).

The harness is load-bearing for four phases' connector tests, so every
simulation it advertises is exercised here: recording, query assertions,
canned sequences, 429/Retry-After, 500, timeouts, malformed bodies, the
``install`` patch and the async client path.
"""

from __future__ import annotations

import httpx
import pytest

from tests.support.http import MockHTTP


def test_records_requests_and_asserts_on_them() -> None:
    mock = MockHTTP()
    mock.enqueue_json({"items": []})
    with mock.client(base_url="https://api.example") as client:
        response = client.get("/videos", params={"part": "snippet", "maxResults": 5})
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert mock.request_count == 1
    recorded = mock.request()
    assert recorded.method == "GET"
    assert recorded.path == "/videos"
    assert recorded.url.startswith("https://api.example/videos?")
    assert recorded.param("part") == "snippet"
    mock.assert_request(0, method="GET", path="/videos")
    mock.assert_query_params(0, part="snippet", maxResults="5")


def test_query_assertion_names_expected_and_actual() -> None:
    mock = MockHTTP()
    mock.enqueue_json({})
    with mock.client(base_url="https://api.example") as client:
        response = client.get("/videos", params={"part": "id"})
    assert response.status_code == 200
    with pytest.raises(AssertionError) as info:
        mock.assert_query_params(0, part="snippet")
    message = str(info.value)
    assert "part" in message
    assert "'snippet'" in message
    assert "'id'" in message


def test_sequence_is_served_in_order_then_exhausts() -> None:
    mock = MockHTTP()
    mock.enqueue_json({"page": 1}).enqueue_json({"page": 2})
    with mock.client(base_url="https://api.example") as client:
        assert client.get("/feed").json() == {"page": 1}
        assert client.get("/feed").json() == {"page": 2}
        with pytest.raises(AssertionError, match="no canned responses left"):
            client.get("/feed")
    assert mock.request_count == 3


def test_repeat_last_serves_the_last_outcome_again() -> None:
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json({"ok": True})
    with mock.client(base_url="https://api.example") as client:
        for _ in range(3):
            assert client.get("/ping").json() == {"ok": True}
    assert mock.request_count == 3


def test_rate_limit_carries_retry_after() -> None:
    mock = MockHTTP()
    mock.enqueue_rate_limit(retry_after="30")
    with mock.client(base_url="https://api.example") as client:
        response = client.get("/quota")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert response.json() == {"error": "rate_limit_exceeded"}


def test_server_error_carries_a_generic_body() -> None:
    mock = MockHTTP()
    mock.enqueue_server_error()
    with mock.client(base_url="https://api.example") as client:
        response = client.get("/anything")
    assert response.status_code == 500
    assert response.json() == {"error": "internal_server_error"}


def test_timeout_raises_the_httpx_exception_a_real_client_would() -> None:
    mock = MockHTTP()
    mock.enqueue_timeout(kind="read")
    with (
        mock.client(base_url="https://api.example") as client,
        pytest.raises(httpx.ReadTimeout),
    ):
        client.get("/slow")


def test_connect_timeout_kind_is_selectable() -> None:
    mock = MockHTTP()
    mock.enqueue_timeout(kind="connect")
    with (
        mock.client(base_url="https://api.example") as client,
        pytest.raises(httpx.ConnectTimeout),
    ):
        client.get("/unreachable")


def test_malformed_body_fails_the_consumer_parse() -> None:
    mock = MockHTTP()
    mock.enqueue_malformed_json()
    with mock.client(base_url="https://api.example") as client:
        response = client.get("/broken")
        assert response.status_code == 200
        with pytest.raises(ValueError):
            response.json()


def test_request_headers_and_body_are_recorded() -> None:
    mock = MockHTTP()
    mock.enqueue_json({}, status=201)
    with mock.client(base_url="https://api.example") as client:
        client.post("/things", json={"name": "x"}, headers={"Authorization": "Bearer token"})
    recorded = mock.request()
    assert recorded.headers["authorization"] == "Bearer token"
    assert recorded.json() == {"name": "x"}


def test_request_before_any_call_fails_clearly() -> None:
    with pytest.raises(AssertionError, match="no requests were made"):
        MockHTTP().request()


def test_install_routes_clients_the_module_constructs(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = MockHTTP()
    mock.enqueue_json({"ok": True})
    mock.install(monkeypatch)
    client = httpx.Client()
    try:
        response = client.get("https://api.example/own-client")
    finally:
        client.close()
    assert response.json() == {"ok": True}
    assert mock.request(0).path == "/own-client"


@pytest.mark.asyncio
async def test_async_clients_share_the_same_recording() -> None:
    mock = MockHTTP()
    mock.enqueue_json({"a": 1})
    async with mock.async_client(base_url="https://api.example") as client:
        response = await client.get("/async")
    assert response.json() == {"a": 1}
    mock.assert_request(0, method="GET", path="/async")
