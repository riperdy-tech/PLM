"""P6 draws each package's visual world (owner decision 2026-10-31).

Before this, every video background and waveform came from the brand palette and
thumbnail A always drew rain, whatever world P3 had sent.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

from media_factory.artwork.generator import render_background, render_thumbnail
from media_factory.rendering.profiles import RENDER_PROFILES
from media_factory.visuals.planner import BrandProfile, plan_visual_session
from media_factory.visuals.worlds import WORLDS, WORLDS_VERSION
from PIL import Image

from services.experiments.genome.engine import TEMPLATES_PATH

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
BRAND = BrandProfile(brand_id="brand_neon", brand_name="Neon Beats",
                     palette=["#111111", "#222222", "#33AAFF", "#FF44AA"])


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _plan(world: str) -> dict[str, object]:
    return plan_visual_session(
        playlist_session_id_str="ps_world", brand_profile=BRAND, visual_profile=world,
        render_profile=RENDER_PROFILES["yt_longform_1080p_v1"], total_duration_seconds=180.0,
        now=NOW,
    ).visual_session.visual_plan_json


def test_every_world_the_p3_templates_name_has_a_look() -> None:
    templates = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))["templates"]
    named = set()
    for template in templates.values():
        if "visual_world" in template:
            named.add(template["visual_world"])
        named.update(template.get("alternatives", {}).get("visual_world", []))
    assert named <= set(WORLDS)


def test_every_waveform_reads_against_its_own_sky() -> None:
    for name, world in WORLDS.items():
        assert min(_contrast(world.glow, world.sky_top),
                   _contrast(world.glow, world.sky_bottom)) >= 3.0, name


def test_the_plan_draws_the_world_and_says_so() -> None:
    plan = _plan("misty_forest")
    world = WORLDS["misty_forest"]
    assert plan["background"] == {"style": "vertical_gradient",
                                  "colors": [world.sky_top, world.sky_bottom]}
    assert plan["visualizer"]["color"] == world.glow  # type: ignore[index]
    assert plan["visual_world"] == {"name": "misty_forest", "known": True,
                                    "version": WORLDS_VERSION}


def test_an_unknown_world_keeps_the_brand_look_and_is_recorded() -> None:
    plan = _plan("neon_v1")
    assert plan["background"]["colors"] == ["#111111", "#222222"]  # type: ignore[index]
    assert plan["visualizer"]["color"] == "#33AAFF"  # type: ignore[index]
    assert plan["visual_world"]["known"] is False  # type: ignore[index]
    concept = "Late Night"
    assert render_thumbnail("thumb_a_hero", BRAND, concept, "neon_v1") == render_thumbnail(
        "thumb_a_hero", BRAND, concept)


def test_thumbnail_a_and_the_background_differ_by_world() -> None:
    concept = "Late Night Study Session"
    heroes = {name: render_thumbnail("thumb_a_hero", BRAND, concept, name)
              for name in ("misty_forest", "neon_tokyo", "snowy_mountains", "night_ocean")}
    assert len(set(heroes.values())) == len(heroes)
    for name, data in heroes.items():
        assert render_thumbnail("thumb_a_hero", BRAND, concept, name) == data  # deterministic
        image = Image.open(io.BytesIO(data)).convert("RGB")
        assert image.size == (1280, 720)
        # The window's top-left sky pixel is the world's own sky colour.
        top = WORLDS[name].sky_top
        expected = tuple(int(top[i:i + 2], 16) for i in (1, 3, 5))
        pixel = image.getpixel((720, 90))
        assert all(abs(a - b) <= 3 for a, b in zip(pixel, expected, strict=True)), name
    world = WORLDS["night_ocean"]
    background = Image.open(io.BytesIO(render_background(
        BRAND, 64, 32, colors=(world.sky_top, world.sky_bottom)))).convert("RGB")
    assert background.getpixel((0, 0)) == tuple(
        int(world.sky_top[i:i + 2], 16) for i in (1, 3, 5))
