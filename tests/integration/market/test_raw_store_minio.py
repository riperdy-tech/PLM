# tests/integration/market/test_raw_store_minio.py
"""The unit suite fakes the object store; this proves the real one (W0.6 storage)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from plm_platform.db import build_engine, scoped_session
from plm_platform.storage import build_client, ensure_bucket, from_uri, get_verified

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import (
    ConnectorType,
    ProviderName,
    ProviderObjectType,
)
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.raw_store import store_raw_observation

pytestmark = pytest.mark.integration
FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_the_payload_artifact_round_trips_through_minio() -> None:
    try:
        client = build_client()
        ensure_bucket(client=client)
        engine = build_engine()
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"minio/postgres unreachable ({type(exc).__name__}); "
                    "run: docker compose up -d --wait")
    with scoped_session("market", factory=sa.orm.sessionmaker(bind=engine)) as session:
        source_id = repositories.upsert_source(
            session, provider_name=ProviderName.YOUTUBE_DATA_API,
            connector_type=ConnectorType.DATA_API, base_identifier="x",
            configuration_version="p1-sources-v1",
        )
        run_id = repositories.start_collection_run(
            session, source_id=source_id, query_id=None, software_version="p1-v1",
            started_at=FIXED_NOW,
        )
        baked_at = datetime(2020, 1, 2, 3, 4, tzinfo=UTC)
        stored = store_raw_observation(
            session, run_id=run_id, source_id=source_id,
            fetched=FetchedObject(
                provider_object_type=ProviderObjectType.VIDEO,
                provider_object_id="vid000000001", observed_at=baked_at,
                payload={"id": "vid000000001", "statistics": {"viewCount": "1"}},
            ),
            request_context_hash="0" * 64, client=client,
        )
        session.commit()
    assert get_verified(from_uri(stored.payload_uri), client=client)
