"""The owner's review changes to a finished P6 package (``media_factory.review_edits``).

Real P6 assembly on SQLite with a stand-in toolchain and an in-memory store. Proves the
edits and the art reopen as P6 stores them, not a real render.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from media_factory.api.schemas import AssemblePackageRequest
from media_factory.domain.enums import PackageStatus
from media_factory.domain.models import MediaPackage
from media_factory.errors import MediaAssemblyError
from media_factory.review_edits import edit_presentation, reopen_art
from media_factory.service import assemble_and_persist
from media_factory.storage.repositories import get_media_package_by_id
from media_factory.thumbnail_art.importer import import_owner_art
from PIL import Image
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

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def _png(seed: int) -> bytes:
    image = Image.effect_noise((1920, 1080), 30 + seed).convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class World:
    def __init__(self, inbox: Path | None) -> None:
        self.engine = create_media_engine(static_pool=True)
        self.store = InMemoryArtifactStore()
        self.toolchain = StandInToolchain()
        manifests = [stored_manifest(self.store, f"edit_track_{n}",
                                     measured_duration_seconds=60, synth=spec)
                     for n, spec in enumerate(synth_specs())]
        backend = backend_for(self.store, manifests, toolchain=self.toolchain)
        request = AssemblePackageRequest.model_validate({
            "candidate_ids": [m.candidate_id for m in manifests], "brand_id": "brand_neon",
            "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
            "visual_profile": "observatory_workspace", "playlist_concept": "Deep Focus",
            "render_profile": "preview_360p_v1"})
        self.factory: Callable[[], Session] = lambda: media_session(self.engine)
        with self.factory() as session:
            self.pkg = assemble_and_persist(
                session, backend, request, clock=FixedClock(NOW),
                default_render_profile="preview_360p_v1",
                render_session_factory=self.factory, worker_id_factory=lambda: "w1",
                thumbnail_art_inbox=inbox)
            session.commit()

    def get(self) -> MediaPackage:
        with self.factory() as session:
            pkg = get_media_package_by_id(session, self.pkg.media_package_id)
        assert pkg is not None
        return pkg

    def call(self, fn: Callable[..., Any], **kwargs: Any) -> Any:
        with self.factory() as session:
            try:
                result = fn(session, self.pkg.media_package_id, now=NOW, **kwargs)
            except Exception:
                session.rollback()
                raise
            session.commit()
            return result


@pytest.fixture
def world() -> Iterator[World]:
    w = World(None)
    yield w
    w.engine.dispose()


def test_text_edits_apply_to_the_published_candidate_and_keep_the_video(world: World) -> None:
    before = world.get()
    assert before.status is PackageStatus.READY_FOR_PUBLISH
    stamps = [c["timestamp_str"] for c in before.chapters]
    description = "Calm focus music for late work.\n\n" + "\n".join(
        f"{s} - Track {n}" for n, s in enumerate(stamps, start=1))
    after = world.call(edit_presentation, title="  Late Night Focus  ",
                       description=description, tags=["focus music", " deep work ", ""])
    assert after.metadata_candidates[0]["title"] == "Late Night Focus"
    assert after.metadata_candidates[1]["title"] == before.metadata_candidates[1]["title"]
    assert {c["description"] for c in after.metadata_candidates} == {description}
    assert {tuple(c["tags"]) for c in after.metadata_candidates} == {("focus music",
                                                                     "deep work")}
    assert after.metadata_candidates[0]["localizations"] == \
        before.metadata_candidates[0]["localizations"]  # translations keep their text
    assert (after.video_checksum, after.manifest_hash) == (before.video_checksum,
                                                           before.manifest_hash)
    assert after.status is PackageStatus.READY_FOR_PUBLISH


@pytest.mark.parametrize(("edit", "code"), [
    ({"title": ""}, "title_invalid"),
    ({"title": "x" * 101}, "title_invalid"),
    ({"description": "no chapters here"}, "chapters_missing"),
    ({"description": "a <b> tag"}, "description_invalid"),
    ({"tags": [" "]}, "tags_invalid"),
    ({"thumbnail_id": "thumb_z"}, "thumbnail_unknown"),
])
def test_a_bad_edit_is_refused_and_changes_nothing(world: World, edit: dict[str, Any],
                                                   code: str) -> None:
    before = world.get()
    with pytest.raises(MediaAssemblyError, match=code):
        world.call(edit_presentation, **edit)
    assert world.get().metadata_candidates == before.metadata_candidates


def test_choosing_a_thumbnail_puts_it_first(world: World) -> None:
    ids = [t["id"] for t in world.get().thumbnails]
    after = world.call(edit_presentation, thumbnail_id=ids[2])
    assert [t["id"] for t in after.thumbnails] == [ids[2], ids[0], ids[1]]


def test_new_art_reopens_the_package_and_a_second_import_replaces_the_first(
        tmp_path: Path) -> None:
    world = World(tmp_path)
    try:
        folder = tmp_path / world.pkg.media_package_id
        for n, letter in enumerate("ABC"):
            (folder / f"{letter}.png").write_bytes(_png(n))
        with world.factory() as session:
            first = import_owner_art(session, world.store, world.toolchain,
                                     world.pkg.media_package_id, folder, now=NOW)
            session.commit()
        assert first.status is PackageStatus.READY_FOR_PUBLISH

        reopened = world.call(reopen_art)
        assert reopened.status is PackageStatus.AWAITING_THUMBNAIL_ART
        with pytest.raises(MediaAssemblyError, match="package_not_in_review"):
            world.call(edit_presentation, title="x")

        (folder / "A.png").write_bytes(_png(9))
        with world.factory() as session:
            second = import_owner_art(session, world.store, world.toolchain,
                                      world.pkg.media_package_id, folder, now=NOW)
            session.commit()
        assert second.status is PackageStatus.READY_FOR_PUBLISH
        assert second.thumbnails[0]["checksum"] != first.thumbnails[0]["checksum"]
        assert second.thumbnails[1]["checksum"] == first.thumbnails[1]["checksum"]
        assert second.video_checksum != first.video_checksum  # re-rendered from new A
    finally:
        world.engine.dispose()
