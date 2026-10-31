"""Visual plan, thumbnails and metadata (P6 §13, §18, §21, R4).

Proves: the visual plan states exactly what the renderer draws and records no asset it
has not produced; thumbnails A/B/C are real 1280x720 PNGs, deterministic (same inputs,
same bytes), distinct per variant, stored content-addressed with the sha256 of the
stored bytes; an invalid brand palette is refused; chapters come from the final
timeline without being moved; titles, labels and tags are functional focus-music ones
(ADR-0044) and every thumbnail carries the brand's logo mark.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime

import pytest
from media_factory.artwork.generator import (
    THUMBNAIL_VARIANTS,
    _contrast_ink,
    _fit,
    functional_label,
    generate_thumbnails,
    render_thumbnail,
)
from media_factory.domain.models import PlaylistTrack
from media_factory.errors import MediaAssemblyError
from media_factory.metadata.compiler import compile_metadata_and_chapters
from media_factory.rendering.profiles import RENDER_PROFILES
from media_factory.visuals.planner import BrandProfile, plan_visual_session
from PIL import Image, ImageDraw

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
        assert variant.text_payload == "Focus"
        assert variant.layout_version.startswith("thumb-layout-v5+")


@pytest.mark.parametrize(("width", "start", "minimum"), [
    (558, 87, 72),
    (1120, 120, 82),
    (980, 88, 72),
])
def test_thumbnail_title_fits_readably_even_when_very_long(
    width: int, start: int, minimum: int,
) -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (1280, 720)))
    for title in (
        "SUPERCALIFRAGILISTICEXPIALIDOCIOUS" * 12,
        "DEEP LATE NIGHT STUDY RAINY CITY LOFI FOCUS " * 40,
    ):
        font, lines = _fit(draw, title, width, start, minimum=minimum)
        assert len(lines) <= 3
        assert lines
        assert font.size >= minimum
        assert any(line.endswith("…") for line in lines)
        assert all(draw.textlength(line, font=font) <= width for line in lines)
        again = _fit(draw, title, width, start, minimum=minimum)
        assert (font.size, lines) == (again[0].size, again[1])


def test_pale_palette_still_has_dark_text_backing() -> None:
    light = BrandProfile(
        brand_id="light", brand_name="Light",
        palette=["#FAFAFA", "#F0F0F0", "#FADEB4", "#FFFFFF"],
    )
    for variant, point in (
        ("thumb_a_hero", (56, 680)),
        ("thumb_b_bold_text", (100, 140)),
        ("thumb_c_minimal", (120, 120)),
    ):
        png = render_thumbnail(variant, light, "Night Drive")
        image = Image.open(io.BytesIO(png))
        assert image.getpixel(point) == (12, 19, 38)


def test_accent_labels_choose_high_contrast_ink() -> None:
    assert _contrast_ink((255, 255, 255)) == (0, 0, 0)
    assert _contrast_ink((0, 0, 0)) == (255, 255, 255)
    assert _contrast_ink((15, 20, 32)) == (255, 255, 255)
    assert _contrast_ink((250, 222, 180)) == (0, 0, 0)


def test_blank_brand_label_keeps_legacy_rendering_safe() -> None:
    unnamed = BrandProfile(
        brand_id="brand_neon", brand_name=" ",
        palette=["#111111", "#222222", "#33AAFF", "#FF44AA"],
    )
    for variant in THUMBNAIL_VARIANTS:
        data = render_thumbnail(variant, unnamed, "Night Drive")
        assert Image.open(io.BytesIO(data)).size == (1280, 720)


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


def _tracks(*spans: tuple[float, float]) -> list[PlaylistTrack]:
    return [
        PlaylistTrack.create(session_id="ps_01", song_id=f"s{n}", source_artifact_id=f"a{n}",
                             position=n, start_offset_seconds=start, end_offset_seconds=end,
                             title=name, now=NOW)
        for n, ((start, end), name) in enumerate(
            zip(spans, ("Amber Circuit", "Quiet Lattice", "Linear Draft"), strict=False))
    ]


def test_titles_are_functional_and_state_the_length() -> None:
    tracks = _tracks((0.0, 2400.0), (2398.0, 4800.0), (4798.0, 7200.0))
    res = compile_metadata_and_chapters(tracks=tracks, brand_name="Longform Signal",
                                        playlist_concept="Deep Work Signal")
    a, b = res.metadata_candidates
    assert a["title"] == (
        "Deep Work Signal — 2 Hours of Instrumental Electronic Music for Coding & Focus")
    assert b["title"].startswith("Deep Work Signal — 2 Hours ")
    for meta in res.metadata_candidates:
        title = meta["title"]
        assert len(title) <= 100
        for banned in ("Official Mix", "Full Album", "Vol.", "lofi", "Lofi", "Relaxing"):
            assert banned not in title
        assert not {"lofi", "beats", "relaxing", "music"} & set(meta["tags"])
        assert "longformsignal" in meta["tags"]
        assert meta["category_id"] == "10"
        assert "Curated by Longform Signal" in meta["description"]
        assert "00:00 - Amber Circuit" in meta["description"]
        assert "Notice: Sound recording" in meta["description"]
    assert {"deep focus", "coding music", "programming music"} <= set(a["tags"])
    assert [c["title"] for c in res.chapters] == ["Amber Circuit", "Quiet Lattice",
                                                  "Linear Draft"]
    assert [t["title"] for t in res.tracklist] == [c["title"] for c in res.chapters]


def test_a_long_concept_is_shortened_so_the_title_fits() -> None:
    tracks = _tracks((0.0, 2700.0), (2698.0, 5400.0))
    res = compile_metadata_and_chapters(tracks=tracks, brand_name="Longform Signal",
                                        playlist_concept="Graphite Studio Night " * 8)
    for meta in res.metadata_candidates:
        assert 95 <= len(meta["title"]) <= 100
        assert meta["title"].startswith("Graphite Studio Night")
        assert "… — 90 Minutes " in meta["title"]  # the concept is cut, never the length


@pytest.mark.parametrize(("concept", "label"), [
    ("Deep Work Signal", "DEEP WORK"),
    ("Late Night Coding Session", "CODING"),
    ("Programming Flow", "CODING"),
    ("Exam Study Block", "STUDY FOCUS"),
    ("Graphite Focus", "DEEP FOCUS"),
    ("Amber Atrium", "DEEP WORK"),
    ("Decoder Ring", "DEEP WORK"),  # a keyword inside another word is not the keyword
])
def test_thumbnail_label_is_a_short_functional_phrase(concept: str, label: str) -> None:
    assert functional_label(concept) == label


def test_every_thumbnail_carries_the_brand_logo_mark() -> None:
    accent = (0x33, 0xAA, 0xFF)
    for variant in THUMBNAIL_VARIANTS:
        image = Image.open(io.BytesIO(render_thumbnail(variant, BRAND, "Deep Work")))
        assert image.convert("RGB").getpixel((1180, 14)) == accent, variant
    hero = Image.open(io.BytesIO(render_thumbnail("thumb_a_hero", BRAND, "Deep Work")))
    assert hero.convert("RGB").getpixel((80, 616)) == accent  # the label's tile
