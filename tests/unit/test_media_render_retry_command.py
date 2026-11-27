"""``retry-render``: a package whose claimed render failed can be rendered again (review
2026-11-26, P6 #22). SQLite and the stand-in toolchain; no FFmpeg, MinIO or real DB."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from media_factory import cli
from media_factory.api.app import create_app
from media_factory.api.schemas import AssemblePackageRequest
from media_factory.backend import MediaBackend
from media_factory.config import MediaFactorySettings
from media_factory.domain.enums import RenderJobStatus
from media_factory.errors import MediaAssemblyError, MediaInfrastructureError
from media_factory.rendering.profiles import RenderProfile
from media_factory.rendering.toolchain import AudioMasterSpec, MediaToolError
from media_factory.service import assemble_and_persist, retry_render
from media_factory.storage.models import render_jobs_table, render_recovery_evidence_table
from plm_platform.cli import app
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 11, 26, 9, tzinfo=UTC)
ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9", "PLM_SERVICE_TOKEN": "t",
}


class CrashOnceToolchain(StandInToolchain):
    """The audio encode fails once, after the background was stored under the claim."""

    def __init__(self) -> None:
        super().__init__()
        self.crashes = 1

    def render_audio_master(self, spec: AudioMasterSpec, profile: RenderProfile,
                            out: Path) -> dict[str, Any]:
        if self.crashes:
            self.crashes -= 1
            raise MediaToolError("render_timeout", "simulated encoder stall")
        return super().render_audio_master(spec, profile, out)


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], Session]]:
    # SQLite drops the UTC offset PostgreSQL keeps; the evidence gates refuse a naive
    # claim time, so read the jobs back as PostgreSQL would return them.
    from media_factory import render_reconciliation, service
    from media_factory.storage import repositories

    def aware(session: Session, job_id: str) -> Any:
        job = repositories.get_render_job(session, job_id)
        if job is None:
            return None
        return replace(job, **{name: getattr(job, name).replace(tzinfo=UTC)
                               for name in ("started_at", "finished_at", "created_at")
                               if getattr(job, name) is not None
                               and getattr(job, name).tzinfo is None})

    monkeypatch.setattr(service, "get_render_job", aware)
    monkeypatch.setattr(render_reconciliation, "get_render_job", aware)
    engine = create_media_engine(static_pool=True)
    try:
        yield lambda: media_session(engine)
    finally:
        engine.dispose()


def _setup() -> tuple[MediaBackend, CrashOnceToolchain, AssemblePackageRequest]:
    store = InMemoryArtifactStore()
    manifests = [stored_manifest(store, f"retry_cmd_{n}", measured_duration_seconds=60,
                                 synth=spec) for n, spec in enumerate(synth_specs())]
    toolchain = CrashOnceToolchain()
    request = AssemblePackageRequest.model_validate({
        "candidate_ids": [m.candidate_id for m in manifests], "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "neon_waveform", "playlist_concept": "Deep Focus",
        "render_profile": "preview_360p_v1",
    })
    return backend_for(store, manifests, toolchain=toolchain), toolchain, request


def _assemble(factory: Callable[[], Session], backend: MediaBackend,
              request: AssemblePackageRequest, *, worker: str = "w1") -> Any:
    with factory() as session:
        pkg = assemble_and_persist(
            session, backend, request, clock=FixedClock(NOW),
            default_render_profile="preview_360p_v1", render_session_factory=factory,
            worker_id_factory=lambda: worker)
        session.commit()
    return pkg


def _retry(factory: Callable[[], Session], backend: MediaBackend,
           request: AssemblePackageRequest, package_id: str, **kwargs: Any) -> Any:
    with factory() as session:
        pkg = retry_render(
            session, backend, request, package_id=package_id, clock=FixedClock(NOW),
            default_render_profile="preview_360p_v1", render_session_factory=factory,
            worker_id_factory=lambda: "w2", recorded_by="owner", **kwargs)
        session.commit()
    return pkg


def _failed_package_id(factory: Callable[[], Session], backend: MediaBackend,
                       request: AssemblePackageRequest) -> str:
    with pytest.raises(MediaInfrastructureError, match="render_timeout"):
        _assemble(factory, backend, request)
    with factory() as session:
        package_id: str = session.execute(
            sa.select(render_jobs_table.c.media_package_id)).scalar_one()
    # Assembling the same request again is refused for good: this is what #22 fixes.
    with pytest.raises(MediaAssemblyError, match="render_job_reconciliation_required"):
        _assemble(factory, backend, request)
    return package_id


def test_retry_render_inventories_the_failed_claim_and_renders_attempt_two(
        factory: Callable[[], Session]) -> None:
    backend, toolchain, request = _setup()
    package_id = _failed_package_id(factory, backend, request)
    pkg = _retry(factory, backend, request, package_id)
    assert pkg.media_package_id == package_id
    assert pkg.status.value == "ready_for_publish"
    with factory() as session:
        jobs = {row.attempt_count: row for row in session.execute(
            sa.select(render_jobs_table)).all()}
        assert jobs[1].job_status == RenderJobStatus.QUARANTINED.value
        assert jobs[2].job_status == RenderJobStatus.COMPLETED.value
        evidence = session.execute(sa.select(
            render_recovery_evidence_table.c.evidence_kind,
            render_recovery_evidence_table.c.method,
            render_recovery_evidence_table.c.detail_json)).all()
    inventory = [e for e in evidence if e.evidence_kind == "orphan_artifacts"]
    assert [e.method for e in inventory] == ["claim_namespace_inventory"]
    assert inventory[0].detail_json["object_count"] >= 1  # the stored background
    assert pkg.render_report["render_job_id"] == jobs[2].id
    assert toolchain.calls.count("audio") == 1


def test_a_retry_that_writes_nothing_needs_the_operators_evidence(
        factory: Callable[[], Session]) -> None:
    """A render that failed before writing any object has nothing to list under an
    unverified brand; the operator's evidence reference is recorded instead."""
    backend, toolchain, request = _setup()
    toolchain.crashes = 0
    with (patch("media_factory.rendering.assembler.MediaRenderer.render",
                side_effect=MediaInfrastructureError("artifact_store_unavailable", "down")),
          pytest.raises(MediaInfrastructureError)):
        _assemble(factory, backend, request)
    with factory() as session:
        package_id = session.execute(
            sa.select(render_jobs_table.c.media_package_id)).scalar_one()
    with pytest.raises(MediaAssemblyError, match="render_retry_not_prepared"):
        _retry(factory, backend, request, package_id)
    pkg = _retry(factory, backend, request, package_id,
                 orphan_evidence_ref="checked renders/brand_neon prefix by hand")
    assert pkg.status.value == "ready_for_publish"


