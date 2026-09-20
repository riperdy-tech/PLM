# services/market_intelligence/storage/raw_store.py
"""§3.2/§7.4: raw observations are immutable, content-addressed and deduplicated
without collapsing time series. Nothing here ever updates a row."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from plm_platform.storage import ObjectAlreadyExistsError, StoredObject, put_content, to_uri
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..connectors.base import FetchedObject
from .models import raw_observations
from .repositories import add_run_cost

__all__ = ["StoredRawObservation", "load_raw_observation", "store_raw_observation"]

SCHEMA_VERSION: str = "p1-raw-v1"
PutObjectFn = Callable[..., StoredObject]


@dataclass(frozen=True, slots=True)
class StoredRawObservation:
    raw_observation_id: str
    created: bool
    payload_uri: str
    payload_hash: str
    size: int = 0


def store_raw_observation(
    session: Session,
    *,
    run_id: str,
    source_id: str,
    fetched: FetchedObject,
    request_context_hash: str,
    schema_version: str = SCHEMA_VERSION,
    put_object: PutObjectFn | None = None,
    settings: Any = None,
    client: Any = None,
) -> StoredRawObservation:
    """Write the artifact, then the row. Idempotent per the table's dedupe constraint."""
    canonical = json.dumps(fetched.payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    put = put_content if put_object is None else put_object
    resource_path = (
        f"raw/{_provider_token(fetched)}/{fetched.provider_object_type.value}/"
        f"{fetched.observed_at.date().isoformat()}"
    )
    try:
        stored_object = put("market/", resource_path, canonical,
                            content_type="application/json", settings=settings, client=client)
    except ObjectAlreadyExistsError:
        # Content addressing means the same key is the same bytes: re-storing an
        # identical payload is a no-op, not a conflict (§3.2).
        from plm_platform.storage import build_key, sha256_digest

        digest = sha256_digest(canonical)
        stored_object = StoredObject(
            key=build_key("market/", resource_path, digest), digest=digest, size=len(canonical)
        )
    payload_uri = to_uri(stored_object.key, settings=settings)

    values = {
        "id": str(uuid.uuid4()),
        "collection_run_id": run_id,
        "source_id": source_id,
        "provider_object_type": fetched.provider_object_type.value,
        "provider_object_id": fetched.provider_object_id,
        "observed_at": fetched.observed_at,
        "request_context_hash": request_context_hash,
        "payload_json": dict(fetched.payload),
        "payload_hash": stored_object.digest,
        "payload_uri": payload_uri,
        "schema_version": schema_version,
    }
    try:
        with session.begin_nested():
            session.execute(sa.insert(raw_observations).values(**values))
    except IntegrityError:
        existing = session.execute(
            sa.select(raw_observations.c.id, raw_observations.c.payload_uri,
                      raw_observations.c.payload_hash)
            .where(
                raw_observations.c.source_id == source_id,
                raw_observations.c.provider_object_type == fetched.provider_object_type.value,
                raw_observations.c.provider_object_id == fetched.provider_object_id,
                raw_observations.c.observed_at == fetched.observed_at,
                raw_observations.c.payload_hash == stored_object.digest,
            )
        ).one_or_none()
        if existing is None:
            raise
        return StoredRawObservation(
            raw_observation_id=str(existing.id), created=False,
            payload_uri=existing.payload_uri, payload_hash=existing.payload_hash,
        )
    add_run_cost(session, run_id, storage_bytes=stored_object.size)
    return StoredRawObservation(
        raw_observation_id=values["id"], created=True,
        payload_uri=payload_uri, payload_hash=stored_object.digest, size=stored_object.size,
    )


def load_raw_observation(session: Session, raw_observation_id: str) -> Mapping[str, Any] | None:
    """Provenance read (§AC-02): the immutable payload plus its request context."""
    row = session.execute(
        sa.select(raw_observations).where(raw_observations.c.id == raw_observation_id)
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _provider_token(fetched: FetchedObject) -> str:
    return {
        "video": "youtube_data_api", "channel": "youtube_data_api",
        "playlist": "youtube_data_api", "search_result_page": "youtube_data_api",
        "track": "spotify_web_api", "trend_point": "public_trends",
    }[fetched.provider_object_type.value]
