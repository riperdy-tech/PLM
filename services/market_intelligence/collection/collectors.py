# services/market_intelligence/collection/collectors.py
"""§10 collection orchestration: connector → raw store → normalize → entities → observations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from minio import Minio
from plm_contracts import Tri
from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..connectors.base import ConnectorError, FetchedObject, FetchResult
from ..connectors.youtube import (
    YouTubeDataAPIConnector,
    parse_search_hits,
    parse_video_stats,
)
from ..domain.entities import CollectionRunCounters
from ..domain.enums import CollectionRunStatus, EntityType, ProviderObjectType
from ..normalization.canonical import (
    channel_canonical_url,
    is_valid_channel_id,
    normalize_text,
    video_canonical_url,
)
from ..normalization.youtube import (
    NormalizationOutcome,
    normalize_channel_object,
    normalize_search_hit,
    normalize_video_object,
)
from ..storage import repositories
from ..storage.raw_store import StoredRawObservation, store_raw_observation
from .rate_limit import QuotaExceededError, RequestBudgetExceededError

__all__ = [
    "ChannelHydration", "CollectionRunSummary", "collect_query", "fetch_channel_subscribers",
    "hydrate_and_store",
]

_MAX_CHANNEL_IDS_PER_CALL: Final[int] = 50  # channels.list's documented id limit


@dataclass(frozen=True, slots=True)
class CollectionRunSummary:
    run_id: str
    raw_count: int
    normalized_count: int
    quarantined_count: int
    error_classes: tuple[str, ...] = ()
    status: CollectionRunStatus = CollectionRunStatus.RUNNING

    def counters(self, *, requests: int, successes: int, rate_limits: int) -> CollectionRunCounters:
        return CollectionRunCounters(
            request_count=requests, success_count=successes, error_count=len(self.error_classes),
            rate_limit_count=rate_limits, raw_observation_count=self.raw_count,
            normalized_observation_count=self.normalized_count,
            quarantined_count=self.quarantined_count, status=self.status,
        )


def collect_query(
    session: Session, *, connector: YouTubeDataAPIConnector, source_id: str, query_id: str,
    query_text: str, run_id: str, settings: MarketSettings, now: datetime,
    storage_client: Minio | None = None,
) -> CollectionRunSummary:
    """One discovery pass for one query: search → store raw → hydrate → normalize."""
    raw_count = 0
    normalized_count = 0
    quarantined = 0
    error_classes: list[str] = []
    try:
        page = connector.search_videos(
            query_text, max_results=settings.max_results_per_search,
            region_code=settings.search_region_code,
            relevance_language=settings.search_relevance_language,
        )
    except ConnectorError as exc:
        return CollectionRunSummary(run_id=run_id, raw_count=0, normalized_count=0,
                                    quarantined_count=0, error_classes=(exc.error_class.value,),
                                    status=CollectionRunStatus.PARTIAL)
    for fetched in page.objects:
        raw = _store(session, run_id=run_id, source_id=source_id, fetched=fetched,
                     context=page.request_context_hash, storage_client=storage_client)
        raw_count += 1 if raw.created else 0
        hit = parse_search_hits(_single(fetched, page.request_context_hash))[0]
        entity_id = repositories.resolve_entity(
            session, entity_type=EntityType.VIDEO, source_id=source_id,
            provider_object_type=ProviderObjectType.VIDEO,
            provider_object_id=hit.video_id, canonical_name=hit.title or hit.video_id,
            normalized_name=normalize_text(hit.title or hit.video_id),
            canonical_url=video_canonical_url(hit.video_id), observed_at=now,
        )
        outcome = normalize_search_hit(fetched, entity_id=entity_id, source_id=source_id,
                                       raw_observation_id=raw.raw_observation_id,
                                       observed_at=now)
        normalized_count, quarantined = _persist_outcome(
            session, outcome, raw_observation_id=raw.raw_observation_id, run_id=run_id,
            normalized_count=normalized_count, quarantined=quarantined,
        )
    video_ids = [obj.provider_object_id for obj in page.objects]
    if video_ids:
        hydration = hydrate_and_store(
            session, connector=connector, source_id=source_id, run_id=run_id,
            video_ids=video_ids, settings=settings, now=now, storage_client=storage_client,
        )
        raw_count += hydration.raw_count
        normalized_count += hydration.normalized_count
        quarantined += hydration.quarantined_count
        error_classes.extend(hydration.error_classes)
    status = CollectionRunStatus.PARTIAL if (error_classes or quarantined) \
        else CollectionRunStatus.SUCCEEDED
    return CollectionRunSummary(run_id=run_id, raw_count=raw_count,
                                normalized_count=normalized_count,
                                quarantined_count=quarantined,
                                error_classes=tuple(error_classes), status=status)


@dataclass(frozen=True, slots=True)
class ChannelHydration:
    """What one batch's `channels.list` produced.

    `subscribers` holds an entry only for channels the provider returned; a channel
    that was not fetched, not returned, hid its count or failed a quality gate has
    no entry and stays unknown downstream — never a zero audience.
    """

    subscribers: dict[str, Tri[float]]
    raw_count: int
    quarantined_count: int
    error_classes: tuple[str, ...]
    budget_error: QuotaExceededError | RequestBudgetExceededError | None = None


def fetch_channel_subscribers(
    session: Session, *, connector: YouTubeDataAPIConnector, source_id: str, run_id: str,
    channel_ids: Sequence[str], now: datetime, storage_client: Minio | None = None,
) -> ChannelHydration:
    """§11.3: one `channels.list` call per batch of up to 50 distinct channels.

    Each channel payload is stored raw (provenance for the subscriber count) and passes
    the same §24 gates as any provider record: a negative or otherwise invalid channel
    record is quarantined with its reason code, and its subscribers stay unknown.
    A connector error leaves the affected channels unknown and is reported. A run
    budget error stops further channel calls and is handed back to the caller, who
    re-raises it once the batch's video observations are safe.
    """
    wanted = sorted({channel for channel in channel_ids if is_valid_channel_id(channel)})
    subscribers: dict[str, Tri[float]] = {}
    raw_count = 0
    quarantined = 0
    errors: list[str] = []
    for offset in range(0, len(wanted), _MAX_CHANNEL_IDS_PER_CALL):
        batch = wanted[offset:offset + _MAX_CHANNEL_IDS_PER_CALL]
        try:
            fetch = connector.fetch_channels(batch)
        except ConnectorError as exc:
            errors.append(exc.error_class.value)
            continue
        except (QuotaExceededError, RequestBudgetExceededError) as exc:
            return ChannelHydration(subscribers=subscribers, raw_count=raw_count,
                                    quarantined_count=quarantined, error_classes=tuple(errors),
                                    budget_error=exc)
        requested = set(batch)
        for fetched in fetch.objects:
            if fetched.provider_object_id not in requested:
                continue  # not what was asked for: never attributed to a channel
            raw = _store(session, run_id=run_id, source_id=source_id, fetched=fetched,
                         context=fetch.request_context_hash, storage_client=storage_client)
            raw_count += 1 if raw.created else 0
            snippet = fetched.payload.get("snippet") or {}
            title = snippet.get("title") or fetched.provider_object_id
            entity_id = repositories.resolve_entity(
                session, entity_type=EntityType.CHANNEL, source_id=source_id,
                provider_object_type=ProviderObjectType.CHANNEL,
                provider_object_id=fetched.provider_object_id, canonical_name=title,
                normalized_name=normalize_text(title),
                canonical_url=channel_canonical_url(fetched.provider_object_id),
                observed_at=now,
            )
            outcome = normalize_channel_object(
                fetched, entity_id=entity_id, source_id=source_id,
                raw_observation_id=raw.raw_observation_id, observed_at=now,
            )
            if outcome.quarantine is not None:
                record = replace(outcome.quarantine, collection_run_id=run_id,
                                 raw_observation_id=raw.raw_observation_id)
                repositories.quarantine_record(session, record)
                quarantined += 1
                continue
            if outcome.observation is not None and outcome.observation.subscriber_count.is_known:
                subscribers[fetched.provider_object_id] = outcome.observation.subscriber_count
    return ChannelHydration(subscribers=subscribers, raw_count=raw_count,
                            quarantined_count=quarantined, error_classes=tuple(errors))


def hydrate_and_store(
    session: Session, *, connector: YouTubeDataAPIConnector, source_id: str, run_id: str,
    video_ids: list[str], settings: MarketSettings, now: datetime,
    storage_client: Minio | None = None,
) -> CollectionRunSummary:
    """videos.list + channels.list hydration: metric-bearing observations with audience.

    The batch's distinct channels are fetched once, and each video observation carries
    its channel id and that channel's subscriber count (§11.3/§17.3 demand). A channel
    the batch could not fetch leaves the count unknown on its videos.
    """
    raw_count = 0
    normalized_count = 0
    quarantined = 0
    error_classes: list[str] = []
    try:
        fetch = connector.fetch_videos(video_ids)
    except ConnectorError as exc:
        return CollectionRunSummary(run_id=run_id, raw_count=0, normalized_count=0,
                                    quarantined_count=0, error_classes=(exc.error_class.value,),
                                    status=CollectionRunStatus.PARTIAL)
    videos = [(fetched, parse_video_stats(_single(fetched, fetch.request_context_hash))[0])
              for fetched in fetch.objects]
    channels = fetch_channel_subscribers(
        session, connector=connector, source_id=source_id, run_id=run_id,
        channel_ids=[stats.channel_id for _fetched, stats in videos if stats.channel_id],
        now=now, storage_client=storage_client,
    )
    raw_count += channels.raw_count
    quarantined += channels.quarantined_count
    error_classes.extend(channels.error_classes)
    for fetched, stats in videos:
        raw = _store(session, run_id=run_id, source_id=source_id, fetched=fetched,
                     context=fetch.request_context_hash, storage_client=storage_client)
        raw_count += 1 if raw.created else 0
        entity_id = repositories.resolve_entity(
            session, entity_type=EntityType.VIDEO, source_id=source_id,
            provider_object_type=ProviderObjectType.VIDEO,
            provider_object_id=stats.video_id, canonical_name=stats.title or stats.video_id,
            normalized_name=normalize_text(stats.title or stats.video_id),
            canonical_url=video_canonical_url(stats.video_id), observed_at=now,
        )
        subscribers = channels.subscribers.get(stats.channel_id) if stats.channel_id else None
        outcome = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                         raw_observation_id=raw.raw_observation_id,
                                         observed_at=now, channel_subscribers=subscribers)
        normalized_count, quarantined = _persist_outcome(
            session, outcome, raw_observation_id=raw.raw_observation_id, run_id=run_id,
            normalized_count=normalized_count, quarantined=quarantined,
        )
    if channels.budget_error is not None:
        # The batch's videos are stored; the run's budget handling (§27) takes over.
        raise channels.budget_error
    status = CollectionRunStatus.PARTIAL if (error_classes or quarantined) \
        else CollectionRunStatus.SUCCEEDED
    return CollectionRunSummary(run_id=run_id, raw_count=raw_count,
                                normalized_count=normalized_count,
                                quarantined_count=quarantined, error_classes=tuple(error_classes),
                                status=status)


def _store(session: Session, *, run_id: str, source_id: str, fetched: FetchedObject,
           context: str, storage_client: Minio | None = None) -> StoredRawObservation:
    """Store the raw object; `store_raw_observation` records `storage_bytes` itself."""
    return store_raw_observation(
        session, run_id=run_id, source_id=source_id, fetched=fetched,
        request_context_hash=context, client=storage_client,
    )


def _single(fetched: FetchedObject, context: str) -> FetchResult:
    return FetchResult(objects=(fetched,), request_context_hash=context)


def _persist_outcome(
    session: Session, outcome: NormalizationOutcome, *, raw_observation_id: str, run_id: str,
    normalized_count: int, quarantined: int,
) -> tuple[int, int]:
    """One drafted observation → one row; one failed draft → one traced quarantine row.

    A quarantined record keeps its evidence (source, raw observation) and gains the
    run it belonged to, so §24's trace survives; nothing is ever dropped silently.
    """
    if outcome.observation is not None:
        repositories.insert_normalized_observation(session, outcome.observation)
        return normalized_count + 1, quarantined
    if outcome.quarantine is not None:
        record = outcome.quarantine
        if record.collection_run_id is None:
            record = replace(record, collection_run_id=run_id)
        if record.raw_observation_id is None:
            record = replace(record, raw_observation_id=raw_observation_id)
        repositories.quarantine_record(session, record)
        return normalized_count, quarantined + 1
    return normalized_count, quarantined
