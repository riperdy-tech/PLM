"""Thumbnails A/B/C and the video background, drawn with Pillow (P6 §17, §18, R4).

Every image is a deterministic function of the brand profile, the playlist concept and
the variant: the same inputs give the same PNG bytes (on the same Pillow/FreeType
build). Nothing is random; the only "variation" is a motif derived from the sha256 of
the inputs. The bytes are stored content-addressed under ``media/`` and every record
carries the sha256 of the bytes stored.

Thumbnail designs, all 1280x720 (YouTube's recommended size, 16:9), PNG, under 2 MB:

* **A, ``thumb_a_hero``**: a window onto the package's visual world (its sky, light,
  weather and skyline from :mod:`..visuals.worlds`; a world P6 does not know keeps the
  original night-city scene) with prominent album typography plus a Full Album label.
* **B, ``thumb_b_bold_text``**: the brand gradient with the playlist concept in large
  type on the left, over an accent bar, and the brand name above it.
* **C, ``thumb_c_minimal``**: a flat dark field with a thin accent rule and the concept
  in small type centred.

The variants change presentation only (P6 §18): every one states the same concept.
The video background is the visual plan's gradient (the world's sky, or the brand's).
"""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from PIL import Image, ImageDraw, ImageFont

from ..artifacts import ArtifactStore, scoped_artifact_root
from ..domain.models import ThumbnailVariant
from ..errors import MediaAssemblyError
from ..visuals.planner import BrandProfile
from ..visuals.worlds import VisualWorld, visual_world

__all__ = [
    "LAYOUT_VERSION",
    "THUMBNAIL_HEIGHT",
    "THUMBNAIL_MAX_BYTES",
    "THUMBNAIL_VARIANTS",
    "THUMBNAIL_WIDTH",
    "generate_thumbnails",
    "render_background",
    "render_thumbnail",
]

THUMBNAIL_WIDTH: Final[int] = 1280
THUMBNAIL_HEIGHT: Final[int] = 720
THUMBNAIL_MAX_BYTES: Final[int] = 2 * 1024 * 1024
THUMBNAIL_VARIANTS: Final[tuple[str, ...]] = ("thumb_a_hero", "thumb_b_bold_text",
                                              "thumb_c_minimal")
LAYOUT_VERSION: Final[str] = "thumb-layout-v4"
_HEX: Final = re.compile(r"\A#[0-9A-Fa-f]{6}\Z")

RGB = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class _Palette:
    base: RGB
    shade: RGB
    accent: RGB
    highlight: RGB


def _rgb(value: str) -> RGB:
    if _HEX.match(value) is None:
        raise MediaAssemblyError("brand_palette_invalid", f"not a #RRGGBB colour: {value!r}",
                                 ["brand.palette"])
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _palette(brand: BrandProfile) -> _Palette:
    if len(brand.palette) < 4:
        raise MediaAssemblyError(
            "brand_palette_invalid",
            f"brand {brand.brand_id!r} declares {len(brand.palette)} colours; 4 are needed "
            "(base, shade, accent, highlight)", ["brand.palette"])
    base, shade, accent, highlight = (_rgb(c) for c in brand.palette[:4])
    return _Palette(base, shade, accent, highlight)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


def _gradient(width: int, height: int, top: RGB, bottom: RGB) -> Image.Image:
    column = Image.new("RGB", (1, height))
    for y in range(height):
        f = y / max(1, height - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * f) for a, b in zip(top, bottom,
                                                                           strict=True)))
    return column.resize((width, height), Image.Resampling.NEAREST)


def render_background(brand: BrandProfile, width: int, height: int,
                      colors: tuple[str, ...] | None = None) -> bytes:
    """The gradient the visualiser is drawn over: ``colors`` (the visual plan's, top to
    bottom) when given, else the brand's base to shade."""
    palette = _palette(brand)
    if colors:
        return _png(_gradient(width, height, _rgb(colors[0]), _rgb(colors[-1])))
    return _png(_gradient(width, height, palette.base, palette.shade))


