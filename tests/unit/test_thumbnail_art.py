"""Owner thumbnail art: style guide prompts, image checks, the P6 hold and the import.

Real P6 assembly on SQLite with a stand-in toolchain and in-memory store: it proves the
hold, the job folder and the release, not the art itself or a real render.
"""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from media_factory.api.schemas import AssemblePackageRequest
from media_factory.domain.enums import PackageStatus
from media_factory.errors import MediaAssemblyError
from media_factory.service import assemble_and_persist
from media_factory.storage.repositories import get_media_package_by_id
from media_factory.thumbnail_art.guide import (
    ART_VARIANTS,
    DEFAULT_STYLE,
    build_prompts,
    load_style_guide,
)
from media_factory.thumbnail_art.importer import import_owner_art
from media_factory.thumbnail_art.inbox import MAX_BYTES, check_image, write_job
from PIL import Image
from plm_contracts.seams.c6 import MediaPackagePayload
from plm_platform.clock import FixedClock

from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
GUIDE = load_style_guide()


def _png(size: tuple[int, int], *, flat: bool = False) -> bytes:
    image = (Image.new("RGB", size, "#334455") if flat
             else Image.effect_noise(size, 40).convert("RGB"))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_variant_a_is_p2s_style_and_b_c_are_two_other_styles() -> None:
    prompts = build_prompts(GUIDE, package_id="mpkg_1", thumbnail_family="electric_city_night",
                            visual_world="observatory_workspace")
    assert [p.variant_key for p in prompts] == list(ART_VARIANTS)
    assert prompts[0].style_id == "electric_city_night"
    assert len({p.style_id for p in prompts}) == 3
    assert len({GUIDE.styles[p.style_id]["look"] for p in prompts}) == 3
    assert len({p.text.casefold() for p in prompts}) == 3
    for p in prompts:
        assert p.text in GUIDE.styles[p.style_id]["texts"]
        assert f'"{p.text}"' in p.prompt
        assert all(rule in p.prompt for rule in GUIDE.rules)
        assert "pinned" not in p.prompt and "{" not in p.prompt
    assert prompts == build_prompts(GUIDE, package_id="mpkg_1",
                                    thumbnail_family="electric_city_night",
                                    visual_world="observatory_workspace")


def test_every_package_gets_three_different_looks_and_phrases() -> None:
    """Guide 2026-11-05: across many packages and every A style, B and C never share a
    look with A or each other, and no phrase repeats (two DEEP WORKs taught nothing)."""
    for family in GUIDE.styles:
        for n in range(40):
            prompts = build_prompts(GUIDE, package_id=f"mpkg_{n:016x}",
                                    thumbnail_family=family,
                                    visual_world="architect_studio_night")
            assert prompts[0].style_id == family
            assert len({GUIDE.styles[p.style_id]["look"] for p in prompts}) == 3
            assert len({p.text.casefold() for p in prompts}) == 3
    vista = build_prompts(GUIDE, package_id="mpkg_1", thumbnail_family=DEFAULT_STYLE,
                          visual_world="observatory_workspace")[0]
    assert GUIDE.worlds["observatory_workspace"]["vista"] in vista.prompt


def test_an_unknown_style_or_world_falls_back_to_the_guide_default() -> None:
    prompts = build_prompts(GUIDE, package_id="mpkg_2", thumbnail_family="illustrated_lofi",
                            visual_world="not_a_world")
    assert prompts[0].style_id == DEFAULT_STYLE
    assert GUIDE.default_world["vista"] in prompts[0].prompt


def test_every_focus_template_thumbnail_family_is_a_guide_style() -> None:
    raw = json.loads(Path("services/experiments/genome/templates/genome_templates.json")
                     .read_text(encoding="utf-8"))
    base = next(t for name, t in raw["templates"].items() if name == "focus_electronic_base")
    assert base["thumbnail_family"] in GUIDE.styles
    assert set(base["alternatives"]["thumbnail_family"]) == set(GUIDE.styles)


@pytest.mark.parametrize(("size", "flat", "problem"), [
    ((1280, 960), False, "not 16:9"),
    ((960, 540), False, "at least 1280"),
    ((1920, 1080), True, "flat fill"),
])
def test_bad_images_are_refused_with_the_reason(size: tuple[int, int], flat: bool,
                                                problem: str) -> None:
    result = check_image(_png(size, flat=flat))
    assert result.problem is not None and problem in result.problem


