"""B02: queued/recovery render claims must remain fenced at claim AND finish.

These SQLite stand-ins prove SQL conditions, not a PostgreSQL race or process death.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.storage.models import media_packages_table, render_jobs_table
from media_factory.storage.render_claims import (
    claim_queued_render_job,
    finish_claimed_render_job,
    queue_render_job,
)
from media_factory.storage.repositories import get_render_job, insert_render_job

from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 10, 29, 13, tzinfo=UTC)
SHA = "a" * 64
VIDEO = "b" * 64


def _job(package_id: str, *, attempt: int = 1) -> RenderJob:
    return RenderJob.create(
        package_id, "preview_360p_v1", attempt=attempt,
        input_manifest_hash=SHA, now=NOW + timedelta(seconds=attempt),
    )


def _persist_package(session: sa.orm.Session, package_id: str) -> None:
    # A deliberately minimal existing row: presence, not media validity, is the gate.
    session.execute(sa.insert(media_packages_table).values(
        id="stored_" + package_id, media_package_id=package_id,
        release_manifest_id="rel", experiment_id="exp", content_arm_id="arm",
        brand_id="brand", playlist_session_id="playlist", visual_session_id="visual",
        video_master_uri="fixture://video", video_checksum=SHA,
        video_duration_seconds=30.0, render_profile="preview_360p_v1",
        audio_master_uri="fixture://audio", audio_checksum=SHA,
        thumbnails_json="[]", tracklist_json="[]", chapters_json="[]",
        metadata_candidates_json="[]", short_source_assets_json="[]",
        render_report_json="{}", status="ready_for_publish",
        package_version="v1", manifest_uri="", manifest_hash=SHA,
        created_at=NOW, updated_at=NOW,
    ))


def _claim(session: sa.orm.Session, job: RenderJob) -> bool:
    return claim_queued_render_job(
        session, job_id=job.id, worker_id="worker-a",
        input_manifest_hash=SHA, started_at=NOW + timedelta(minutes=1),
    )


def _finish(session: sa.orm.Session, job: RenderJob) -> bool:
    return finish_claimed_render_job(
        session, job_id=job.id, worker_id="worker-a",
        input_manifest_hash=SHA, started_at=NOW + timedelta(minutes=1),
        finished_at=NOW + timedelta(minutes=2),
        status=RenderJobStatus.COMPLETED, output_checksum=VIDEO,
    )


def test_generic_queue_rejects_second_attempt_even_with_valid_job_id() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        first, retry = _job("generic_queue"), _job("generic_queue", attempt=2)
        with media_session(engine) as session:
            assert queue_render_job(session, first)
            with pytest.raises(ValueError, match="reconciled retry staging"):
                queue_render_job(session, retry)
            assert get_render_job(session, retry.id) is None
            session.rollback()
    finally:
        engine.dispose()


def test_claim_rejects_forged_retry_without_matching_quarantined_predecessor() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        first, retry = _job("forged_retry"), _job("forged_retry", attempt=2)
        with media_session(engine) as session:
            # Legacy/direct DB insert is possible; claiming must still fail.
            insert_render_job(session, retry)
            assert not _claim(session, retry)
            assert queue_render_job(session, first)
            assert not _claim(session, retry)
            # Terminal FAILED is not approved quarantine and must stay refused.
            session.execute(sa.update(render_jobs_table).where(
                render_jobs_table.c.id == first.id,
            ).values(
                job_status=RenderJobStatus.FAILED.value, error_code="render_failed",
            ))
            assert not _claim(session, retry)
            session.rollback()
    finally:
        engine.dispose()


def test_claim_blocks_previously_queued_work_if_package_is_now_stored() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = _job("existing_final_package")
        with media_session(engine) as session:
            assert queue_render_job(session, job)
            _persist_package(session, job.media_package_id)
            assert not _claim(session, job)
            stored = get_render_job(session, job.id)
            assert stored is not None and stored.job_status is RenderJobStatus.QUEUED
            session.commit()
    finally:
        engine.dispose()


def test_claim_blocks_older_attempt_when_a_newer_job_exists() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        initial = _job("superseded_queued")
        newer = _job(initial.media_package_id, attempt=2)
        with media_session(engine) as session:
            assert queue_render_job(session, initial)
            insert_render_job(session, newer)  # Simulate old/malformed row.
            assert not _claim(session, initial)
            assert not _claim(session, newer)  # Predecessor isn't quarantined.
            session.rollback()
    finally:
        engine.dispose()


def test_finish_blocks_late_newer_attempt_even_with_original_worker_token() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        first = _job("superseded_running")
        later = _job(first.media_package_id, attempt=2)
        with media_session(engine) as session:
            assert queue_render_job(session, first)
            assert _claim(session, first)
            insert_render_job(session, later)  # Simulate a broken external writer.
            assert not _finish(session, first)
            persisted = get_render_job(session, first.id)
            assert persisted is not None and persisted.job_status is RenderJobStatus.RUNNING
            session.rollback()
    finally:
        engine.dispose()


def test_finish_blocks_package_stored_since_claim() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = _job("published_since_start")
        with media_session(engine) as session:
            assert queue_render_job(session, job)
            assert _claim(session, job)
            _persist_package(session, job.media_package_id)
            assert not _finish(session, job)
            persisted = get_render_job(session, job.id)
            assert persisted is not None and persisted.job_status is RenderJobStatus.RUNNING
            session.rollback()
    finally:
        engine.dispose()


def test_claim_and_finish_still_allow_legitimate_initial_execution() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = _job("legitimate_initial")
        with media_session(engine) as session:
            assert queue_render_job(session, job)
            assert _claim(session, job)
            assert _finish(session, job)
            session.commit()
        with media_session(engine) as session:
            stored = get_render_job(session, job.id)
            assert stored is not None and stored.job_status is RenderJobStatus.COMPLETED
            assert stored.output_checksum == VIDEO
    finally:
        engine.dispose()
