"""Opt-in P1 taxonomy-v2 replay on an isolated disposable PostgreSQL database.

NEVER points tests at the configured development/production database itself.
Requires a loopback server and explicit PLM_MARKET_PG_ISOLATED=1; creates and
drops only a randomly named throwaway database. No network provider calls.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from plm_contracts import Tri
from plm_platform.config import load_settings
from plm_platform.db import database_url
from sqlalchemy.orm import Session

from services.market_intelligence.cli import _classify_pending, _recompute
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.enums import (
    ConnectorType,
    EntityType,
    ProviderName,
    ProviderObjectType,
)
from services.market_intelligence.domain.enums import TaxonomyDimension as D
from services.market_intelligence.normalization.youtube import NormalizedObservationDraft
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import MARKET_METADATA, raw_observations
from services.market_intelligence.storage.raw_store import SCHEMA_VERSION
from services.market_intelligence.taxonomy.arms import generate_candidate_arms
from services.market_intelligence.taxonomy.validators import ClassificationDraft

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def disposable_market_db() -> Iterator[sa.Engine]:
    if os.getenv("PLM_MARKET_PG_ISOLATED") != "1":
        pytest.skip("requires PLM_MARKET_PG_ISOLATED=1 for disposable PostgreSQL")
    url = database_url(load_settings(dotenv_path=Path(".env")))
    if url.get_backend_name() != "postgresql":
        pytest.skip("requires PostgreSQL")
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("requires loopback PostgreSQL, never shared remote DB")
    root = sa.create_engine(
        url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5},
    )
    disposable_name = f"plm_market_duration_probe_{uuid4().hex[:16]}"
    quoted = f'"{disposable_name}"'
    created = False
    probe: sa.Engine | None = None
    try:
        with root.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quoted}")
        created = True
        probe = sa.create_engine(
            url.set(database=disposable_name), connect_args={"connect_timeout": 5},
        )
        with probe.begin() as connection:
            connection.exec_driver_sql("CREATE SCHEMA market")
        MARKET_METADATA.create_all(probe)
        yield probe
    finally:
        if probe is not None:
            probe.dispose()
        if created:
            with root.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {quoted} WITH (FORCE)")
        root.dispose()


def test_postgres_measured_duration_classifies_and_preserves_versioned_evidence(
    disposable_market_db: sa.Engine,
) -> None:
    settings = load_market_settings(min_arm_support=2)
    with Session(disposable_market_db) as session:
        source_id = repositories.upsert_source(
            session, provider_name=ProviderName.YOUTUBE_DATA_API,
            connector_type=ConnectorType.DATA_API,
            base_identifier="https://example.invalid/probe",
            configuration_version="p1-sources-v1",
        )
        run_id = repositories.start_collection_run(
            session, source_id=source_id, query_id=None,
            software_version="duration-probe", started_at=NOW,
        )
        ids: list[str] = []
        for idx, (duration, age_h) in enumerate([
            (3600.0, 3), (3900.0, 2), (125.0, 1),
        ]):
            when = NOW - timedelta(hours=age_h)
            provider_id = f"pgdur_{idx:05d}"
            entity_id = repositories.resolve_entity(
                session, entity_type=EntityType.VIDEO, source_id=source_id,
                provider_object_type=ProviderObjectType.VIDEO,
                provider_object_id=provider_id,
                canonical_name="45_75m lofi study playlist",
                normalized_name="45_75m lofi study playlist",
                canonical_url=None, observed_at=when,
            )
            raw_id = repositories.deterministic_id(
                "raw", "measured-duration-probe", provider_id,
            )
            payload = {"duration_seconds": duration}
            session.execute(sa.insert(raw_observations).values(
                id=raw_id, collection_run_id=run_id, source_id=source_id,
                provider_object_type=ProviderObjectType.VIDEO.value,
                provider_object_id=provider_id, observed_at=when,
                request_context_hash="c" * 64,
                payload_json=payload,
                payload_hash=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8"),
                ).hexdigest(),
                payload_uri=f"plm://fixture/market/{raw_id}",
                schema_version=SCHEMA_VERSION,
            ))
            observation_id, created = repositories.insert_normalized_observation(
                session, NormalizedObservationDraft(
                    entity_id=entity_id, source_id=source_id,
                    raw_observation_id=raw_id, observed_at=when,
                    published_at=when - timedelta(days=3),
                    views=Tri.known(1500), likes=Tri.known(100),
                    comments=Tri.known(10), subscriber_count=Tri.known(750),
                    video_duration_seconds=Tri.known(duration),
                    playlist_item_count=Tri.unknown(),
                    channel_video_count=Tri.unknown(),
                    text_title="45_75m lofi study playlist",
                    text_description="rain night city",
                    country_signal=None, language_signal="en",
                ),
            )
            assert created
            ids.append(observation_id)
        session.commit()
    with Session(disposable_market_db) as session:
        classified = _classify_pending(session, now=NOW, settings=settings, full=True)
        assert classified["classified"] == 2
        assert classified["cached"] == 1
        session.commit()
    with Session(disposable_market_db) as session:
        recomputed = _recompute(session, now=NOW, settings=settings)
        assert recomputed["arms"] == 1
        session.commit()
    with Session(disposable_market_db) as session:
        current = [
            row for row in repositories.load_arms(session)
            if row["arm_id"].startswith("p1_taxonomy_v2_")
        ]
        assert len(current) == 1
        assert current[0]["taxonomy_json"]["duration_bucket"] == "45_75m"
        assert current[0]["support_count"] == 2
        assert set(current[0]["source_observation_ids"]) == set(ids[:2])
        assert ids[2] not in current[0]["source_observation_ids"]
        replay = _recompute(session, now=NOW, settings=settings)
        assert replay["arms"] == 1
        session.rollback()


def test_postgres_versioned_arm_slug_collision_fails_closed(
    disposable_market_db: sa.Engine,
) -> None:
    shared = {D.GENRE: ("jazz",), D.DURATION_BUCKET: ("3_10m",)}
    first = generate_candidate_arms(
        [ClassificationDraft(dimensions={**shared, D.MOOD: ("calm",)}, confidence=0.8)],
        min_support=1, taxonomy_version="p1-taxonomy-v2",
    )[0]
    other = generate_candidate_arms(
        [ClassificationDraft(dimensions={**shared, D.MOOD: ("dreamy",)}, confidence=0.8)],
        min_support=1, taxonomy_version="p1-taxonomy-v2",
    )[0]
    assert first.arm_id == other.arm_id
    assert first.fingerprint != other.fingerprint
    with Session(disposable_market_db) as session:
        first_id, created = repositories.upsert_arm(
            session, first, brand_id="neon_seoul", refuse_slug_collision=True,
        )
        assert created
        session.commit()
    with Session(disposable_market_db) as session:
        with pytest.raises(ValueError, match="arm_slug_identity_conflict"):
            repositories.upsert_arm(
                session, other, brand_id="neon_seoul", refuse_slug_collision=True,
            )
        session.rollback()
    with Session(disposable_market_db) as session:
        persisted = next(
            item for item in repositories.load_arms(session)
            if item["arm_id"] == first_id
        )
        assert persisted["fingerprint"] == first.fingerprint
        assert persisted["taxonomy_json"]["mood"] == "calm"


def test_postgres_v3_opt_in_distinguishes_profiles_across_independent_batches(
    disposable_market_db: sa.Engine,
) -> None:
    shared = {D.GENRE: ("jazz",), D.DURATION_BUCKET: ("3_10m",)}
    calm = ClassificationDraft(
        dimensions={**shared, D.MOOD: ("calm",)}, confidence=0.8,
    )
    dreamy = ClassificationDraft(
        dimensions={**shared, D.MOOD: ("dreamy",)}, confidence=0.8,
    )
    first = generate_candidate_arms(
        [calm], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )[0]
    other = generate_candidate_arms(
        [dreamy], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )[0]
    assert first.arm_id != other.arm_id
    assert first.arm_id.startswith("p1_taxonomy_v3_jazz_3_")
    assert len(first.arm_id) == 64
    assert len(other.arm_id) == 64
    all_at_once = generate_candidate_arms(
        [calm, dreamy], min_support=1, taxonomy_version="p1-taxonomy-v3",
    )
    assert {a.arm_id for a in all_at_once} == {first.arm_id, other.arm_id}
    with Session(disposable_market_db) as session:
        for item in (first, other):
            saved_id, created = repositories.upsert_arm(
                session, item, brand_id="neon_seoul", refuse_slug_collision=True,
            )
            assert created and saved_id == item.arm_id
        session.commit()
    with Session(disposable_market_db) as session:
        saved_id, created = repositories.upsert_arm(
            session, first, brand_id="neon_seoul", refuse_slug_collision=True,
        )
        assert not created and saved_id == first.arm_id
        stored = {
            row["arm_id"]: row for row in repositories.load_arms(session)
            if row["arm_id"].startswith("p1_taxonomy_v3_")
        }
        assert len(stored) == 2
        assert stored[first.arm_id]["taxonomy_json"]["mood"] == "calm"
        assert stored[other.arm_id]["taxonomy_json"]["mood"] == "dreamy"
        session.rollback()
    # Exercise PostgreSQL's actual VARCHAR(64) enforcement, not SQLite
    # or the market arm table (which stores unbounded Text).
    width_probe = sa.Table(
        "v3_arm_id_width_probe", sa.MetaData(schema="market"),
        sa.Column("arm_id", sa.String(64), primary_key=True),
    )
    width_probe.create(disposable_market_db)
    with disposable_market_db.begin() as connection:
        connection.execute(sa.insert(width_probe), [
            {"arm_id": first.arm_id},
            {"arm_id": other.arm_id},
        ])
        saved = set(connection.execute(sa.select(width_probe.c.arm_id)).scalars())
    assert saved == {first.arm_id, other.arm_id}
