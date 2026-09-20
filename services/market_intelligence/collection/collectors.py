# services/market_intelligence/collection/collectors.py
"""§10 collection orchestration: connector → raw store → normalize → entities → observations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

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
from ..normalization.canonical import normalize_text, video_canonical_url
from ..normalization.youtube import (
    NormalizationOutcome,
    normalize_search_hit,
    normalize_video_object,
)
from ..storage import repositories
from ..storage.raw_store import store_raw_observation

__all__ = ["CollectionRunSummary", "collect_query", "hydrate_and_store"]


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
    storage_client=None,
) -> CollectionRunSummary:
    """One discovery pass for one query: search → store raw → hydrate → normalize."""
    raw_count = 0
    normalized_count = 0
    quarantined = 0
    error_classes: list[str] = []
    try:
        page = connector.search_videos(query_text, max_results=settings.max_results_per_search)
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


def hydrate_and_store(
    session: Session, *, connector: YouTubeDataAPIConnector, source_id: str, run_id: str,
    video_ids: list[str], settings: MarketSettings, now: datetime, storage_client=None,
) -> CollectionRunSummary:
    """videos.list hydration: metric-bearing observations for entities discovery created."""
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
    for fetched in fetch.objects:
        raw = _store(session, run_id=run_id, source_id=source_id, fetched=fetched,
                     context=fetch.request_context_hash, storage_client=storage_client)
        raw_count += 1 if raw.created else 0
        stats = parse_video_stats(_single(fetched, fetch.request_context_hash))[0]
        entity_id = repositories.resolve_entity(
            session, entity_type=EntityType.VIDEO, source_id=source_id,
            provider_object_type=ProviderObjectType.VIDEO,
            provider_object_id=stats.video_id, canonical_name=stats.title or stats.video_id,
            normalized_name=normalize_text(stats.title or stats.video_id),
            canonical_url=video_canonical_url(stats.video_id), observed_at=now,
        )
        outcome = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                         raw_observation_id=raw.raw_observation_id,
                                         observed_at=now)
        normalized_count, quarantined = _persist_outcome(
            session, outcome, raw_observation_id=raw.raw_observation_id, run_id=run_id,
            normalized_count=normalized_count, quarantined=quarantined,
        )
    status = CollectionRunStatus.PARTIAL if (error_classes or quarantined) \
        else CollectionRunStatus.SUCCEEDED
    return CollectionRunSummary(run_id=run_id, raw_count=raw_count,
                                normalized_count=normalized_count,
                                quarantined_count=quarantined, error_classes=tuple(error_classes),
                                status=status)


def _store(session: Session, *, run_id: str, source_id: str, fetched: FetchedObject,
           context: str, storage_client=None):
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
