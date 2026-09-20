# services/market_intelligence/storage/repositories.py
"""Typed helpers over the `market` schema. Every function takes a Session whose
search_path is the `market` schema (`plm_platform.db.scoped_session("market")`)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..domain.entities import CollectionRunCounters
from ..domain.enums import CollectionRunStatus, ConnectorType, ProviderName
from .models import collection_runs, sources

__all__ = [
    "add_run_cost", "finish_collection_run", "load_run", "start_collection_run", "upsert_source",
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
