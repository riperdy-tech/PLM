# services/market_intelligence/storage/repositories.py
"""Typed helpers over the `market` schema. Every function takes a Session whose
search_path is the `market` schema (`plm_platform.db.scoped_session("market")`)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from plm_contracts import Tri
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.entities import CollectionRunCounters, NormalizedObservation, QuarantinedRecord
from ..domain.enums import (
    CollectionRunStatus,
    ConnectorType,
    EntityStatus,
    EntityType,
    ProviderName,
    ProviderObjectType,
)
from ..domain.value_objects import tri_from_optional
from ..normalization.youtube import QUARANTINE_REASON_CODES, NormalizedObservationDraft
from .models import (
    collection_runs,
    entities,
    entity_identities,
    observations,
    quarantined_records,
    sources,
)

__all__ = [
    "add_run_cost", "count_quarantined", "finish_collection_run",
    "insert_normalized_observation", "load_entity", "load_observations", "load_run",
    "quarantine_record", "resolve_entity", "start_collection_run", "upsert_source",
]


def upsert_source(
    session: Session,
    *,
    provider_name: ProviderName,
    connector_type: ConnectorType,
    base_identifier: str,
    configuration_version: str,
) -> str:
    """One row per provider (§7.1); secrets never live here."""
    existing = session.execute(
        sa.select(sources.c.id).where(sources.c.provider_name == provider_name.value)
    ).scalar_one_or_none()
    if existing is not None:
        session.execute(
            sa.update(sources).where(sources.c.id == existing).values(
                connector_type=connector_type.value,
                base_identifier=base_identifier,
                configuration_version=configuration_version,
                updated_at=sa.func.now(),
            )
        )
        return str(existing)
    source_id = _new_id()
    session.execute(sa.insert(sources).values(
        id=source_id, provider_name=provider_name.value,
        connector_type=connector_type.value, base_identifier=base_identifier,
        status="active", configuration_version=configuration_version,
    ))
    return source_id


def start_collection_run(
    session: Session,
    *,
    source_id: str,
    query_id: str | None,
    software_version: str,
    started_at: datetime,
) -> str:
    """§7.3: every collection attempt has a run record, starting `running`."""
    run_id = _new_id()
    session.execute(sa.insert(collection_runs).values(
        id=run_id, source_id=source_id, query_id=query_id, started_at=started_at,
        status=CollectionRunStatus.RUNNING.value, software_version=software_version,
    ))
    return run_id


def finish_collection_run(
    session: Session,
    run_id: str,
    *,
    status: CollectionRunStatus,
    counters: CollectionRunCounters,
    error_summary: Mapping[str, Any] | None = None,
    finished_at: datetime | None = None,
) -> None:
    """A run that lost records ends `partial`, never `succeeded` (§24, §43)."""
    if status is CollectionRunStatus.SUCCEEDED and (
        counters.error_count or counters.quarantined_count
    ):
        raise ValueError(
            "a run with errors or quarantined records must finish as partial, not succeeded"
        )
    session.execute(sa.update(collection_runs).where(collection_runs.c.id == run_id).values(
        status=status.value,
        finished_at=finished_at if finished_at is not None else sa.func.now(),
        request_count=counters.request_count, success_count=counters.success_count,
        error_count=counters.error_count, rate_limit_count=counters.rate_limit_count,
        raw_observation_count=counters.raw_observation_count,
        normalized_observation_count=counters.normalized_observation_count,
        error_summary=dict(error_summary) if error_summary else None,
    ))


def add_run_cost(
    session: Session,
    run_id: str,
    *,
    api_calls: int = 0,
    llm_tokens: int = 0,
    llm_cost_estimate: float = 0.0,
    storage_bytes: int = 0,
) -> None:
    """§42: costs attach to `collection_run_id` so P9 can price market research."""
    if min(api_calls, llm_tokens, storage_bytes) < 0 or llm_cost_estimate < 0:
        raise ValueError("costs cannot be negative")
    session.execute(sa.update(collection_runs).where(collection_runs.c.id == run_id).values(
        cost_api_calls=collection_runs.c.cost_api_calls + api_calls,
        cost_llm_tokens=collection_runs.c.cost_llm_tokens + llm_tokens,
        cost_llm_cost_estimate=collection_runs.c.cost_llm_cost_estimate + llm_cost_estimate,
        cost_storage_bytes=collection_runs.c.cost_storage_bytes + storage_bytes,
    ))


def load_run(session: Session, run_id: str) -> Mapping[str, Any] | None:
    row = session.execute(
        sa.select(collection_runs).where(collection_runs.c.id == run_id)
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def resolve_entity(
    session: Session,
    *,
    entity_type: EntityType,
    source_id: str,
    provider_object_type: ProviderObjectType,
    provider_object_id: str,
    canonical_name: str,
    normalized_name: str,
    canonical_url: str | None,
    observed_at: datetime,
    language: str | None = None,
    country_code: str | None = None,
) -> str:
    """§22: provider ids are canonical when available; one entity, many sightings."""
    identity = session.execute(
        sa.select(entity_identities.c.entity_id).where(
            entity_identities.c.source_id == source_id,
            entity_identities.c.provider_object_type == provider_object_type.value,
            entity_identities.c.provider_object_id == provider_object_id,
        )
    ).scalar_one_or_none()
    if identity is not None:
        session.execute(sa.update(entity_identities).where(
            entity_identities.c.entity_id == identity,
            entity_identities.c.provider_object_type == provider_object_type.value,
            entity_identities.c.provider_object_id == provider_object_id,
        ).values(last_seen_at=observed_at))
        session.execute(sa.update(entities).where(entities.c.id == identity).values(
            last_seen_at=observed_at,
            canonical_name=canonical_name,
            normalized_name=normalized_name,
        ))
        return str(identity)

    entity_id = _new_id()
    session.execute(sa.insert(entities).values(
        id=entity_id, entity_type=entity_type.value, canonical_name=canonical_name,
        normalized_name=normalized_name, canonical_url=canonical_url, language=language,
        country_code=country_code, first_seen_at=observed_at, last_seen_at=observed_at,
        status=EntityStatus.ACTIVE.value,
    ))
    session.execute(sa.insert(entity_identities).values(
        id=_new_id(), entity_id=entity_id, source_id=source_id,
        provider_object_type=provider_object_type.value, provider_object_id=provider_object_id,
        first_seen_at=observed_at, last_seen_at=observed_at,
    ))
    return entity_id


def insert_normalized_observation(
    session: Session, draft: NormalizedObservationDraft | None,
) -> tuple[str, bool]:
    """One row per (entity, source, observed_at); a repeat is idempotent (§7.6/AC-03)."""
    if draft is None:
        raise ValueError("cannot insert a draft that was quarantined")
    values = {
        "id": _new_id(), "entity_id": draft.entity_id, "source_id": draft.source_id,
        "raw_observation_id": draft.raw_observation_id, "observed_at": draft.observed_at,
        "published_at": draft.published_at,
        "views": _tri_value(draft.views), "likes": _tri_value(draft.likes),
        "comments": _tri_value(draft.comments),
        "subscriber_count": _tri_value(draft.subscriber_count),
        "video_duration_seconds": _tri_value(draft.video_duration_seconds),
        "playlist_item_count": _tri_value(draft.playlist_item_count),
        "channel_video_count": _tri_value(draft.channel_video_count),
        "text_title": draft.text_title, "text_description": draft.text_description,
        "country_signal": draft.country_signal, "language_signal": draft.language_signal,
    }
    try:
        with session.begin_nested():
            session.execute(sa.insert(observations).values(**values))
    except IntegrityError:
        existing = session.execute(
            sa.select(observations.c.id).where(
                observations.c.entity_id == draft.entity_id,
                observations.c.source_id == draft.source_id,
                observations.c.observed_at == draft.observed_at,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return str(existing), False
    return values["id"], True


def load_observations(
    session: Session, *, source_id: str | None = None, entity_id: str | None = None,
    since: datetime | None = None,
) -> list[NormalizedObservation]:
    """Read back as Tri-typed domain records: NULL becomes unknown here, once."""
    statement = sa.select(observations).order_by(observations.c.observed_at)
    if source_id is not None:
        statement = statement.where(observations.c.source_id == source_id)
    if entity_id is not None:
        statement = statement.where(observations.c.entity_id == entity_id)
    if since is not None:
        statement = statement.where(observations.c.observed_at >= since)
    records: list[NormalizedObservation] = []
    for row in session.execute(statement).mappings():
        records.append(NormalizedObservation(
            entity_id=str(row["entity_id"]), source_id=str(row["source_id"]),
            raw_observation_id=str(row["raw_observation_id"]), observed_at=row["observed_at"],
            published_at=row["published_at"],
            views=tri_from_optional(row["views"]), likes=tri_from_optional(row["likes"]),
            comments=tri_from_optional(row["comments"]),
            subscriber_count=tri_from_optional(row["subscriber_count"]),
            video_duration_seconds=tri_from_optional(row["video_duration_seconds"]),
            playlist_item_count=tri_from_optional(row["playlist_item_count"]),
            channel_video_count=tri_from_optional(row["channel_video_count"]),
            text_title=row["text_title"], text_description=row["text_description"],
            country_signal=row["country_signal"], language_signal=row["language_signal"],
        ))
    return records


def quarantine_record(session: Session, record: QuarantinedRecord) -> str:
    """§24: quarantine with a reason code instead of dropping silently."""
    if record.reason_code not in QUARANTINE_REASON_CODES:
        raise ValueError(f"unknown quarantine reason code {record.reason_code!r}")
    record_id = _new_id()
    session.execute(sa.insert(quarantined_records).values(
        id=record_id, source_id=None, raw_observation_id=record.raw_observation_id,
        collection_run_id=record.collection_run_id, reason_code=record.reason_code,
        detail=record.detail, payload_uri=record.payload_uri,
    ))
    return record_id


def count_quarantined(session: Session, run_id: str | None) -> int:
    """Quarantined rows for one run; `run_id=None` counts every quarantined row."""
    statement = sa.select(sa.func.count()).select_from(quarantined_records)
    if run_id is not None:
        statement = statement.where(quarantined_records.c.collection_run_id == run_id)
    return int(session.execute(statement).scalar_one())


def load_entity(session: Session, entity_id: str) -> Mapping[str, Any] | None:
    row = session.execute(
        sa.select(entities).where(entities.c.id == entity_id)
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _tri_value(metric: Tri) -> int | float | None:
    """Tri → column value: unknown/not_ready are NULL, a verified zero is 0."""
    return metric.unwrap() if metric.is_known else None
