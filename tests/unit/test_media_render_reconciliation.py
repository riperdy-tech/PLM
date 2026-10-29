"""B02 bounded read-only claim inventory, not actual worker recovery or MinIO proof."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from media_factory import cli, render_reconciliation
from media_factory.artifacts import MinioArtifactStore
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.errors import MediaInfrastructureError
from media_factory.render_reconciliation import inspect_render_claim
from media_factory.storage.render_claims import claim_queued_render_job, queue_render_job
from plm_platform.cli import app
from plm_platform.storage import MEDIA_PREFIX, build_key
from typer.testing import CliRunner

from tests.support.fake_minio import FakeMinio, store_settings
from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
SHA = "a" * 64
NAMESPACE = "b" * 64


class ListingFakeMinio(FakeMinio):
    def __init__(self) -> None:
        super().__init__()
        self.listed: list[str] = []
        self.reads = 0
        self.writes = 0

    def list_objects(self, bucket: str, *, prefix: str, recursive: bool = True) -> Any:
        self._reach()
        assert recursive
        self.listed.append(prefix)
        for (b, key), data in sorted(self.objects.items()):
            if b == bucket and key.startswith(prefix):
                yield SimpleNamespace(object_name=key, size=len(data))

    def get_object(self, bucket: str, key: str) -> Any:
        self.reads += 1
        return super().get_object(bucket, key)

    def put_object(self, bucket: str, key: str, data: Any, length: int,
                   content_type: str = "application/octet-stream") -> None:
        self.writes += 1
        super().put_object(bucket, key, data, length, content_type=content_type)


def _store() -> tuple[MinioArtifactStore, ListingFakeMinio]:
    fake = ListingFakeMinio()
    return MinioArtifactStore(store_settings(), client=fake), fake  # type: ignore[arg-type]


def _add(fake: ListingFakeMinio, path: str, data: bytes = b"media") -> str:
    settings = store_settings()
    key = build_key(MEDIA_PREFIX, path, hashlib.sha256(data).hexdigest())
    fake.objects[(settings.minio_bucket, key)] = data
    return key


def _job() -> RenderJob:
    queued = RenderJob.create(
        "package_01", "preview_360p_v1", input_manifest_hash=SHA, now=NOW,
    )
    return replace(queued, job_status=RenderJobStatus.RUNNING,
                   worker_id="worker_alpha", started_at=NOW)


def test_claim_listing_is_exact_read_only_and_key_sha_is_not_byte_proof() -> None:
    store, fake = _store()
    render_root = f"renders/brand_01/package_01/claims/{NAMESPACE}"
    thumb_root = f"thumbnails/brand_01/package_01/claims/{NAMESPACE}"
    valid = _add(fake, f"{render_root}/audio_master.wav")
    _add(fake, f"{thumb_root}/thumb_a.png")
    unrelated = _add(fake, f"renders/brand_01/package_other/claims/{NAMESPACE}/audio_master.wav")
    _add(fake, "renders/brand_01/package_01/claims/" + "c" * 64 + "/audio_master.wav")
    # Metadata-only inspection must not detect this corruption as good bytes.
    fake.objects[(store_settings().minio_bucket, valid)] = b"tampered"
    page = store.list_claim_objects(
        brand_id="brand_01", media_package_id="package_01", claim_namespace=NAMESPACE,
    )
    assert len(page.objects) == 2 and not page.truncated
    assert all(f"/claims/{NAMESPACE}/" in item.uri for item in page.objects)
    assert all(unrelated not in item.uri for item in page.objects)
    assert page.objects[0].pinned_sha256 == hashlib.sha256(b"media").hexdigest()
    assert fake.reads == fake.writes == 0
    assert len(fake.listed) == 2


def test_claim_listing_has_global_limit_across_render_and_thumbnail_roots() -> None:
    store, fake = _store()
    root = f"renders/brand_01/package_01/claims/{NAMESPACE}"
    for index in range(4):
        _add(fake, f"{root}/output_{index}.wav")
    _add(fake, f"thumbnails/brand_01/package_01/claims/{NAMESPACE}/thumb_a.png")
    result = store.list_claim_objects(
        brand_id="brand_01", media_package_id="package_01",
        claim_namespace=NAMESPACE, limit=2,
    )
    assert len(result.objects) == 2 and result.truncated
    assert fake.reads == fake.writes == 0 and len(fake.listed) == 1
    for invalid in (0, 101, True, 1.0):
        with pytest.raises(ValueError, match="limit"):
            store.list_claim_objects(
                brand_id="brand_01", media_package_id="package_01",
                claim_namespace=NAMESPACE, limit=invalid,
            )
    for bad in ("../brand_02", "brand_01/extra", " "):
        with pytest.raises(ValueError):
            store.list_claim_objects(
                brand_id=bad, media_package_id="package_01", claim_namespace=NAMESPACE,
            )
    assert len(fake.listed) == 1


def test_claim_listing_reports_invalid_object_key_without_claiming_integrity() -> None:
    store, fake = _store()
    prefix = f"media/renders/brand_01/package_01/claims/{NAMESPACE}/"
    fake.objects[(store_settings().minio_bucket, prefix + "not_content_addressed")] = b"???"
    page = store.list_claim_objects(
        brand_id="brand_01", media_package_id="package_01", claim_namespace=NAMESPACE,
    )
    assert len(page.objects) == 1 and page.objects[0].pinned_sha256 is None


def test_claim_listing_refuses_out_of_scope_backend_response() -> None:
    store, fake = _store()

    def malicious(bucket: str, *, prefix: str, recursive: bool = True) -> Any:
        yield SimpleNamespace(object_name="media/unrelated/" + SHA, size=1)

    fake.list_objects = malicious  # type: ignore[method-assign]
    with pytest.raises(MediaInfrastructureError, match="claim listing failed"):
        store.list_claim_objects(
            brand_id="brand_01", media_package_id="package_01", claim_namespace=NAMESPACE,
        )


def test_reconciliation_refuses_no_claim_or_naive_sqlite_timestamp_without_listing() -> None:
    engine = create_media_engine(static_pool=True)
    store, fake = _store()
    job = _job()
    queued = replace(job, job_status=RenderJobStatus.QUEUED,
                     worker_id=None, started_at=None)
    try:
        with media_session(engine) as session:
            assert queue_render_job(session, queued)
            session.commit()
        with media_session(engine) as session:
            pending = inspect_render_claim(
                session, store, job_id=job.id, brand_id="brand_01",
            )
            assert pending.reason == "no_committed_execution_identity"
            session.rollback()
        with media_session(engine) as session:
            assert claim_queued_render_job(
                session, job_id=job.id, worker_id="worker_alpha",
                input_manifest_hash=SHA, started_at=NOW,
            )
            session.commit()
        with media_session(engine) as session:
            naive = inspect_render_claim(
                session, store, job_id=job.id, brand_id="brand_01",
            )
            assert naive.reason == "execution_timezone_unknown"
            assert naive.evidence is None
            session.rollback()
        assert fake.listed == []
    finally:
        engine.dispose()


def test_reconciliation_inventory_preserves_unknown_brand_without_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, fake = _store()
    job = _job()
    from media_factory.storage.render_claims import render_claim_namespace
    namespace = render_claim_namespace(
        job_id=job.id, worker_id="worker_alpha", input_manifest_hash=SHA, started_at=NOW,
    )
    _add(fake, f"renders/brand_01/package_01/claims/{namespace}/video_master.mp4")
    monkeypatch.setattr(render_reconciliation, "get_render_job", lambda *_: job)
    monkeypatch.setattr(render_reconciliation, "get_media_package_by_id", lambda *_: None)
    result = inspect_render_claim(
        None, store, job_id=job.id, brand_id="brand_01",  # type: ignore[arg-type]
    )
    assert result.claim_namespace == namespace
    assert result.brand_source == "unverified_operator_hint"
    assert result.reason == "metadata_only_unverified_bytes_and_worker_liveness"
    assert result.evidence is not None and len(result.evidence.objects) == 1
    assert fake.reads == fake.writes == 0


def test_reconciliation_refuses_mismatched_persisted_brand_or_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, fake = _store()
    job = _job()
    monkeypatch.setattr(render_reconciliation, "get_render_job", lambda *_: job)
    monkeypatch.setattr(render_reconciliation, "get_media_package_by_id", lambda *_:
                        SimpleNamespace(brand_id="brand_other", render_report={}))
    with pytest.raises(ValueError, match="brand ID conflicts"):
        inspect_render_claim(None, store, job_id=job.id, brand_id="brand_01")  # type: ignore[arg-type]
    monkeypatch.setattr(render_reconciliation, "get_media_package_by_id", lambda *_:
                        SimpleNamespace(brand_id="brand_01", render_report={
                            "artifact_claim_namespace": "c" * 64,
                        }))
    conflicted = inspect_render_claim(
        None, store, job_id=job.id, brand_id="brand_01",  # type: ignore[arg-type]
    )
    assert conflicted.reason == "stored_claim_namespace_conflict"
    assert conflicted.evidence is None and fake.listed == []


def test_render_inspect_cli_is_read_only_and_explicitly_non_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, fake = _store()
    engine = create_media_engine(static_pool=True)
    job = _job()
    monkeypatch.setattr(cli, "platform_session_factory", lambda: lambda: media_session(engine))
    monkeypatch.setattr(cli, "MinioArtifactStore", lambda _: store)
    monkeypatch.setattr(cli, "load_settings", store_settings)
    monkeypatch.setattr(render_reconciliation, "get_render_job", lambda *_: job)
    monkeypatch.setattr(render_reconciliation, "get_media_package_by_id", lambda *_: None)
    try:
        command = CliRunner().invoke(
            app, ["media", "render-inspect", "--job-id", job.id,
                  "--brand-id", "brand_01", "--limit", "2"],
        )
        assert command.exit_code == 0, command.output
        assert "unverified_operator_hint" in command.output
        assert "No retry or mutation" in command.output
        assert "Observed objects: 0" in command.output
        assert fake.reads == fake.writes == 0
        assert len(fake.listed) == 2
        help_result = CliRunner().invoke(app, ["media", "render-inspect", "--help"])
        assert help_result.exit_code == 0
        assert "--job-id" in help_result.output and "--brand-id" in help_result.output
    finally:
        engine.dispose()
