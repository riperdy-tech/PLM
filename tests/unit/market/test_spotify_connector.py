# tests/unit/market/test_spotify_connector.py
"""Review fix 8: an unparseable release date must be refused, not invented.

The connector used to map any release date it could not parse to 1970-01-01, which
turned a provider-schema problem into a plausible-looking observation instant.
"""

from datetime import UTC, datetime

import pytest

from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.connectors.spotify import (
    SpotifyConnector,
    SpotifyCredentials,
)
from services.market_intelligence.domain.enums import ConnectorErrorClass


def connector(mock_http) -> SpotifyConnector:
    return SpotifyConnector(
        client=mock_http.client(base_url="https://api.spotify.com/v1"),
        credentials=SpotifyCredentials(access_token="test-token"),
    )


def _track(release_date) -> dict:
    track = {"id": "track-1", "name": "A", "artists": [], "duration_ms": 1000}
    if release_date is not None:
        track["album"] = {"release_date": release_date}
    return track


@pytest.mark.parametrize("release_date", [None, "", "not-a-date", "2026-13-40"])
def test_unparseable_release_dates_are_refused_not_faked_as_1970(mock_http, release_date) -> None:
    mock_http.enqueue_json(_track(release_date))
    with pytest.raises(ConnectorError) as failure:
        connector(mock_http).fetch_tracks(["track-1"])
    assert failure.value.error_class is ConnectorErrorClass.SCHEMA_CHANGE
    assert "release date" in failure.value.detail


@pytest.mark.parametrize(("release_date", "expected"),
                         [("2026-03-09", datetime(2026, 3, 9, tzinfo=UTC)),
                          ("2026-03", datetime(2026, 3, 1, tzinfo=UTC)),
                          ("2026", datetime(2026, 1, 1, tzinfo=UTC))])
def test_parseable_release_dates_still_become_the_observation_instant(
    mock_http, release_date, expected,
) -> None:
    mock_http.enqueue_json(_track(release_date))
    result = connector(mock_http).fetch_tracks(["track-1"])
    assert result.objects[0].observed_at == expected
