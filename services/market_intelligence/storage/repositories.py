# services/market_intelligence/storage/repositories.py
"""Typed helpers over the `market` schema. Every function takes a Session whose
search_path is the `market` schema (`plm_platform.db.scoped_session("market")`).

Ids are *derived*, never minted: every row's id is a uuid5 over its stable identity,
so replaying the same inputs into a fresh schema reproduces the same ids — the
project determinism constraint (values reproducible from stored inputs plus a
version string).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from plm_contracts import Tri
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.entities import CollectionRunCounters, NormalizedObservation, QuarantinedRecord
from ..domain.enums import (
    ArmStatus,
    CollectionRunStatus,
    ConnectorType,
    EntityStatus,
    EntityType,
    ProviderName,
    ProviderObjectType,
    QueryType,
    TaxonomyDimension,
)
from ..domain.value_objects import tri_from_optional
from ..normalization.canonical import text_fingerprint
from ..normalization.youtube import (
    NORMALIZATION_VERSION,
    QUARANTINE_REASON_CODES,
    NormalizedObservationDraft,
)
from .models import (
    arms,
    classifications,
    collection_runs,
    entities,
    entity_identities,
    observations,
    quarantined_records,
    queries,
    seed_evidence,
    sources,
)

if TYPE_CHECKING:  # `taxonomy.arms` imports domain/validators only — no runtime cycle
    from ..taxonomy.arms import ArmDraft

__all__ = [
    "add_run_cost", "count_quarantined", "deterministic_id", "finish_collection_run",
    "insert_classification", "insert_normalized_observation", "insert_seed_evidence",
    "latest_classifications_for", "load_arms", "load_classification", "load_entity",
    "load_observations", "load_queries", "load_run", "load_seed_evidence",
    "observations_for_entities", "quarantine_record", "resolve_entity",
    "start_collection_run", "upsert_arm", "upsert_query", "upsert_source",
]

_ID_NAMESPACE: Final[UUID] = uuid5(NAMESPACE_URL, "plm:market:p1")
ENTITY_RESOLUTION_VERSION: Final[str] = "p1-entity-v1"
"""Entity ids carry the version of the resolution rules that bound them (§22)."""


def deterministic_id(*parts: str) -> str:
    """uuid5 over a row's stable identity parts, joined with `|`.

    Callers pass the inputs that *are* the row's identity (provider identity,
    observed timestamp, transformation/version tokens) — never anything mutable.
    """
    return str(uuid5(_ID_NAMESPACE, "|".join(parts)))


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
    source_id = deterministic_id("source", provider_name.value)
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
    run_id = deterministic_id(
        "collection_run", source_id, query_id or "", software_version,
        started_at.isoformat(),
    )
    try:
        with session.begin_nested():
            session.execute(sa.insert(collection_runs).values(
                id=run_id, source_id=source_id, query_id=query_id, started_at=started_at,
                status=CollectionRunStatus.RUNNING.value, software_version=software_version,
            ))
    except IntegrityError:
        # Re-starting the identical attempt (a replay) is the same run, not a second one.
        existing = session.execute(
            sa.select(collection_runs.c.id).where(collection_runs.c.id == run_id)
        ).scalar_one_or_none()
        if existing is None:
            raise
        return str(existing)
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
        # A sighting only rewrites state when it is at least as new as the last one:
        # a backfill must never regress a name or move `last_seen` backwards (§22).
        session.execute(sa.update(entity_identities).where(
            entity_identities.c.entity_id == identity,
            entity_identities.c.provider_object_type == provider_object_type.value,
            entity_identities.c.provider_object_id == provider_object_id,
            entity_identities.c.last_seen_at <= observed_at,
        ).values(last_seen_at=observed_at))
        session.execute(sa.update(entities).where(
            entities.c.id == identity,
            entities.c.last_seen_at <= observed_at,
        ).values(
            last_seen_at=observed_at,
            canonical_name=canonical_name,
            normalized_name=normalized_name,
        ))
        return str(identity)

    entity_id = deterministic_id(
        "entity", ENTITY_RESOLUTION_VERSION, entity_type.value, source_id,
        provider_object_type.value, provider_object_id,
    )
    session.execute(sa.insert(entities).values(
        id=entity_id, entity_type=entity_type.value, canonical_name=canonical_name,
        normalized_name=normalized_name, canonical_url=canonical_url, language=language,
        country_code=country_code, first_seen_at=observed_at, last_seen_at=observed_at,
        status=EntityStatus.ACTIVE.value,
    ))
    session.execute(sa.insert(entity_identities).values(
        id=deterministic_id(
            "entity_identity", source_id, provider_object_type.value, provider_object_id
        ),
        entity_id=entity_id, source_id=source_id,
        provider_object_type=provider_object_type.value, provider_object_id=provider_object_id,
        first_seen_at=observed_at, last_seen_at=observed_at,
    ))
    return entity_id


def insert_normalized_observation(
    session: Session, draft: NormalizedObservationDraft | None, *,
    normalization_version: str = NORMALIZATION_VERSION,
) -> tuple[str, bool]:
    """One row per (entity, source, observed_at); a repeat is idempotent (§7.6/AC-03)."""
    if draft is None:
        raise ValueError("cannot insert a draft that was quarantined")
    values = {
        "id": deterministic_id(
            "observation", draft.entity_id, draft.source_id, draft.observed_at.isoformat()
        ),
        "entity_id": draft.entity_id, "source_id": draft.source_id,
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
        "normalization_version": normalization_version,
        "not_ready_metrics": sorted(
            name for name in _OBSERVATION_METRIC_FIELDS if getattr(draft, name).is_not_ready
        ) or None,
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
    """Read back as Tri-typed domain records: NULL becomes unknown here, once.

    A metric the row records in `not_ready_metrics` comes back `not_ready`, not
    `unknown` — the distinction the Tri type exists to preserve.
    """
    statement = sa.select(observations).order_by(observations.c.observed_at)
    if source_id is not None:
        statement = statement.where(observations.c.source_id == source_id)
    if entity_id is not None:
        statement = statement.where(observations.c.entity_id == entity_id)
    if since is not None:
        statement = statement.where(observations.c.observed_at >= since)
    return _rows_to_observations(session.execute(statement).mappings())


def observations_for_entities(
    session: Session, entity_ids: Sequence[str], *, since: datetime | None = None,
) -> list[NormalizedObservation]:
    """The feature layer's read path; quarantined records have no row and cannot appear."""
    if not entity_ids:
        return []
    statement = (sa.select(observations)
                 .where(observations.c.entity_id.in_(list(entity_ids)))
                 .order_by(observations.c.observed_at))
    if since is not None:
        statement = statement.where(observations.c.observed_at >= since)
    return _rows_to_observations(session.execute(statement).mappings())


