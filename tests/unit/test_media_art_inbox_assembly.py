"""Assembly into the owner's thumbnail art inbox: what it must and must not do (review
2026-11-26, P6 findings 23 and the inbox-mode notes)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from media_factory.api.schemas import AssemblePackageRequest
from media_factory.domain.enums import PackageStatus, RenderJobStatus
from media_factory.domain.models import MediaPackage
from media_factory.errors import MediaAssemblyError
from media_factory.service import assemble_and_persist
from media_factory.storage.repositories import get_media_package_by_id, get_render_job
from plm_platform.clock import FixedClock
from sqlalchemy.orm import Session

from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)


@pytest.fixture
def factory() -> Iterator[Callable[[], Session]]:
    engine = create_media_engine(static_pool=True)
    try:
        yield lambda: media_session(engine)
    finally:
        engine.dispose()


def _assemble(factory: Callable[[], Session], inbox: Path | None,
              **overrides: Any) -> tuple[MediaPackage, InMemoryArtifactStore]:
    store = InMemoryArtifactStore()
    manifests = [stored_manifest(store, f"inbox_track_{n}", measured_duration_seconds=60,
                                 synth=spec) for n, spec in enumerate(synth_specs())]
    backend = backend_for(store, manifests, toolchain=StandInToolchain())
    request = AssemblePackageRequest.model_validate({
        "candidate_ids": [m.candidate_id for m in manifests], "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "observatory_workspace", "playlist_concept": "Deep Focus",
        "render_profile": "preview_360p_v1", **overrides,
    })
    with factory() as session:
        pkg = assemble_and_persist(
            session, backend, request, clock=FixedClock(NOW),
            default_render_profile="preview_360p_v1", render_session_factory=factory,
            worker_id_factory=lambda: "w1", thumbnail_art_inbox=inbox)
        session.commit()
    return pkg, store


def test_a_replay_with_another_visual_world_is_refused(
        factory: Callable[[], Session]) -> None:
    """The package id does not include the visual world, so the same songs asked for
    in another world replayed the stored package with the old one, silently."""
    pkg, _ = _assemble(factory, None)
    again, _ = _assemble(factory, None)
    assert again.media_package_id == pkg.media_package_id
    with pytest.raises(MediaAssemblyError, match="package_visual_profile_conflict"):
        _assemble(factory, None, visual_profile="blueprint_hall")


def test_a_failed_art_job_write_does_not_strand_the_render_claim(
        factory: Callable[[], Session], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Writing the art job is the last, non-fatal step: the package and its completed
    render job are committed even when the inbox cannot be written (#23)."""
    def broken(*args: Any, **kwargs: Any) -> Path:
        raise OSError("inbox drive unplugged")

    monkeypatch.setattr("media_factory.service.write_job", broken)
    pkg, _ = _assemble(factory, tmp_path)
    assert pkg.status is PackageStatus.AWAITING_THUMBNAIL_ART
    with factory() as session:
        stored = get_media_package_by_id(session, pkg.media_package_id)
        assert stored is not None
        job = get_render_job(session, str(pkg.render_report["render_job_id"]))
        assert job is not None and job.job_status is RenderJobStatus.COMPLETED
