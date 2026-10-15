"""Visual plan, thumbnails and metadata (P6 §13, §18, §21, R4).

Proves: the visual plan states exactly what the renderer draws and records no asset it
has not produced; thumbnails A/B/C are real 1280x720 PNGs, deterministic (same inputs,
same bytes), distinct per variant, stored content-addressed with the sha256 of the
stored bytes; an invalid brand palette is refused; chapters come from the final
timeline without being moved.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime

import pytest
from media_factory.artwork.generator import (
    THUMBNAIL_VARIANTS,
    generate_thumbnails,
    render_thumbnail,
)
from media_factory.domain.models import PlaylistTrack
from media_factory.errors import MediaAssemblyError
from media_factory.metadata.compiler import compile_metadata_and_chapters
from media_factory.rendering.profiles import RENDER_PROFILES
from media_factory.visuals.planner import BrandProfile, plan_visual_session
from PIL import Image

from tests.support.media_backend import InMemoryArtifactStore

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
BRAND = BrandProfile(brand_id="brand_neon", brand_name="Neon Beats",
                     palette=["#111111", "#222222", "#33AAFF", "#FF44AA"])


def test_visual_plan_states_what_is_rendered_and_records_no_unproduced_asset() -> None:
    profile = RENDER_PROFILES["yt_longform_1080p_v1"]
    res = plan_visual_session(
        playlist_session_id_str="ps_test_01", brand_profile=BRAND, visual_profile="neon_v1",
        render_profile=profile, total_duration_seconds=180.0, now=NOW,
    )
    plan = res.visual_session.visual_plan_json
    assert plan["canvas"] == {"width": 1920, "height": 1080, "fps": 30}
    assert plan["visualizer"]["color"] == "#33AAFF"
    assert plan["background"]["colors"] == ["#111111", "#222222"]
    assert res.assets == []


def test_thumbnails_are_deterministic_real_images() -> None:
    concept = "Late Night Synthwave"
    renders = {v: render_thumbnail(v, BRAND, concept) for v in THUMBNAIL_VARIANTS}
    for variant, data in renders.items():
        assert render_thumbnail(variant, BRAND, concept) == data  # same inputs, same bytes
        image = Image.open(io.BytesIO(data))
        assert image.format == "PNG"
        assert image.size == (1280, 720)
        assert len(data) < 2 * 1024 * 1024
    assert len(set(renders.values())) == 3
    assert render_thumbnail("thumb_a_hero", BRAND, "Another Concept") != renders["thumb_a_hero"]


def test_thumbnails_are_stored_with_the_checksum_of_the_stored_bytes() -> None:
    store = InMemoryArtifactStore()
    variants = generate_thumbnails(media_package_id_str="mpkg_x", brand_profile=BRAND,
                                   playlist_concept="Focus", store=store, now=NOW)
    assert [v.variant_key for v in variants] == list(THUMBNAIL_VARIANTS)
    for variant in variants:
        assert hashlib.sha256(store.get(variant.uri)).hexdigest() == variant.checksum
        assert variant.uri.endswith(variant.checksum)


def test_an_invalid_palette_is_refused() -> None:
    bad = BrandProfile(brand_id="b", brand_name="B", palette=["#111111", "teal", "#000000",
                                                              "#000000"])
    with pytest.raises(MediaAssemblyError) as raised:
        render_thumbnail("thumb_c_minimal", bad, "Focus")
    assert raised.value.code == "brand_palette_invalid"


def test_chapters_come_from_the_final_timeline() -> None:
    tracks = [
        PlaylistTrack.create(session_id="ps_01", song_id=f"s{n}", source_artifact_id=f"a{n}",
                             position=n, start_offset_seconds=start, end_offset_seconds=end,
                             title=f"Track {n + 1}", now=NOW)
        for n, (start, end) in enumerate([(0.0, 120.0), (118.0, 250.0), (248.0, 400.0)])
    ]
    res = compile_metadata_and_chapters(tracks=tracks, brand_name="Neon Beats",
                                        playlist_concept="Night Driving",
                                        ai_disclosure_required=True)
    assert [c["start_seconds"] for c in res.chapters] == [0.0, 118.0, 248.0]
    assert [c["end_seconds"] for c in res.chapters] == [118.0, 248.0, 400.0]
    assert [c["timestamp_str"] for c in res.chapters] == ["00:00", "01:58", "04:08"]
    assert len(res.tracklist) == 3
    for meta in res.metadata_candidates:
        assert meta["contains_synthetic_media"] is True
        assert "Notice: Sound recording" in meta["description"]