def _scene(draw: ImageDraw.ImageDraw, world: VisualWorld, seed: str,
           box: tuple[int, int, int, int]) -> None:
    """The world's skyline and weather inside the window ``box`` (left, top, right, bottom)."""
    left, top, right, bottom = box
    width = right - left
    bottom_rgb = _rgb(world.sky_bottom)
    ground: RGB = (int(bottom_rgb[0] * 0.35), int(bottom_rgb[1] * 0.35),
                   int(bottom_rgb[2] * 0.35))
    glow = _rgb(world.glow)
    shapes = _motif(f"{world.skyline}|{seed}".encode(), 24)
    if world.skyline == "city":
        for i, height in enumerate(shapes[:22]):
            x = left + i * (width // 22)
            draw.rectangle((x, int(bottom - 120 - height * 135), x + width // 22 + 1, bottom),
                           fill=ground)
            if i % 3 == 0:
                draw.rectangle((x + 6, int(bottom - 100 - height * 120), x + 12,
                                int(bottom - 92 - height * 120)), fill=glow)
    elif world.skyline == "hills":
        points = [(left, bottom)] + [(left + i * width // 8, int(bottom - 80 - h * 110))
                                     for i, h in enumerate(shapes[:9])] + [(right, bottom)]
        draw.polygon(points, fill=ground)
    elif world.skyline == "waves":
        for row in range(6):
            y = bottom - 150 + row * 24
            for i in range(0, width, 40):
                draw.arc((left + i, y, left + i + 40, y + 16), 180, 360, fill=glow, width=2)
    elif world.skyline == "trees":
        for i, height in enumerate(shapes[:14]):
            x = left + i * (width // 14)
            peak = int(bottom - 150 - height * 150)
            draw.polygon([(x, bottom), (x + width // 28, peak), (x + width // 14, bottom)],
                         fill=ground)
    elif world.skyline == "road":
        mid = (left + right) // 2
        draw.polygon([(left, bottom), (mid - 12, bottom - 210), (mid + 12, bottom - 210),
                      (right, bottom)], fill=ground)
        for i in range(5):
            y = bottom - 30 - i * 40
            draw.rectangle((mid - 3, y, mid + 3, y + 18), fill=glow)
    else:  # room: a sill and a lamp in front of the window
        draw.rectangle((left, bottom - 70, right, bottom), fill=ground)
        draw.ellipse((right - 150, bottom - 120, right - 70, bottom - 70), fill=glow)
    weather = _motif(f"{world.weather}|{seed}".encode(), 64)
    height = bottom - top
    for i, amount in enumerate(weather):
        x = left + 18 + (i * 71) % (width - 30)
        y = top + 16 + int((i * 67 + amount * 47) % (height * 0.6))
        if world.weather == "rain":
            draw.line((x, y, x - 14, y + 27), fill=(93, 139, 177), width=2)
        elif world.weather == "snow":
            draw.ellipse((x, y, x + 6, y + 6), fill=(245, 248, 255))
        elif world.weather == "stars" and i % 2 == 0:
            draw.ellipse((x, y, x + 3, y + 3), fill=(235, 238, 255))
    if world.weather == "mist":
        for band in range(4):
            y = top + height // 3 + band * 45
            draw.rectangle((left, y, right, y + 18), fill=bottom_rgb)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _ellipsize(draw: ImageDraw.ImageDraw, text: str,
               font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> str:
    """Keep a visible prefix; never return text wider than the allotted region."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    marker = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        middle = (lo + hi + 1) // 2
        if draw.textlength(text[:middle].rstrip() + marker, font=font) <= max_width:
            lo = middle
        else:
            hi = middle - 1
    return text[:lo].rstrip() + marker


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int,
         minimum: int = 72, max_lines: int = 3,
         ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    """Prefer full text; at a readable font floor, shorten only the visible title.

    An unbroken long word must not spill over the artwork, and a very long
    concept must not shrink to unreadably small letters in a mobile preview.
    """
    words = text.split()
    for size in (*range(start, minimum, -4), minimum):
        font = _font(size)
        lines: list[str] = []
        for word in words:
            trial = f"{lines[-1]} {word}" if lines else word
            if lines and draw.textlength(trial, font=font) <= max_width:
                lines[-1] = trial
            else:
                lines.append(word)
        if len(lines) <= max_lines and all(
            draw.textlength(line, font=font) <= max_width for line in lines
        ):
            return font, lines

    font = _font(minimum)
    lines = []
    remaining = list(words)
    for index in range(max_lines):
        if not remaining:
            break
        if index == max_lines - 1:
            lines.append(_ellipsize(draw, " ".join(remaining), font, max_width))
            break
        line = remaining.pop(0)
        if draw.textlength(line, font=font) > max_width:
            lines.append(_ellipsize(draw, line, font, max_width))
            continue
        while remaining and draw.textlength(
            f"{line} {remaining[0]}", font=font,
        ) <= max_width:
            line = f"{line} {remaining.pop(0)}"
        lines.append(line)
    return font, lines


def _contrast_ink(background: RGB) -> RGB:
    """Use dark ink on light accents and white on dark accents."""
    def linear(channel: int) -> float:
        normalized = channel / 255
        return normalized / 12.92 if normalized <= 0.04045 else (
            (normalized + 0.055) / 1.055
        ) ** 2.4

    r, g, b = (linear(channel) for channel in background)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0, 0, 0) if luminance > 0.179 else (255, 255, 255)


def _motif(seed: bytes, count: int) -> Sequence[float]:
    digest = hashlib.sha256(seed).digest()
    while len(digest) < count:
        digest += hashlib.sha256(digest).digest()
    return [0.2 + 0.8 * b / 255 for b in digest[:count]]


def render_thumbnail(variant: str, brand: BrandProfile, concept: str,
                     world_name: str | None = None) -> bytes:
    """One thumbnail variant as PNG bytes (deterministic); A draws ``world_name``'s scene."""
    world = visual_world(world_name)
    if not concept.strip():
        raise MediaAssemblyError("playlist_concept_missing", "a thumbnail needs the concept",
                                 ["playlist_concept"])
    palette = _palette(brand)
    w, h = THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT
    if variant == "thumb_a_hero":
        # Editorial cover rather than a waveform: a night-window scene, one clear
        # focal subject and large readable album words in the left safe area.
        image = _gradient(w, h, palette.shade, palette.base)
        draw = ImageDraw.Draw(image)
        # A dedicated title plate is legible even when all brand colors are pale.
        draw.rounded_rectangle((46, 66, 664, 690), radius=30, fill=(12, 19, 38))
        draw.rounded_rectangle((688, 65, 1214, 633), radius=32, fill=(12, 19, 39),
                               outline=palette.accent, width=9)
        if world is not None:
            image.paste(_gradient(496, 533, _rgb(world.sky_top), _rgb(world.sky_bottom)),
                        (703, 80))
            draw = ImageDraw.Draw(image)
            draw.ellipse((968, 135, 1084, 251), fill=_rgb(world.light))
            _scene(draw, world, f"{brand.brand_id}|{concept}", (703, 80, 1199, 612))
        else:
            image.paste(_gradient(496, 533, (28, 49, 89), (8, 17, 37)), (703, 80))
            draw = ImageDraw.Draw(image)
            draw.ellipse((968, 135, 1084, 251), fill=(255, 216, 150))
            heights = _motif(f"{brand.brand_id}|{concept}".encode(), 22)
            for i, height in enumerate(heights):
                x = 703 + i * 23
                top = int(490 - height * 135)
                draw.rectangle((x, top, x + 24, 612), fill=(17, 30, 53))
            rain = _motif(f"rain|{concept}".encode(), 64)
            for i, amount in enumerate(rain):
                x = 721 + (i * 71) % 468
                y = 96 + int((i * 67 + amount * 47) % 310)
                draw.line((x, y, x - 14, y + 27), fill=(93, 139, 177), width=2)
        draw.rectangle((944, 80, 957, 633), fill=(21, 28, 49))
        draw.rectangle((703, 335, 1200, 349), fill=(21, 28, 49))
        draw.rounded_rectangle((675, 596, 1230, 670), radius=15, fill=(36, 39, 54))
        draw.ellipse((1030, 565, 1130, 608), fill=(222, 151, 99))
        draw.rectangle((1040, 553, 1120, 592), fill=(222, 151, 99))
        label = brand.brand_name.upper()
        small, brand_lines = _fit(draw, label, 558, 35, minimum=28, max_lines=1)
        if brand_lines:
            draw.text((72, 115), brand_lines[0], fill=(245, 245, 245), font=small)
        font, lines = _fit(draw, concept.upper(), 558, 87, minimum=72)
        y = 226
        for line in lines:
            draw.text((72, y), line, font=font, fill=(252, 246, 226),
                      stroke_width=2, stroke_fill=(6, 12, 25))
            y += int(getattr(font, "size", 60) * 1.23)
        draw.rounded_rectangle((72, 612, 335, 668), radius=16, fill=palette.accent)
        draw.text((91, 622), "FULL ALBUM", font=_font(31),
                  fill=_contrast_ink(palette.accent))
    elif variant == "thumb_b_bold_text":
        image = _gradient(w, h, palette.shade, palette.base)
        draw = ImageDraw.Draw(image)
        # The fixed dark plate preserves title contrast even for pale brand palettes.
        draw.rounded_rectangle((64, 120, w - 64, 572), radius=26, fill=(12, 19, 38))
        draw.rectangle((64, h - 130, w - 64, h - 110), fill=palette.accent)
        font, lines = _fit(draw, concept.upper(), w - 160, 120, minimum=82)
        y = 150
        for line in lines:
            draw.text((80, y), line, font=font, fill=(255, 255, 255))
            y += int(getattr(font, "size", 40) * 1.1)
        small, brand_lines = _fit(
            draw, brand.brand_name, w - 160, 36, minimum=28, max_lines=1,
        )
        if brand_lines:
            draw.text((80, 80), brand_lines[0], font=small,
                      fill=_contrast_ink(palette.shade))
    elif variant == "thumb_c_minimal":
        image = Image.new("RGB", (w, h), palette.base)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((96, 86, w - 96, 604), radius=24, fill=(12, 19, 38))
        draw.rectangle((w // 2 - 160, h // 2 + 50, w // 2 + 160, h // 2 + 54),
                       fill=palette.accent)
        font, lines = _fit(draw, concept, w - 300, 88, minimum=72)
        step = int(getattr(font, "size", 40) * 1.2)
        y = h // 2 - step * len(lines)
        for line in lines:
            draw.text(((w - draw.textlength(line, font=font)) / 2, y), line,
                      font=font, fill=(235, 235, 235))
            y += step
    else:
        raise MediaAssemblyError("thumbnail_variant_unknown", f"unknown variant {variant!r}")
    data = _png(image)
    if len(data) > THUMBNAIL_MAX_BYTES:
        raise MediaAssemblyError(
            "thumbnail_too_large", f"{variant} is {len(data)} bytes; YouTube allows 2 MB")
    return data


def generate_thumbnails(*, media_package_id_str: str, brand_profile: BrandProfile,
                        playlist_concept: str, store: ArtifactStore,
                        variant_keys: Sequence[str] = THUMBNAIL_VARIANTS,
                        claim_namespace: str | None = None,
                        visual_world: str | None = None,
                        now: datetime) -> list[ThumbnailVariant]:
    """Draw, store and record the thumbnail candidates; checksums are of stored bytes."""
    root = scoped_artifact_root(
        f"thumbnails/{brand_profile.brand_id}/{media_package_id_str}", claim_namespace,
    )
    variants: list[ThumbnailVariant] = []
    for key in variant_keys:
        data = render_thumbnail(key, brand_profile, playlist_concept, visual_world)
        stored = store.put(f"{root}/{key}.png", data, content_type="image/png")
        variants.append(ThumbnailVariant.create(
            media_package_id_str=media_package_id_str,
            variant_key=key,
            source_assets=[],
            uri=stored.uri,
            checksum=stored.sha256,
            text_payload=playlist_concept,
            width=THUMBNAIL_WIDTH,
            height=THUMBNAIL_HEIGHT,
            layout_version=f"{LAYOUT_VERSION}+{brand_profile.version}",
            now=now,
        ))
    return variants
