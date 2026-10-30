# services/market_intelligence/connectors/spotify.py
"""§4.3 Spotify metadata as an optional secondary signal.

February 2026 removed popularity fields from several responses and restricted playlist
item access, so this connector reads metadata only and **never** a popularity field:
the opportunity score must not depend on something the provider no longer returns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType
from .base import ConnectorError, FetchedObject, FetchResult, hash_request_context

__all__ = ["SpotifyConnector", "SpotifyCredentials", "TrackStats", "parse_track_stats"]

BASE_URL = "https://api.spotify.com/v1"
_BANNED_FIELDS = ("popularity",)


@dataclass(frozen=True, slots=True)
class SpotifyCredentials:
    access_token: str

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(frozen=True, slots=True)
class TrackStats:
    track_id: str
    name: str | None
    artist_names: tuple[str, ...]
    duration_seconds: float | None


class SpotifyConnector:
    provider_name = ProviderName.SPOTIFY_WEB_API

    def __init__(self, client: httpx.Client, credentials: SpotifyCredentials,
                 *, base_url: str = BASE_URL) -> None:
        self._client = client
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")

    def fetch_tracks(self, track_ids: Sequence[str]) -> FetchResult:
        if not track_ids:
            raise ConnectorError(ConnectorErrorClass.INVALID_REQUEST,
                                 "fetch_tracks called without ids")
        # February 2026 removed Get Several Tracks (GET /tracks); Get Track remains.
        tracks = []
        for track_id in track_ids:
            response = self._client.get(f"{self._base_url}/tracks/{track_id}",
                                        headers=self._credentials.headers())
            if response.status_code >= 400:
                raise ConnectorError(_error_class(response.status_code),
                                     f"spotify returned HTTP {response.status_code}")
            tracks.append(response.json())
        objects = tuple(
            FetchedObject(provider_object_type=ProviderObjectType.TRACK,
                          provider_object_id=str(track["id"]),
                          observed_at=_parse_release(track), payload=track)
            for track in tracks
            if track and track.get("id")
        )
        if any(field in obj.payload for obj in objects for field in _BANNED_FIELDS):
            raise ConnectorError(
                ConnectorErrorClass.SCHEMA_CHANGE,
                "spotify returned a popularity field the 2026 API removed; the score must "
                "not depend on it (PHASE-P1 §4.3)",
            )
        return FetchResult(objects=objects, quota_units=0,
                           request_context_hash=hash_request_context(
                               self.provider_name.value, "tracks", {"ids": ",".join(track_ids)}))


def _error_class(status: int) -> ConnectorErrorClass:
    if status in (401, 403):
        return ConnectorErrorClass.AUTH_FAILURE
    if status == 404:
        return ConnectorErrorClass.NOT_FOUND
    if status == 429:
        return ConnectorErrorClass.RATE_LIMIT
    if status >= 500:
        return ConnectorErrorClass.PROVIDER_5XX
    return ConnectorErrorClass.INVALID_REQUEST


def parse_track_stats(fetch: FetchResult) -> tuple[TrackStats, ...]:
    return tuple(
        TrackStats(
            track_id=obj.provider_object_id,
            name=obj.payload.get("name"),
            artist_names=tuple(artist.get("name", "") for artist in obj.payload.get("artists", [])),
            duration_seconds=(
                float(obj.payload["duration_ms"]) / 1000.0
                if obj.payload.get("duration_ms") is not None else None
            ),
        )
        for obj in fetch.objects
    )


def _parse_release(track: Mapping[str, Any]) -> datetime:
    """Tracks carry a release date, not a timestamp: midnight UTC of that date.

    A missing or unparseable date is refused as a provider-schema failure. The old
    fallback returned 1970-01-01, which turned a schema problem into a
    plausible-looking observation instant — a date that was never observed
    (review fix 8).
    """
    release = str(track.get("album", {}).get("release_date") or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(release, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ConnectorError(
        ConnectorErrorClass.SCHEMA_CHANGE,
        f"spotify track {track.get('id')!r} has no parseable release date "
        f"({release!r}); refusing to invent one",
    )
