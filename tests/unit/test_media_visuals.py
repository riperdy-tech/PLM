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
import re
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
from media_factory.metadata.compiler import HASHTAGS, compile_metadata_and_chapters
from media_factory.metadata.packaging import (
    DEFAULT_GENRE,
    GENRES,
    SCENES,
    SERIES,
    choose_titles,
)
from media_factory.rendering.profiles import RENDER_PROFILES
from media_factory.visuals.planner import BrandProfile, plan_visual_session
from media_factory.visuals.worlds import WORLDS
from PIL import Image, ImageDraw, ImageFont

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
        assert variant.layout_version.startswith("thumb-layout-v6+")


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
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size >= minimum
        assert any(line.endswith("…") for line in lines)
        assert all(draw.textlength(line, font=font) <= width for line in lines)
        again = _fit(draw, title, width, start, minimum=minimum)
        assert isinstance(again[0], ImageFont.FreeTypeFont)
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
        assert "Notice:" not in meta["description"]  # the API flag discloses AI use


def _tracks(*spans: tuple[float, float]) -> list[PlaylistTrack]:
    return [
        PlaylistTrack.create(session_id="ps_01", song_id=f"s{n}", source_artifact_id=f"a{n}",
                             position=n, start_offset_seconds=start, end_offset_seconds=end,
                             title=name, now=NOW)
        for n, ((start, end), name) in enumerate(
            zip(spans, ("Amber Circuit", "Quiet Lattice", "Linear Draft"), strict=False))
    ]


def test_titles_lead_with_function_and_genre_and_state_the_length() -> None:
    """Owner 2026-11-17, on P1's market data: titles say what the video is for and what
    it sounds like (function, the sub-style's genre, length or a feature), never a
    poetic scene line; three candidates in different formats."""
    tracks = _tracks((0.0, 2400.0), (2398.0, 4800.0), (4798.0, 7200.0))
    res = compile_metadata_and_chapters(tracks=tracks, brand_name="Longform Signal",
                                        playlist_concept="focused focus_electronic for x",
                                        visual_world="rainy_night_desk",
                                        subgenre="restrained_retro_future", seed="mpkg_1")
    scene, genre = res.hooks
    assert scene == SCENES["rainy_night_desk"] and genre == "Retro Synth"
    a, b, c = res.metadata_candidates
    assert [m["variant_id"] for m in (a, b, c)] == ["meta_a", "meta_b", "meta_c"]
    titles = [m["title"] for m in (a, b, c)]
    assert len(set(titles)) == 3 and any("2 Hours" in t for t in titles)
    leads = ("Deep Work Music", "Focus Music", "Concentration Music", "2 Hours", "Retro Synth")
    for meta in res.metadata_candidates:
        title = meta["title"]
        assert "Retro Synth" in title and title.startswith(leads), title
        assert scene not in title and "Rainy" not in title
        assert len(title) <= 100 and "focus_electronic" not in title
        for banned in ("Official Mix", "Full Album", "Vol.", "lofi", "Lofi", "Relaxing"):
            assert banned not in title
        assert "longformsignal" in meta["tags"] and 5 <= len(meta["tags"]) <= 10
        assert {"music for studying", "concentration music"} <= set(meta["tags"])
        assert meta["category_id"] == "10"
        lines = meta["description"].split("\n")
        assert lines[0].startswith(f"{scene}: 2 hours of calm instrumental retro synth for ")
        assert lines[1].startswith("No lyrics and no vocals")
        assert "00:00 - Amber Circuit" in meta["description"]
        for owner_cut in ("Music:", "Visuals:", "Notice:", "Curated by", "Series:"):
            assert owner_cut not in meta["description"]
        assert lines[-2:] == ["", HASHTAGS] and len(HASHTAGS.split()) <= 3
    assert [c["title"] for c in res.chapters] == ["Amber Circuit", "Quiet Lattice",
                                                  "Linear Draft"]
    assert [t["title"] for t in res.tracklist] == [c["title"] for c in res.chapters]