def _rows_to_observations(rows: Iterable[Mapping[str, Any]]) -> list[NormalizedObservation]:
    """One Tri mapping for every read path: NULL becomes unknown here, once.

    A metric the row records in `not_ready_metrics` comes back `not_ready`, not
    `unknown` — the distinction the Tri type exists to preserve.
    """
    records: list[NormalizedObservation] = []
    for row in rows:
        not_ready = frozenset(row["not_ready_metrics"] or ())
        records.append(NormalizedObservation(
            entity_id=str(row["entity_id"]), source_id=str(row["source_id"]),
            raw_observation_id=str(row["raw_observation_id"]), observed_at=row["observed_at"],
            published_at=row["published_at"],
            views=_tri_from_row(row, "views", not_ready),
            likes=_tri_from_row(row, "likes", not_ready),
            comments=_tri_from_row(row, "comments", not_ready),
            subscriber_count=_tri_from_row(row, "subscriber_count", not_ready),
            video_duration_seconds=_tri_from_row(row, "video_duration_seconds", not_ready),
            playlist_item_count=_tri_from_row(row, "playlist_item_count", not_ready),
            channel_video_count=_tri_from_row(row, "channel_video_count", not_ready),
            text_title=row["text_title"], text_description=row["text_description"],
            country_signal=row["country_signal"], language_signal=row["language_signal"],
        ))
    return records


