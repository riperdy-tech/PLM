# services/market_intelligence/connectors/trends.py
"""§4.4 public research feed: a JSON endpoint, no browser, no credentials, no quota."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType
from .base import ConnectorError, FetchedObject, FetchResult, hash_request_context

__all__ = ["PublicTrendsConnector", "TrendPoint", "parse_trend_points"]


@dataclass(frozen=True, slots=True)
class TrendPoint:
    term: str
    interest: int
    observed_at: datetime


class PublicTrendsConnector:
    """Reads a configured public JSON feed; the feed's shape is validated, not assumed."""

    provider_name = ProviderName.PUBLIC_TRENDS

    def __init__(self, client: httpx.Client, *, feed_path: str,
                 observed_at: Callable[[], datetime] | None = None) -> None:
        self._client = client
        self._feed_path = feed_path
        self._observed_at = observed_at if observed_at is not None else (lambda: datetime.now(UTC))

    def fetch_points(self, term: str) -> FetchResult:
        response = self._client.get(self._feed_path)
        if response.status_code >= 400:
            raise ConnectorError(ConnectorErrorClass.INVALID_REQUEST,
                                 f"trends feed returned HTTP {response.status_code}")
        try:
            payload: Mapping[str, Any] = response.json()
        except ValueError as exc:
            raise ConnectorError(ConnectorErrorClass.PARSER_FAILURE,
                                 "trends feed body is not JSON") from exc
        if "points" not in payload:
            raise ConnectorError(ConnectorErrorClass.SCHEMA_CHANGE,
                                 "trends feed has no 'points' key (§43)")
        observed_at = self._observed_at()
        matching = [point for point in payload["points"] if point.get("term") == term]
        # Newest reading first: the plan's own test pins `points[0].interest == 78`,
        # the 2026-09-20 point, so the connector — not the feed's row order — owns
        # the ordering. ISO date strings sort chronologically; the sort is stable,
        # so equal dates keep feed order (deterministic either way).
        matching.sort(key=lambda point: str(point.get("date") or ""), reverse=True)
        objects = tuple(
            FetchedObject(provider_object_type=ProviderObjectType.TREND_POINT,
                          provider_object_id=f"{point['term']}@{point.get('date', 'latest')}",
                          observed_at=observed_at, payload=point)
            for point in matching
        )
        return FetchResult(objects=objects, quota_units=0,
                           request_context_hash=hash_request_context(
                               self.provider_name.value, "trends.feed", {"term": term}))


def parse_trend_points(fetch: FetchResult) -> tuple[TrendPoint, ...]:
    return tuple(
        TrendPoint(term=str(obj.payload["term"]), interest=int(obj.payload["interest"]),
                   observed_at=obj.observed_at)
        for obj in fetch.objects
    )