def _words(title: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", title.lower()))


def test_no_title_is_a_template_fill_of_a_recent_one() -> None:
    """The upload variation check refuses a title within two words of a recent one, so
    the titles are chosen around them instead of getting a run stuck."""
    tracks = _tracks((0.0, 2400.0), (2398.0, 4800.0), (4798.0, 7200.0))
    recent: list[str] = []
    for n in range(7):  # seven videos of one genre, world and length in a row
        res = compile_metadata_and_chapters(
            tracks=tracks, brand_name="LS", playlist_concept="x",
            visual_world="late_night_library", subgenre="minimal_pulse", seed=f"mpkg_{n}",
            recent_titles=recent)
        made = [m["title"] for m in res.metadata_candidates]
        assert len(made) == 3
        for title in made:
            for other in recent:
                assert len(_words(title) ^ _words(other)) > 2, (title, other)
        recent += made


def test_every_genre_and_length_fits_youtubes_title_limit() -> None:
    for genre in (*GENRES.values(), DEFAULT_GENRE):
        for length in ("25 Minutes", "1 Hour", "104 Minutes", "2 Hours"):
            made = choose_titles(genre=genre, length=length, seed=genre + length)
            assert len(made) == 3
            for title in made:
                assert len(title) <= 100, title
    tracks = _tracks((0.0, 700.0), (698.0, 1500.0))
    unknown = compile_metadata_and_chapters(tracks=tracks, brand_name="LS",
                                            playlist_concept="x", subgenre="no_such", seed="s")
    assert all(DEFAULT_GENRE in m["title"] for m in unknown.metadata_candidates)


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


def test_every_scene_has_its_own_wording_in_each_chosen_language() -> None:
    """ADR-0045 decision 2: de, nl, sv, ko, zh-Hant, ja; every focus world's scene
    written in each for the description; the title leads with the language's search
    phrase, then the genre and length, within 100 characters; no credit or AI lines."""
    from media_factory.metadata.localization import _SCENES, LANGUAGES, localize

    assert set(LANGUAGES) == {"de", "nl", "sv", "ko", "zh-Hant", "ja"}
    assert set(_SCENES) == set(SCENES)
    for world, scenes in _SCENES.items():
        assert set(scenes) == set(LANGUAGES), world
        out = localize(world, genre="Retro Synth", seconds=1500, chapter_block="00:00 - A")
        assert set(out) == set(LANGUAGES)
        for code, text in out.items():
            assert len(text["title"]) <= 100, (code, text["title"])
            assert text["title"].startswith(LANGUAGES[code].head)
            assert "Retro Synth" in text["title"] and scenes[code] not in text["title"]
            assert scenes[code] in text["description"].split("\n")[0]
            assert "00:00 - A" in text["description"]
            assert "AI" not in text["description"]
            assert "25" in text["description"]
    assert localize("night_driving", genre="X", seconds=60, chapter_block="") == {}


def test_the_package_carries_the_translations_on_every_candidate() -> None:
    tracks = _tracks((0.0, 700.0), (698.0, 1500.0))
    res = compile_metadata_and_chapters(tracks=tracks, brand_name="Longform Signal",
                                        playlist_concept="x", visual_world="late_night_library",
                                        seed="mpkg_2")
    first = res.metadata_candidates[0]["localizations"]
    assert set(first) == {"de", "nl", "sv", "ko", "zh-Hant", "ja"}
    assert all(m["localizations"] == first for m in res.metadata_candidates)
    assert first["ko"]["title"].startswith("집중할 때 듣는 음악 | 공부·작업용 BGM")


def test_a_focus_world_names_its_series_in_the_package() -> None:
    tracks = _tracks((0.0, 700.0), (698.0, 1500.0))
    res = compile_metadata_and_chapters(tracks=tracks, brand_name="LS", playlist_concept="x",
                                        visual_world="late_night_library", seed="s")
    assert {m["series"] for m in res.metadata_candidates} == {"night_desk"}
    assert "Series:" not in res.metadata_candidates[0]["description"]
    other = compile_metadata_and_chapters(tracks=tracks, brand_name="LS",
                                          playlist_concept="x", seed="s")
    assert {m["series"] for m in other.metadata_candidates} == {None}
    assert "Series:" not in other.metadata_candidates[0]["description"]
    assert len({series for series, _ in SERIES.values()}) == 3  # spec: 3-5 series
    assert set(SERIES) == set(SCENES) and set(SCENES) <= set(WORLDS)
