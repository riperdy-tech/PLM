"""B02 conditional retry insert must not override published work or retry caps."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.storage.models import media_packages_table
from media_factory.storage.render_claims import (
    claim_queued_render_job,
    quarantine_stale_render_job,
    queue_reconciled_render_retry,
    queue_render_job,
)
from media_factory.storage.repositories import get_render_job

from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)
SHA = "a" * 64


def _stale(session: sa.orm.Session, package: str) -> RenderJob:
    job = RenderJob.create(package, "preview_360p_v1", input_manifest_hash=SHA, now=NOW)
    assert queue_render_job(session, job)
    assert claim_queued_render_job(
        session, job_id=job.id, worker_id="owner", input_manifest_hash=SHA, started_at=NOW,
    )
    assert quarantine_stale_render_job(
        session, job_id=job.id, input_manifest_hash=SHA,
        started_before=NOW, quarantined_at=NOW + timedelta(minutes=1),
        expected_worker_id="owner", expected_started_at=NOW,
        worker_termination_confirmed=True,
    )
    return job


def _queue(session: sa.orm.Session, job: RenderJob, *, worker: str,
           start: datetime, when: datetime, max_attempts: int = 3) -> RenderJob | None:
    return queue_reconciled_render_retry(
        session, previous_job_id=job.id, expected_worker_id=worker,
        expected_started_at=start, input_manifest_hash=SHA,
        worker_termination_confirmed=True, orphan_artifacts_reconciled=True,
        source_inputs_reverified=True, now=when, max_attempts=max_attempts,
    )


def test_persisted_media_package_blocks_retry_even_when_prior_is_quarantined() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            job = _stale(session, "already_published")
            session.execute(sa.insert(media_packages_table).values(
                id="persisted_ready", media_package_id=job.media_package_id,
                release_manifest_id="release", experiment_id="experiment",
                content_arm_id="arm", brand_id="brand", playlist_session_id="playlist",
                visual_session_id="visual", video_master_uri="memory://video",
                video_checksum=SHA, video_duration_seconds=42.0,
                render_profile=job.render_profile, audio_master_uri="memory://audio",
                audio_checksum=SHA, thumbnails_json="[]", tracklist_json="[]",
                chapters_json="[]", metadata_candidates_json="[]",
                short_source_assets_json="[]", render_report_json="{}",
                status="ready_for_publish", package_version="v1",
                manifest_uri="", manifest_hash=SHA, created_at=NOW, updated_at=NOW,
            ))
            assert _queue(session, job, worker="owner", start=NOW,
                          when=NOW + timedelta(minutes=3)) is None
            session.commit()
        with media_session(engine) as session:
            assert get_render_job(session, job.id).job_status is RenderJobStatus.QUARANTINED
            assert len(session.execute(sa.select(media_packages_table.c.id)).scalars().all()) == 1
    finally:
        engine.dispose()


def test_retry_respects_configured_max_attempts_and_original_execution_fence() -> None:
    engine = create_media_engine(static_pool=True)
    second_start = NOW + timedelta(minutes=3)
    try:
        with media_session(engine) as session:
            first = _stale(session, "bounded_attempts")
            second = _queue(session, first, worker="owner", start=NOW,
                            when=NOW + timedelta(minutes=2))
            assert second is not None and second.attempt_count == 2
            assert claim_queued_render_job(
                session, job_id=second.id, worker_id="owner-two",
                input_manifest_hash=SHA, started_at=second_start,
            )
            assert quarantine_stale_render_job(
                session, job_id=second.id, input_manifest_hash=SHA,
                started_before=second_start,
                quarantined_at=NOW + timedelta(minutes=4),
                expected_worker_id="owner-two", expected_started_at=second_start,
                worker_termination_confirmed=True,
            )
            assert _queue(session, second, worker="owner-two", start=second_start,
                          when=NOW + timedelta(minutes=5), max_attempts=2) is None
            assert _queue(session, second, worker="owner", start=NOW,
                          when=NOW + timedelta(minutes=5)) is None
            third = _queue(session, second, worker="owner-two", start=second_start,
                           when=NOW + timedelta(minutes=5))
            assert third is not None and third.attempt_count == 3
            assert _queue(session, second, worker="owner-two", start=second_start,
                          when=NOW + timedelta(minutes=6)) is None
            session.commit()
        with media_session(engine) as session:
            assert get_render_job(session, second.id).job_status is RenderJobStatus.QUARANTINED
            assert get_render_job(session, third.id).job_status is RenderJobStatus.QUEUED
    finally:
        engine.dispose()