def quarantine_record(session: Session, record: QuarantinedRecord) -> str:
    """§24: quarantine with a reason code instead of dropping silently."""
    if record.reason_code not in QUARANTINE_REASON_CODES:
        raise ValueError(f"unknown quarantine reason code {record.reason_code!r}")
    record_id = deterministic_id(
        "quarantine", record.source_id or "", record.raw_observation_id or "",
        record.collection_run_id or "", record.reason_code, record.detail,
        record.payload_uri or "",
    )
    try:
        with session.begin_nested():
            session.execute(sa.insert(quarantined_records).values(
                id=record_id, source_id=record.source_id,
                raw_observation_id=record.raw_observation_id,
                collection_run_id=record.collection_run_id, reason_code=record.reason_code,
                detail=record.detail, payload_uri=record.payload_uri,
            ))
    except IntegrityError:
        existing = session.execute(
            sa.select(quarantined_records.c.id).where(quarantined_records.c.id == record_id)
        ).scalar_one_or_none()
        if existing is None:
            raise
        return str(existing)
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
    """Tri → numeric column value: unknown and not_ready are NULL, verified zero is 0.

    `not_ready` is not lost: its name goes into `not_ready_metrics` (see
    `insert_normalized_observation`/`load_observations`).
    """
    return metric.unwrap() if metric.is_known else None


_OBSERVATION_METRIC_FIELDS: Final[tuple[str, ...]] = (
    "views", "likes", "comments", "subscriber_count", "video_duration_seconds",
    "playlist_item_count", "channel_video_count",
)


def _tri_from_row(row: Mapping[str, Any], name: str, not_ready: frozenset[str]) -> Tri:
    """NULL ⇒ unknown, *unless* the row records that metric as `not_ready`."""
    if row[name] is not None:
        return tri_from_optional(row[name])
    return Tri.not_ready() if name in not_ready else tri_from_optional(None)