def test_a_retry_with_another_request_keeps_the_staged_attempt_for_the_right_one(
        factory: Callable[[], Session]) -> None:
    backend, _, request = _setup()
    package_id = _failed_package_id(factory, backend, request)
    other = request.model_copy(update={"playlist_concept": "Another Mood"})
    with pytest.raises(MediaAssemblyError, match="render_retry_input_changed"):
        _retry(factory, backend, other, package_id)
    pkg = _retry(factory, backend, request, package_id)
    assert pkg.status.value == "ready_for_publish"
    with factory() as session:
        assert session.execute(sa.select(sa.func.count()).select_from(
            render_jobs_table)).scalar_one() == 2


def test_retry_render_refuses_what_it_cannot_retry(factory: Callable[[], Session]) -> None:
    backend, toolchain, request = _setup()
    with pytest.raises(MediaAssemblyError, match="render_job_unknown"):
        _retry(factory, backend, request, "mpkg_none")
    toolchain.crashes = 0
    pkg = _assemble(factory, backend, request)
    with pytest.raises(MediaAssemblyError, match="render_retry_package_exists"):
        _retry(factory, backend, request, pkg.media_package_id)


def test_a_changed_input_for_an_earlier_attempt_is_a_render_intent_conflict(
        factory: Callable[[], Session]) -> None:
    """The same package queued earlier with other inputs was a bare ``ValueError``
    (a 500); it is the refusal ``render_intent_conflict``."""
    backend, _, request = _setup()
    _failed_package_id(factory, backend, request)
    other = request.model_copy(update={"playlist_concept": "Another Mood"})
    with pytest.raises(MediaAssemblyError, match="render_intent_conflict"):
        _assemble(factory, backend, other)


def test_the_retry_route_renders_again_and_answers_409_for_a_job_state(
        factory: Callable[[], Session]) -> None:
    backend, _, request = _setup()
    platform = load_settings(env=ENV)
    client = TestClient(create_app(
        session_factory=factory, settings=MediaFactorySettings(platform=platform),
        clock=FixedClock(NOW), backend=backend))
    headers = {SERVICE_TOKEN_HEADER: "t"}
    body = request.model_dump(mode="json")
    assert client.post("/media/packages/assemble", json=body,
                       headers=headers).status_code == 503
    blocked = client.post("/media/packages/assemble", json=body, headers=headers)
    assert blocked.status_code == 409
    with factory() as session:
        package_id = session.execute(
            sa.select(render_jobs_table.c.media_package_id)).scalar_one()
    other = client.post("/media/packages/assemble", headers=headers,
                        json={**body, "playlist_concept": "Another Mood"})
    assert other.status_code == 409
    assert other.json()["detail"]["code"] == "render_intent_conflict"
    done = client.post(f"/media/packages/{package_id}/retry-render", headers=headers,
                       json={"assembly": body, "recorded_by": "owner"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "ready_for_publish"
    again = client.post(f"/media/packages/{package_id}/retry-render", headers=headers,
                        json={"assembly": body, "recorded_by": "owner"})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "render_retry_package_exists"


def test_the_retry_render_command(factory: Callable[[], Session], tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _, request = _setup()
    package_id = _failed_package_id(factory, backend, request)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(env=ENV))
    monkeypatch.setattr(cli, "platform_session_factory", lambda: factory)
    monkeypatch.setattr(cli, "deployed_backend", lambda *_: backend)
    monkeypatch.setattr(cli, "load_media_factory_settings",
                        lambda **_: MediaFactorySettings(
                            default_render_profile="preview_360p_v1"))
    done = CliRunner().invoke(app, ["media", "retry-render", package_id, "--request",
                                    str(path), "--recorded-by", "owner"])
    assert done.exit_code == 0, done.output
    assert f"Media Package ID:    {package_id}" in done.output
    again = CliRunner().invoke(app, ["media", "retry-render", package_id, "--request",
                                     str(path), "--recorded-by", "owner"])
    assert again.exit_code == 2
    assert "render_retry_package_exists" in again.output