def test_a_good_image_is_resized_to_1280x720_and_kept_under_2_mb() -> None:
    assert check_image(b"not an image").problem is not None
    result = check_image(_png((1920, 1080)))
    assert result.problem is None and len(result.data) <= MAX_BYTES
    assert Image.open(io.BytesIO(result.data)).size == (1280, 720)
    assert result.content_type in {"image/png", "image/jpeg"}


def test_a_written_job_is_never_rewritten(tmp_path: Path) -> None:
    prompts = build_prompts(GUIDE, package_id="mpkg_3", thumbnail_family=None,
                            visual_world="blueprint_hall")
    folder = write_job(tmp_path, package_id="mpkg_3", title="T", guide_version="g1",
                       prompts=prompts)
    first = (folder / "job.json").read_text(encoding="utf-8")
    assert "A.png" in (folder / "PROMPTS.md").read_text(encoding="utf-8")
    write_job(tmp_path, package_id="mpkg_3", title="Other", guide_version="g2",
              prompts=prompts[:1])
    assert (folder / "job.json").read_text(encoding="utf-8") == first


def test_a_rewrite_replaces_the_prompts_and_moves_saved_images_aside(tmp_path: Path) -> None:
    prompts = build_prompts(GUIDE, package_id="mpkg_4", thumbnail_family=None,
                            visual_world="blueprint_hall")
    folder = write_job(tmp_path, package_id="mpkg_4", title="T", guide_version="g1",
                       prompts=prompts)
    (folder / "A.jpg").write_bytes(b"old art")
    write_job(tmp_path, package_id="mpkg_4", title="T", guide_version="g2",
              prompts=prompts, rewrite=True)
    assert json.loads((folder / "job.json").read_text(encoding="utf-8"))["guide_version"] == "g2"
    assert not (folder / "A.jpg").exists()
    assert (folder / "previous-g1" / "A.jpg").read_bytes() == b"old art"


def test_a_package_waits_for_art_and_the_import_releases_it(tmp_path: Path) -> None:
    from youtube_publishing.manifests import PackageNotReadyError, build_publish_manifest

    engine = create_media_engine(static_pool=True)
    store = InMemoryArtifactStore()
    manifests = [stored_manifest(store, f"art_track_{n}", measured_duration_seconds=60,
                                 synth=spec) for n, spec in enumerate(synth_specs())]
    toolchain = StandInToolchain()
    backend = backend_for(store, manifests, toolchain=toolchain)
    request = AssemblePackageRequest.model_validate({
        "candidate_ids": [m.candidate_id for m in manifests], "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "observatory_workspace", "playlist_concept": "Deep Focus",
        "render_profile": "preview_360p_v1", "thumbnail_family": "monumental_minimal",
    })
    factory = lambda: media_session(engine)
    try:
        with factory() as session:
            pkg = assemble_and_persist(
                session, backend, request, clock=FixedClock(NOW),
                default_render_profile="preview_360p_v1", render_session_factory=factory,
                worker_id_factory=lambda: "w1", thumbnail_art_inbox=tmp_path)
            session.commit()
        assert pkg.status is PackageStatus.AWAITING_THUMBNAIL_ART
        # The first render is a still placeholder: the owner's art replaces it.
        assert "still" in toolchain.calls and "video" not in toolchain.calls
        folder = tmp_path / pkg.media_package_id
        job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        assert job["variants"][0]["style_id"] == "monumental_minimal"
        payload = MediaPackagePayload.model_validate(pkg.to_c6_payload())
        with pytest.raises(PackageNotReadyError, match="awaiting_thumbnail_art"):
            build_publish_manifest(payload, object(), publish_at=None,  # type: ignore[arg-type]
                                   now=NOW)

        (folder / "A.png").write_bytes(_png((1920, 1080)))
        (folder / "b.jpg").write_bytes(_png((1280, 960)))
        with factory() as session:
            with pytest.raises(MediaAssemblyError) as refused:
                import_owner_art(session, store, toolchain, pkg.media_package_id, folder,
                                 now=NOW)
            session.rollback()
        assert "art_b (b.jpg): 1280x960 is not 16:9" in str(refused.value)
        assert "art_c: no image saved" in str(refused.value)
        with factory() as session:
            still = get_media_package_by_id(session, pkg.media_package_id)
            assert still is not None and still.status is PackageStatus.AWAITING_THUMBNAIL_ART

        (folder / "b.jpg").unlink()
        (folder / "B.png").write_bytes(_png((2560, 1440)))
        (folder / "C.webp").write_bytes(_png((1920, 1080)))
        with factory() as session:
            released = import_owner_art(session, store, toolchain, pkg.media_package_id,
                                        folder, now=NOW)
            session.commit()
        assert released.status is PackageStatus.READY_FOR_PUBLISH
        assert [t["id"] for t in released.thumbnails] == list(ART_VARIANTS)
        assert all("/owner_art/" in t["uri"] for t in released.thumbnails)
        # The video is now art A held still over the same audio master (owner, 2026-11-04).
        assert toolchain.calls.count("still") == 2  # the placeholder, then the art
        assert released.video_master_uri != pkg.video_master_uri
        assert "/owner_art/video_master" in released.video_master_uri
        assert released.video_checksum != pkg.video_checksum
        assert released.audio_checksum == pkg.audio_checksum
        assert released.manifest_hash != pkg.manifest_hash
        still = released.render_report["video_pipeline"]["still_image"]
        assert still["variant_key"] == "art_a" and still["source"] == "A.png"
        assert released.render_report["video_master"]["checksum"] == released.video_checksum
        assert released.render_report["qa"]["passed"] is True
        assert store.get(released.video_master_uri).startswith(b"STAND-IN STILL VIDEO")

        with factory() as session:
            replay = assemble_and_persist(
                session, backend, request, clock=FixedClock(NOW),
                default_render_profile="preview_360p_v1", render_session_factory=factory,
                worker_id_factory=lambda: "w2", thumbnail_art_inbox=tmp_path)
        assert replay.status is PackageStatus.READY_FOR_PUBLISH
        with factory() as session, pytest.raises(MediaAssemblyError,
                                                 match="thumbnail_art_not_awaited"):
            import_owner_art(session, store, toolchain, pkg.media_package_id, folder, now=NOW)
    finally:
        engine.dispose()


