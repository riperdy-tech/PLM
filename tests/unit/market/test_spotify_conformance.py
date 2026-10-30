"""R6 step 1: the Spotify connector against docs-derived Web API responses.

Proves the connector calls Get Track (``GET /tracks/{id}``), which survived
Spotify's February 2026 changes (Get Several Tracks was removed), reads the
documented TrackObject, refuses the removed ``popularity`` field and maps the
documented error statuses. It does not prove Spotify still answers this way;
see ``tests/support/provider_fixtures.py``.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.connectors.spotify import (
    SpotifyConnector,
    SpotifyCredentials,
    parse_track_stats,
)
from services.market_intelligence.domain.enums import ConnectorErrorClass
from tests.support.http import MockHTTP
from tests.support.provider_fixtures import enqueue

P = "spotify"
DAY, YEAR = "11dFghVXANMlKmJXsNCbNl", "2takcwOaAZWiXQijPHIx7B"


def _connector(mock: MockHTTP) -> SpotifyConnector:
    return SpotifyConnector(client=mock.client(),
                            credentials=SpotifyCredentials(access_token="tok"))


def test_each_track_is_one_get_track_call(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "track_day", "track_year")
    result = _connector(mock_http).fetch_tracks([DAY, YEAR])
    assert [r.url for r in mock_http.requests] == [
        f"https://api.spotify.com/v1/tracks/{DAY}", f"https://api.spotify.com/v1/tracks/{YEAR}"]
    assert all(r.method == "GET" and r.headers["Authorization"] == "Bearer tok"
               for r in mock_http.requests)
    assert [o.observed_at for o in result.objects] == [
        datetime(2026, 8, 1, tzinfo=UTC), datetime(2019, 1, 1, tzinfo=UTC)]
    stats = parse_track_stats(result)
    assert [(s.name, s.artist_names, s.duration_seconds) for s in stats] == [
        ("2AM Seoul", ("NightLoop",), 212.0), ("Rain Tape", ("NightLoop",), 183.5)]


def test_the_removed_popularity_field_is_a_schema_change(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "track_with_popularity")
    with pytest.raises(ConnectorError) as raised:
        _connector(mock_http).fetch_tracks(["3n3Ppam7vgaVa1iaRUc9Lp"])
    assert raised.value.error_class is ConnectorErrorClass.SCHEMA_CHANGE


@pytest.mark.parametrize(("error", "expected"), [
    ("error_400", ConnectorErrorClass.INVALID_REQUEST),
    ("error_401", ConnectorErrorClass.AUTH_FAILURE),
    ("error_403", ConnectorErrorClass.AUTH_FAILURE),
    ("error_404", ConnectorErrorClass.NOT_FOUND),
    ("error_429", ConnectorErrorClass.RATE_LIMIT),
])
def test_documented_errors_map_to_their_class(mock_http: MockHTTP, error: str,
                                              expected: ConnectorErrorClass) -> None:
    enqueue(mock_http, P, error)
    with pytest.raises(ConnectorError) as raised:
        _connector(mock_http).fetch_tracks([DAY])
    assert raised.value.error_class is expected


def test_a_failure_part_way_returns_nothing(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "track_day", "error_404")
    with pytest.raises(ConnectorError):
        _connector(mock_http).fetch_tracks([DAY, "missing"])