def upsert_query(
    session: Session,
    *,
    source_id: str,
    query_text: str,
    query_type: QueryType,
    locale: str,
    country_code: str,
    language: str,
    seed_group: str,
    priority: int,
    query_version: str,
    parent_query_id: str | None = None,
    proposal: object | None = None,
) -> tuple[str, bool]:
    """One row per (source, normalised term). Stage B proposals carry their lineage."""
    fingerprint = text_fingerprint(query_text)
    existing = session.execute(
        sa.select(queries.c.id).where(
            queries.c.source_id == source_id, queries.c.query_fingerprint == fingerprint
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing), False
    query_id = deterministic_id("query", source_id, fingerprint)
    session.execute(sa.insert(queries).values(
        id=query_id, source_id=source_id, query_text=query_text,
        query_fingerprint=fingerprint, query_type=query_type.value, locale=locale,
        country_code=country_code, language=language, seed_group=seed_group,
        active=True, priority=priority, query_version=query_version,
        parent_query_id=parent_query_id,
        proposed_by_model=getattr(proposal, "proposed_by_model", None),
        model_name=getattr(proposal, "model_name", None),
        model_version=getattr(proposal, "model_version", None),
        proposal_confidence=getattr(proposal, "confidence", None),
    ))
    return query_id, True


def load_queries(
    session: Session, *, source_id: str | None = None, active_only: bool = True,
) -> list[Mapping[str, Any]]:
    statement = sa.select(queries).order_by(queries.c.priority.desc(), queries.c.query_text)
    if source_id is not None:
        statement = statement.where(queries.c.source_id == source_id)
    if active_only:
        statement = statement.where(queries.c.active.is_(True))
    return [dict(row) for row in session.execute(statement).mappings()]


def insert_seed_evidence(
    session: Session, *, query_id: str, raw_observation_ids: Sequence[str],
) -> int:
    """§8: every automatic seed stores the observations it was derived from."""
    inserted = 0
    for raw_id in raw_observation_ids:
        try:
            with session.begin_nested():
                session.execute(sa.insert(seed_evidence).values(
                    id=deterministic_id("seed_evidence", query_id, raw_id),
                    query_id=query_id, raw_observation_id=raw_id,
                ))
            inserted += 1
        except IntegrityError:
            continue  # the same observation already backs this seed
    return inserted


def load_seed_evidence(session: Session, query_id: str) -> list[str]:
    return [str(value) for value in session.execute(
        sa.select(seed_evidence.c.raw_observation_id)
        .where(seed_evidence.c.query_id == query_id)
        .order_by(seed_evidence.c.created_at)
    ).scalars()]


def insert_classification(
    session: Session,
    *,
    observation_id: str | None,
    entity_id: str | None,
    content_fingerprint: str,
    taxonomy_version: str,
    classifier_model: str,
    classifier_version: str,
    classification_prompt_version: str,
    classification_schema_version: str,
    dimensions: Mapping[str, Any],
    confidence: float,
    unmapped_terms: Sequence[str],
) -> str:
    """§29: every classification names the taxonomy, model, prompt and schema versions."""
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("classification confidence must be within 0–1")
    classification_id = deterministic_id(
        "classification", content_fingerprint, taxonomy_version
    )
    session.execute(sa.insert(classifications).values(
        id=classification_id, observation_id=observation_id, entity_id=entity_id,
        content_fingerprint=content_fingerprint, taxonomy_version=taxonomy_version,
        classifier_model=classifier_model, classifier_version=classifier_version,
        classification_prompt_version=classification_prompt_version,
        classification_schema_version=classification_schema_version,
        dimensions_json=dict(dimensions), confidence=confidence,
        unmapped_terms=list(unmapped_terms),
    ))
    return classification_id


def load_classification(
    session: Session, *, content_fingerprint: str, taxonomy_version: str,
) -> Mapping[str, Any] | None:
    row = session.execute(
        sa.select(classifications).where(
            classifications.c.content_fingerprint == content_fingerprint,
            classifications.c.taxonomy_version == taxonomy_version,
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def latest_classifications_for(
    session: Session, *, taxonomy_version: str, limit: int = 500,
) -> list[Mapping[str, Any]]:
    """Newest-first classification rows for one taxonomy version — the scorer's input."""
    statement = (
        sa.select(classifications)
        .where(classifications.c.taxonomy_version == taxonomy_version)
        .order_by(classifications.c.created_at.desc())
        .limit(limit)
    )
    return [dict(row) for row in session.execute(statement).mappings()]


def upsert_arm(session: Session, draft: ArmDraft, *, brand_id: str) -> tuple[str, bool]:
    """One row per fingerprint; an existing arm keeps its identity (Review Focus 4).

    The row id is derived from the fingerprint (uuid5), never minted: replaying the
    same classifications into a fresh schema reproduces the same arm row.
    """
    existing = session.execute(
        sa.select(arms.c.arm_id).where(arms.c.fingerprint == draft.fingerprint)
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing), False
    session.execute(sa.insert(arms).values(
        id=deterministic_id("arm", draft.fingerprint), brand_id=brand_id, arm_id=draft.arm_id,
        fingerprint=draft.fingerprint, status=ArmStatus.CANDIDATE.value,
        taxonomy_json={dimension.value: draft.profile.value_for(dimension)
                       for dimension in TaxonomyDimension},
        source_observation_ids=list(draft.source_observation_ids),
        support_count=draft.support_count,
    ))
    return draft.arm_id, True


def load_arms(session: Session, *, status: ArmStatus | None = None) -> list[Mapping[str, Any]]:
    statement = sa.select(arms).order_by(arms.c.arm_id)
    if status is not None:
        statement = statement.where(arms.c.status == status.value)
    return [dict(row) for row in session.execute(statement).mappings()]