def test_a_still_video_that_fails_qa_keeps_the_package_waiting(tmp_path: Path) -> None:
    engine = create_media_engine(static_pool=True)
    store = InMemoryArtifactStore()
    manifests = [stored_manifest(store, f"qa_track_{n}", measured_duration_seconds=60,
                                 synth=spec) for n, spec in enumerate(synth_specs())]
    toolchain = StandInToolchain()
    backend = backend_for(store, manifests, toolchain=toolchain)
    request = AssemblePackageRequest.model_validate({
        "candidate_ids": [m.candidate_id for m in manifests], "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "observatory_workspace", "playlist_concept": "Deep Focus",
        "render_profile": "preview_360p_v1",
    })
    factory = lambda: media_session(engine)
    try:
        with factory() as session:
            pkg = assemble_and_persist(
                session, backend, request, clock=FixedClock(NOW),
                default_render_profile="preview_360p_v1", render_session_factory=factory,
                worker_id_factory=lambda: "w1", thumbnail_art_inbox=tmp_path)
            session.commit()
        folder = tmp_path / pkg.media_package_id
        for name in ("A.png", "B.png", "C.png"):
            (folder / name).write_bytes(_png((1920, 1080)))
        toolchain.probe_overrides["pixel_format"] = "yuvj420p"
        with factory() as session:
            with pytest.raises(MediaAssemblyError, match="thumbnail_art_video_failed"):
                import_owner_art(session, store, toolchain, pkg.media_package_id, folder,
                                 now=NOW)
            session.rollback()
        with factory() as session:
            still = get_media_package_by_id(session, pkg.media_package_id)
            assert still is not None and still.status is PackageStatus.AWAITING_THUMBNAIL_ART
            assert still.video_checksum == pkg.video_checksum
    finally:
        engine.dispose()


def test_every_phrase_says_focus_or_work() -> None:
    """The channel is focus music for work (ADR-0044): 'STILLNESS' or 'SILENT HOURS'
    read as sleep or meditation, so every phrase names focus, work, flow or locking in."""
    words = ("focus", "work", "flow", "lock in", "distraction", "hour")
    for style_id, style in GUIDE.styles.items():
        for text in style["texts"]:
            assert any(w in text.casefold() for w in words), (style_id, text)
