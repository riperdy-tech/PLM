# services/market_intelligence/normalization/youtube.py
"""YouTube payload → normalised draft, with §24's data-quality gates.

A record that fails a gate is returned as a `quarantine` outcome and is *never*
silently repaired or dropped: the caller writes `market_quarantined_records`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from plm_contracts import Tri

from ..connectors.base import FetchedObject
from ..domain.entities import QuarantinedRecord
from ..domain.value_objects import tri_from_optional
from .canonical import is_valid_channel_id, is_valid_video_id

__all__ = [
    "QUARANTINE_REASON_CODES",
    "NormalizationOutcome",
    "NormalizedObservationDraft",
    "normalize_channel_object",
    "normalize_search_hit",
    "normalize_video_object",
]

QUARANTINE_REASON_CODES: Final[frozenset[str]] = frozenset({
    "malformed_provider_id", "negative_count", "impossible_timestamp_order",
    "negative_duration", "non_finite_number",
})


@dataclass(frozen=True, slots=True)
class NormalizedObservationDraft:
    """What `repositories.insert_normalized_observation` accepts (Tri metrics only)."""

    entity_id: str
    source_id: str
    raw_observation_id: str
    observed_at: datetime
    published_at: datetime | None
    views: Tri
    likes: Tri
    comments: Tri
    subscriber_count: Tri
    video_duration_seconds: Tri
    playlist_item_count: Tri
    channel_video_count: Tri
    text_title: str | None
    text_description: str | None
    country_signal: str | None
    language_signal: str | None


@dataclass(frozen=True, slots=True)
class NormalizationOutcome:
    observation: NormalizedObservationDraft | None = None
    quarantine: QuarantinedRecord | None = None


def normalize_video_object(
    fetched: FetchedObject, *, entity_id: str, source_id: str, raw_observation_id: str,
    observed_at: datetime,
) -> NormalizationOutcome:
    if not is_valid_video_id(fetched.provider_object_id):
        return _quarantined("malformed_provider_id",
                            f"video id {fetched.provider_object_id!r} is not 11 URL-safe chars",
                            fetched)
    snippet = fetched.payload.get("snippet") or {}
    statistics = fetched.payload.get("statistics") or {}
    details = fetched.payload.get("contentDetails") or {}
    published_at = _timestamp(snippet.get("publishedAt"))
    duration = _seconds(details.get("duration"))
    draft = NormalizedObservationDraft(
        entity_id=entity_id, source_id=source_id, raw_observation_id=raw_observation_id,
        observed_at=observed_at, published_at=published_at,
        views=tri_from_optional(_count(statistics.get("viewCount"))),
        likes=tri_from_optional(_count(statistics.get("likeCount"))),
        comments=tri_from_optional(_count(statistics.get("commentCount"))),
        subscriber_count=Tri.unknown(),  # videos.list never carries channel size
        video_duration_seconds=tri_from_optional(duration),
        playlist_item_count=Tri.unknown(), channel_video_count=Tri.unknown(),
        text_title=snippet.get("title"), text_description=snippet.get("description"),
        country_signal=None, language_signal=snippet.get("defaultLanguage"),
    )
    return _validated(draft, fetched)


def normalize_channel_object(
    fetched: FetchedObject, *, entity_id: str, source_id: str, raw_observation_id: str,
    observed_at: datetime,
) -> NormalizationOutcome:
    if not is_valid_channel_id(fetched.provider_object_id):
        return _quarantined("malformed_provider_id",
                            f"channel id {fetched.provider_object_id!r} is not UC + 22 chars",
                            fetched)
    snippet = fetched.payload.get("snippet") or {}
    statistics = fetched.payload.get("statistics") or {}
    draft = NormalizedObservationDraft(
        entity_id=entity_id, source_id=source_id, raw_observation_id=raw_observation_id,
        observed_at=observed_at, published_at=None,
        views=tri_from_optional(_count(statistics.get("viewCount"))),
        likes=Tri.unknown(), comments=Tri.unknown(),
        subscriber_count=tri_from_optional(_count(statistics.get("subscriberCount"))),
        video_duration_seconds=Tri.unknown(), playlist_item_count=Tri.unknown(),
        channel_video_count=tri_from_optional(_count(statistics.get("videoCount"))),
        text_title=snippet.get("title"), text_description=snippet.get("description"),
        country_signal=snippet.get("country"), language_signal=snippet.get("defaultLanguage"),
    )
    return _validated(draft, fetched)


def normalize_search_hit(
    fetched: FetchedObject, *, entity_id: str, source_id: str, raw_observation_id: str,
    observed_at: datetime,
) -> NormalizationOutcome:
    """Discovery sees text and a publish date only — every metric stays unknown."""
    snippet = fetched.payload.get("snippet") or {}
    if not is_valid_video_id(fetched.provider_object_id):
        return _quarantined("malformed_provider_id", "search result without a video id", fetched)
    draft = NormalizedObservationDraft(
        entity_id=entity_id, source_id=source_id, raw_observation_id=raw_observation_id,
        observed_at=observed_at, published_at=_timestamp(snippet.get("publishedAt")),
        views=Tri.unknown(), likes=Tri.unknown(), comments=Tri.unknown(),
        subscriber_count=Tri.unknown(), video_duration_seconds=Tri.unknown(),
        playlist_item_count=Tri.unknown(), channel_video_count=Tri.unknown(),
        text_title=snippet.get("title"), text_description=snippet.get("description"),
        country_signal=None, language_signal=None,
    )
    return _validated(draft, fetched)


def _validated(draft: NormalizedObservationDraft, fetched: FetchedObject) -> NormalizationOutcome:
    for name, metric in (
        ("views", draft.views), ("likes", draft.likes), ("comments", draft.comments),
        ("subscriber_count", draft.subscriber_count), ("channel_video_count",
                                                        draft.channel_video_count),
    ):
        if metric.is_known:
            value = metric.unwrap()
            if value < 0:
                return _quarantined("negative_count", f"{name}={value}", fetched)
    if draft.video_duration_seconds.is_known and draft.video_duration_seconds.unwrap() < 0:
        return _quarantined("negative_duration",
                            f"duration={draft.video_duration_seconds.unwrap()}", fetched)
    if draft.published_at is not None and draft.published_at > draft.observed_at:
        return _quarantined(
            "impossible_timestamp_order",
            f"published_at {draft.published_at.isoformat()} is after observed_at "
            f"{draft.observed_at.isoformat()}",
            fetched,
        )
    return NormalizationOutcome(observation=draft)


def _quarantined(reason: str, detail: str, fetched: FetchedObject) -> NormalizationOutcome:
    return NormalizationOutcome(quarantine=QuarantinedRecord(
        reason_code=reason, detail=detail, raw_observation_id=None,
        payload_uri=None,
    ))


def _count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    if number < 0:
        return number  # the gate above quarantines it with a reason code
    return number


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    from ..connectors.youtube import parse_iso8601_duration

    parsed = parse_iso8601_duration(value)
    if parsed is not None and not math.isfinite(parsed):
        return None
    return parsed
