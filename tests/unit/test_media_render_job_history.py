"""B02: read-only bounded per-package render history (not worker recovery)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from click import unstyle
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.storage.render_claims import (
    claim_queued_render_job,
    finish_claimed_render_job,
    list_render_job_summaries,
    queue_render_job,
)
from media_factory.storage.repositories import get_render_job, insert_render_job
from plm_platform.cli import app
from typer.testing import CliRunner

from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 10, 28, tzinfo=UTC)
SHA = "a" * 64
OUTPUT = "b" * 64


def test_render_history_is_bounded_scoped_and_read_only() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            first = RenderJob.create(
                "package_target", "preview_360p_v1",
                input_manifest_hash=SHA, now=NOW,
            )
            retry = RenderJob.create(
                "package_target", "preview_360p_v1",
                input_manifest_hash=SHA, attempt=2,
                now=NOW + timedelta(seconds=10),
            )
            unrelated = RenderJob.create(
                "package_unrelated", "preview_360p_v1",
                input_manifest_hash=SHA, now=NOW,
            )
            for job in (first, unrelated):
                assert queue_render_job(session, job)
            assert claim_queued_render_job(
                session, job_id=first.id, worker_id="worker-a",
                input_manifest_hash=SHA, started_at=NOW,
            )
            assert finish_claimed_render_job(
                session, job_id=first.id, worker_id="worker-a",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(seconds=2),
                status=RenderJobStatus.COMPLETED, output_checksum=OUTPUT,
            )
            # History reader must also show preexisting legacy/anomalous
            # attempts; bypass the protected runtime queue only in this fixture.
            insert_render_job(session, retry)
            session.commit()
        with media_session(engine) as session:
            history = list_render_job_summaries(
                session, media_package_id="package_target",
            )
            assert [entry.job_id for entry in history] == [retry.id, first.id]
            assert [entry.status for entry in history] == [
                RenderJobStatus.QUEUED, RenderJobStatus.COMPLETED,
            ]
            assert history[1].finished_at is not None
            assert len(list_render_job_summaries(
                session, media_package_id="package_target", limit=1,
            )) == 1
            assert list_render_job_summaries(
                session, media_package_id="package_missing",
            ) == ()
            for invalid in (0, 101, True, 1.5):
                with pytest.raises(ValueError, match="limit"):
                    list_render_job_summaries(
                        session, media_package_id="package_target",
                        limit=invalid,
                    )
            with pytest.raises(ValueError, match="media_package_id"):
                list_render_job_summaries(session, media_package_id="  ")
            session.rollback()
        with media_session(engine) as session:
            assert get_render_job(session, first.id).job_status is RenderJobStatus.COMPLETED
            assert get_render_job(session, retry.id).job_status is RenderJobStatus.QUEUED
            assert get_render_job(session, unrelated.id).job_status is RenderJobStatus.QUEUED
    finally:
        engine.dispose()


def test_render_history_cli_is_read_only_and_labels_unknown_worker_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from media_factory import cli

    engine = create_media_engine(static_pool=True)
    try:
        pending = RenderJob.create(
            "package_operator", "preview_360p_v1", input_manifest_hash=SHA,
            now=NOW,
        )
        with media_session(engine) as session:
            assert queue_render_job(session, pending)
            assert claim_queued_render_job(
                session, job_id=pending.id, worker_id="prior-process",
                input_manifest_hash=SHA, started_at=NOW,
            )
            session.commit()
        monkeypatch.setattr(
            cli, "platform_session_factory", lambda: lambda: media_session(engine),
        )
        result = CliRunner().invoke(
            app, ["media", "render-jobs", "-p", "package_operator", "--limit", "10"],
        )
        assert result.exit_code == 0, result.output
        assert pending.id in result.output
        assert "running" in result.output.lower()
        assert "does not prove worker liveness" in result.output
        assert "No automatic recovery" in result.output
        missing = CliRunner().invoke(
            app, ["media", "render-jobs", "--package-id", "package_other"],
        )
        assert missing.exit_code == 0
        assert "Rows: 0" in missing.output
        with media_session(engine) as session:
            status = get_render_job(session, pending.id)
            assert status is not None and status.job_status is RenderJobStatus.RUNNING
            assert status.worker_id == "prior-process"
    finally:
        engine.dispose()


def test_render_history_cli_requires_exact_package() -> None:
    runner = CliRunner()
    help_result = runner.invoke(app, ["media", "render-jobs", "--help"])
    assert help_result.exit_code == 0
    # Typer forces Rich styling under GITHUB_ACTIONS; compare plain text.
    help_output = unstyle(help_result.output)
    assert "--package-id" in help_output
    assert "--limit" in help_output
    missing = runner.invoke(app, ["media", "render-jobs"])
    assert missing.exit_code != 0
