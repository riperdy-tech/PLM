# services/market_intelligence/connectors/youtube.py
"""Official YouTube Data API v3 connector (§4.1, §10) — discovery and hydration.

No private analytics are ever requested or inferred (§10.3). Credentials are injected
as `Secret`s and never reach a URL, a payload, a log line or an exception message.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from random import Random
from typing import Any, Final

import httpx
from plm_platform.config import Secret

from ..collection.rate_limit import QuotaBudget, RateLimiter, RequestBudget
from ..collection.retries import RetryPolicy, classify_http_status, execute_with_retries
from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType
from .base import ConnectorError, FetchedObject, FetchResult, hash_request_context

__all__ = [
    "BASE_URL", "LIST_QUOTA_UNITS", "SEARCH_QUOTA_UNITS", "SearchHit",
    "YouTubeCredentials", "YouTubeDataAPIConnector", "parse_iso8601_duration",
    "parse_search_hits",
]

BASE_URL: Final[str] = "https://www.googleapis.com/youtube/v3"
SEARCH_QUOTA_UNITS: Final[int] = 100  # documented cost of one search.list call (§4.1)
LIST_QUOTA_UNITS: Final[int] = 1

_DURATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)
_MAX_IDS_PER_LIST_CALL: Final[int] = 50


@dataclass(frozen=True, slots=True)
class YouTubeCredentials:
    """API key auth or OAuth bearer auth; at least one must be present."""

    api_key: Secret | None = None
    access_token: Secret | None = None

    def headers(self) -> dict[str, str]:
        if self.access_token is not None:
            return {"Authorization": f"Bearer {self.access_token.get_secret_value()}"}
        if self.api_key is not None:
            return {"X-Goog-Api-Key": self.api_key.get_secret_value()}
        raise ConnectorError(ConnectorErrorClass.AUTH_FAILURE, "no YouTube credential configured")


@dataclass(frozen=True, slots=True)
class SearchHit:
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: datetime | None
    channel_title: str


def _parse_timestamp(value: str | None) -> datetime | None:
    """Provider timestamps are documented UTC; `Z` is normalised, naive means UTC."""
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_iso8601_duration(text: str | None) -> float | None:
    """`PT1H2M3S` → seconds. `None` for missing or non-time-only durations."""
    if not text:
        return None
    match = _DURATION_RE.match(text)
    if match is None:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return float(parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"])


def _int_or_none(value: Any) -> int | None:
    """Provider counts arrive as strings; absent stays `None`, never 0 (Review Focus 1)."""
    if value is None or value == "":
        return None
    return int(value)


class YouTubeDataAPIConnector:
    """One connector instance per run: it carries the run's budgets and rate limiter."""

    provider_name = ProviderName.YOUTUBE_DATA_API

    def __init__(
        self,
        *,
        client: httpx.Client,
        credentials: YouTubeCredentials,
        quota: QuotaBudget | None = None,
        request_budget: RequestBudget | None = None,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: Random | None = None,
        limiter: RateLimiter | None = None,
        base_url: str = BASE_URL,
        observed_at: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._quota = quota if quota is not None else QuotaBudget(10_000)
        self._requests = request_budget if request_budget is not None else RequestBudget(1_000)
        self._policy = policy if policy is not None else RetryPolicy()
        self._sleep = sleep
        self._rng = rng if rng is not None else Random()
        self._limiter = limiter
        self._base_url = base_url.rstrip("/")
        self._observed_at = observed_at if observed_at is not None else (lambda: datetime.now(UTC))
        self._request_count = 0
        self._quota_units_used = 0

    # -- public surface ---------------------------------------------------------

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def quota_units_used(self) -> int:
        return self._quota_units_used

    def search_videos(
        self,
        query: str,
        *,
        max_results: int = 10,
        published_after: datetime | None = None,
        page_token: str | None = None,
    ) -> FetchResult:
        params: dict[str, Any] = {
            "part": "snippet", "type": "video", "q": query, "maxResults": max_results,
            "pageToken": page_token,
            "publishedAfter": published_after.astimezone(UTC).isoformat()
            if published_after else None,
        }
        payload = self._get("/search", params, quota_units=SEARCH_QUOTA_UNITS,
                           operation="search.list")
        observed_at = self._observed_at()
        context = hash_request_context(
            self.provider_name.value, "search.list",
            {k: v for k, v in params.items() if k != "pageToken"},
        )
        objects = _search_objects(payload, observed_at)
        return FetchResult(
            objects=objects, request_context_hash=context, quota_units=SEARCH_QUOTA_UNITS,
            next_page_token=payload.get("nextPageToken"),
        )

    def iter_search_videos(
        self,
        query: str,
        *,
        max_results: int = 10,
        max_pages: int = 1,
        published_after: datetime | None = None,
    ) -> Iterator[FetchResult]:
        token: str | None = None
        for _ in range(max_pages):
            page = self.search_videos(
                query, max_results=max_results, published_after=published_after,
                page_token=token,
            )
            yield page
            token = page.next_page_token
            if not token:
                return

    # -- internals --------------------------------------------------------------

    def _get(
        self, path: str, params: Mapping[str, Any], *, quota_units: int, operation: str
    ) -> Mapping[str, Any]:
        self._quota.consume(quota_units, operation=f"{self.provider_name.value}.{operation}")
        self._requests.consume(operation=operation)
        self._quota_units_used += quota_units
        clean = {k: v for k, v in params.items() if v is not None}

        def attempt() -> Mapping[str, Any]:
            if self._limiter is not None:
                self._limiter.acquire()
            self._request_count += 1
            try:
                response = self._client.get(
                    f"{self._base_url}{path}", params=clean, headers=self._credentials.headers()
                )
            except httpx.TimeoutException as exc:
                raise ConnectorError(
                    ConnectorErrorClass.TRANSIENT_NETWORK, f"{operation} timed out"
                ) from exc
            except httpx.TransportError as exc:
                raise ConnectorError(
                    ConnectorErrorClass.TRANSIENT_NETWORK, f"{operation} transport failure"
                ) from exc
            return _parse_response(response, operation)

        return execute_with_retries(
            attempt, policy=self._policy, sleep=self._sleep, rng=self._rng
        )


def _parse_response(response: httpx.Response, operation: str) -> Mapping[str, Any]:
    if response.status_code >= 400:
        raise _error_for(response, operation)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorError(
            ConnectorErrorClass.PARSER_FAILURE, f"{operation} returned a body that is not JSON"
        ) from exc
    if not isinstance(payload, Mapping) or "items" not in payload:
        raise ConnectorError(
            ConnectorErrorClass.SCHEMA_CHANGE,
            f"{operation} response has no 'items': the provider schema moved (§43)",
        )
    return payload


def _error_for(response: httpx.Response, operation: str) -> ConnectorError:
    retry_after = response.headers.get("Retry-After")
    retry_after_seconds = float(retry_after) if retry_after and retry_after.isdigit() else None
    reason = ""
    try:
        body = response.json()
        errors = body.get("error", {}).get("errors", [])
        reason = errors[0].get("reason", "") if errors else ""
    except ValueError:
        pass
        # a non-JSON error body is normal (gateway pages); the status still decides
    if response.status_code == 403 and reason == "quotaExceeded":
        return ConnectorError(
            ConnectorErrorClass.QUOTA_EXCEEDED, f"{operation}: provider quota exhausted"
        )
    return ConnectorError(
        classify_http_status(response.status_code),
        f"{operation} failed with HTTP {response.status_code}",
        retry_after_seconds=retry_after_seconds,
    )


def _search_objects(payload: Mapping[str, Any], observed_at: datetime) -> tuple[FetchedObject, ...]:
    objects: list[FetchedObject] = []
    for item in payload.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id:
            continue  # channel/playlist results are not the discovery target (§10.1)
        objects.append(FetchedObject(
            provider_object_type=ProviderObjectType.SEARCH_RESULT_PAGE,
            provider_object_id=video_id, observed_at=observed_at, payload=item,
        ))
    return tuple(objects)


def parse_search_hits(fetch: FetchResult) -> tuple[SearchHit, ...]:
    hits: list[SearchHit] = []
    for obj in fetch.objects:
        snippet = obj.payload.get("snippet") or {}
        hits.append(SearchHit(
            video_id=obj.provider_object_id,
            channel_id=snippet.get("channelId", ""),
            title=snippet.get("title", ""),
            description=snippet.get("description", ""),
            published_at=_parse_timestamp(snippet.get("publishedAt")),
            channel_title=snippet.get("channelTitle", ""),
        ))
    return tuple(hits)
