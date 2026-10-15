"""SQLite harness for the live dashboard tests (R5): every schema the views read.

One in-memory database (``StaticPool``, shared across the TestClient's worker threads)
with ``ops``, ``ar``, ``analytics``, ``youtube``, ``distribution`` and ``market``
attached and their tables created from each owner's own metadata. Seed helpers write
through the owners' writers where one exists (:class:`ExceptionQueue`,
:class:`PipelineStore`, the P9 ingest API in the tests) and insert rows directly only for
tables whose owner has no in-process writer worth driving here (P2 arm state, lineage).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from analytics.storage.models import ANALYTICS_METADATA
from distribution.storage.models import DISTRIBUTION_METADATA
from plm_platform.exceptions import EXCEPTIONS_METADATA, ExceptionCase, ExceptionQueue
from plm_platform.pipeline.store import PIPELINE_METADATA, PipelineStore
from plm_platform.pipeline.types import TransitionRecord
from sqlalchemy.orm import Session, sessionmaker
from youtube_publishing.storage.models import YOUTUBE_METADATA

from services.autonomous_ar.storage.models import AR_METADATA
from services.market_intelligence.storage.models import MARKET_METADATA

__all__ = [
    "ALL_SCHEMAS",
    "create_dashboard_engine",
    "raise_hold",
    "record_run",
    "seed_arms",
    "seed_lineage",
]

ALL_SCHEMAS: tuple[str, ...] = ("ops", "ar", "analytics", "youtube", "distribution", "market")
_METADATA: dict[str, Sequence[sa.MetaData]] = {
    "ops": (EXCEPTIONS_METADATA, PIPELINE_METADATA),
    "ar": (AR_METADATA,),
    "analytics": (ANALYTICS_METADATA,),
    "youtube": (YOUTUBE_METADATA,),
    "distribution": (DISTRIBUTION_METADATA,),
    "market": (MARKET_METADATA,),
}


def create_dashboard_engine(schemas: Iterable[str] = ALL_SCHEMAS) -> sa.Engine:
    """The schemas named are attached and created; the others do not exist at all."""
    chosen = tuple(schemas)
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=sa.pool.StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        for schema in chosen:
            connection.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {schema}")
    for schema in chosen:
        for metadata in _METADATA[schema]:
            metadata.create_all(engine)
    return engine


def _uid() -> str:
    return uuid.uuid4().hex


def seed_arms(factory: sessionmaker[Session], now: datetime,
              arms: Sequence[tuple[str, str, float | None, int]]) -> None:
    """P2 arm rows: (arm_id, status, posterior mean or None for no posterior, observations)."""
    state = AR_METADATA.tables["ar.ar_content_arm_state"]
    posterior = AR_METADATA.tables["ar.ar_arm_posterior"]
    with factory() as session:
        for arm_id, status, mean, observations in arms:
            session.execute(state.insert().values(
                id=_uid(), brand_id="brand_test", arm_id=arm_id, status=status,
                selection_count=observations, last_updated_at=now))
            if mean is not None:
                session.execute(posterior.insert().values(
                    id=_uid(), brand_id="brand_test", arm_id=arm_id, model_type="gaussian",
                    model_version="v1", prior_mean=0.5, prior_variance=0.04,
                    posterior_mean=mean, posterior_variance=0.01 + 0.001 * observations,
                    observation_count=observations, effective_observation_count=observations,
                    last_update_at=now))
        session.commit()


def seed_lineage(factory: sessionmaker[Session], now: datetime, *,
                 videos: dict[str, str], tracks: dict[str, str],
                 genres: dict[str, str]) -> None:
    """Stored lineage: video → arm (P7), track → release → arm (P8), arm → genre (P1)."""
    publications = YOUTUBE_METADATA.tables["youtube.publication_records"]
    releases = DISTRIBUTION_METADATA.tables["distribution.releases"]
    track_rows = DISTRIBUTION_METADATA.tables["distribution.tracks"]
    market_arms = MARKET_METADATA.tables["market.market_arms"]
    lineage: dict[str, Any] = {"experiment_id": "exp_1", "experiment_family_id": "fam_1",
                               "content_genome_id": "gen_1", "production_directive_id": "dir_1"}
    with factory() as session:
        for video_id, arm_id in videos.items():
            session.execute(publications.insert().values(
                publication_record_id=_uid(), youtube_video_id=video_id, channel_id="chan_1",
                publish_attempt_id=_uid(), media_package_id="pkg_1",
                release_manifest_id="man_1", content_arm_id=arm_id, title=video_id,
                privacy_status="private", contains_synthetic_media=True, published_at=now,
                video_url=f"https://youtube.example/{video_id}", created_at=now,
                updated_at=now, **lineage))
        for index, (track_id, arm_id) in enumerate(sorted(tracks.items())):
            release_id = f"rel_{track_id}"
            session.execute(releases.insert().values(
                id=_uid(), release_id=release_id, artist_id="artist_1",
                release_title=track_id, status="submitted", submission_fingerprint=_uid(),
                deliveries=[], source_manifest_ids=[], content_arm_id=arm_id,
                media_package_id="pkg_1", created_at=now, updated_at=now, **lineage))
            session.execute(track_rows.insert().values(
                id=_uid(), track_id=track_id, release_id=release_id, track_number=1,
                title=track_id, isrc=f"USPLM26{index:05d}", duration_seconds=180.0,
                audio_asset_uri="s3://plm/a.wav", audio_checksum="0" * 64, created_at=now))
        for arm_id, genre in genres.items():
            session.execute(market_arms.insert().values(
                id=_uid(), brand_id="brand_test", arm_id=arm_id, fingerprint=_uid(),
                status="active", taxonomy_json={"genre": genre}, source_observation_ids=[],
                support_count=1))
        session.commit()


def raise_hold(factory: sessionmaker[Session], reason_code: str, now: datetime, *,
               subject_ref: str | None = None, raised_by: str = "P5:test",
               origin_phase: str = "P5") -> ExceptionCase:
    with factory() as session:
        result = ExceptionQueue().raise_exception(
            session, origin_phase=origin_phase,
            subject_ref=subject_ref or f"cand_{_uid()[:12]}", reason_code=reason_code,
            raised_by=raised_by, now=now, details={"note": "seeded by the dashboard test"})
        session.commit()
        return result.case


HELD_PATH = ("IDEA", "RESEARCHED", "POLICY_CHECKED", "GENERATING", "GENERATED", "QA")


def record_run(factory: sessionmaker[Session], workflow_id: str, now: datetime, *,
               path: Sequence[str] = HELD_PATH, hold_case_ids: Sequence[str] = (),
               hold_reason: str = "qa_quarantined", fail: str | None = None) -> None:
    """Record a run through :class:`PipelineStore`: ``path``, then HELD (when
    ``hold_case_ids``) or FAILED (when ``fail`` names the error)."""
    store = PipelineStore()
    sequence = 0
    previous: str | None = None
    at = now - timedelta(minutes=30)

    def record(to_state: str, **extra: Any) -> None:
        nonlocal sequence, previous, at
        sequence += 1
        at = at + timedelta(minutes=1)
        store.record(session, TransitionRecord(
            workflow_id=workflow_id, sequence=sequence, from_state=previous,
            to_state=to_state, occurred_at=at, directive_id=f"dir_{workflow_id}",
            brand_id="brand_test", **extra))
        previous = to_state

    with factory() as session:
        for state in path:
            record(state)
        if hold_case_ids:
            record("HELD", step="qa", error_code=hold_reason, held_from=previous,
                   hold_case_ids=tuple(hold_case_ids))
        elif fail is not None:
            record("FAILED", step="generate", error_code=fail, error_detail="scripted",
                   final=True)
        session.commit()
