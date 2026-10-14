"""Thumbnails A/B/C and the video background, drawn with Pillow (P6 §17, §18, R4).

Every image is a deterministic function of the brand profile, the playlist concept and
the variant: the same inputs give the same PNG bytes (on the same Pillow/FreeType
build). Nothing is random; the only "variation" is a motif derived from the sha256 of
the inputs. The bytes are stored content-addressed under ``media/`` and every record
carries the sha256 of the bytes stored.

Thumbnail designs, all 1280x720 (YouTube's recommended size, 16:9), PNG, under 2 MB:

* **A, ``thumb_a_hero``**: the brand gradient with a centred waveform motif (bar
  heights derived from the concept's hash) in the accent colour, the brand name small
  at the bottom.
* **B, ``thumb_b_bold_text``**: the brand gradient with the playlist concept in large
  type on the left, over an accent bar, and the brand name above it.
* **C, ``thumb_c_minimal``**: a flat dark field with a thin accent rule and the concept
  in small type centred.

The variants change presentation only (P6 §18): every one states the same concept.
The background is the same gradient at the render profile's size.
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

from ..artifacts import ArtifactStore
from ..domain.models import ThumbnailVariant
from ..errors import MediaAssemblyError
from ..visuals.planner import BrandProfile

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
LAYOUT_VERSION: Final[str] = "thumb-layout-v1"
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


def render_background(brand: BrandProfile, width: int, height: int) -> bytes:
    """The brand gradient (base to shade) the visualiser is drawn over."""
    palette = _palette(brand)
    return _png(_gradient(width, height, palette.base, palette.shade))


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int,
         minimum: int = 18) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    """The largest font (from ``start``) at which ``text`` wraps into three lines."""
    words = text.split()
    size = start
    while True:
        font = _font(size)
        lines: list[str] = []
        for word in words:
            trial = f"{lines[-1]} {word}" if lines else word
            if lines and draw.textlength(trial, font=font) <= max_width:
                lines[-1] = trial
            else:
                lines.append(word)
        fits = len(lines) <= 3 and all(draw.textlength(ln, font=font) <= max_width
                                       for ln in lines)
        if fits or size <= minimum:
            return font, lines
        size -= 4


def _motif(seed: bytes, count: int) -> Sequence[float]:
    digest = hashlib.sha256(seed).digest()
    while len(digest) < count:
        digest += hashlib.sha256(digest).digest()
    return [0.2 + 0.8 * b / 255 for b in digest[:count]]


def render_thumbnail(variant: str, brand: BrandProfile, concept: str) -> bytes:
    """One thumbnail variant as PNG bytes (deterministic)."""
    if not concept.strip():
        raise MediaAssemblyError("playlist_concept_missing", "a thumbnail needs the concept",
                                 ["playlist_concept"])
    palette = _palette(brand)
    w, h = THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT
    if variant == "thumb_a_hero":
        image = _gradient(w, h, palette.base, palette.shade)
        draw = ImageDraw.Draw(image)
        bars = _motif(f"{brand.brand_id}|{concept}".encode(), 48)
        bar_w = w // (len(bars) * 2)
        x0 = (w - bar_w * (len(bars) * 2 - 1)) // 2
        for i, level in enumerate(bars):
            half = int(level * h * 0.28)
            x = x0 + i * bar_w * 2
            draw.rectangle((x, h // 2 - half, x + bar_w - 1, h // 2 + half), fill=palette.accent)
        font = _font(40)
        label = brand.brand_name
        draw.text(((w - draw.textlength(label, font=font)) / 2, h - 90), label,
                  font=font, fill=palette.highlight)
    elif variant == "thumb_b_bold_text":
        image = _gradient(w, h, palette.shade, palette.base)
        draw = ImageDraw.Draw(image)
        draw.rectangle((64, h - 150, w - 64, h - 130), fill=palette.accent)
        font, lines = _fit(draw, concept.upper(), w - 160, 120)
        y = 150
        for line in lines:
            draw.text((80, y), line, font=font, fill=(255, 255, 255))
            y += int(getattr(font, "size", 40) * 1.1)
        small = _font(36)
        draw.text((80, 80), brand.brand_name, font=small, fill=palette.highlight)
    elif variant == "thumb_c_minimal":
        image = Image.new("RGB", (w, h), palette.base)
        draw = ImageDraw.Draw(image)
        draw.rectangle((w // 2 - 160, h // 2 + 50, w // 2 + 160, h // 2 + 54),
                       fill=palette.accent)
        font, lines = _fit(draw, concept, w - 300, 56)
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
                        now: datetime) -> list[ThumbnailVariant]:
    """Draw, store and record the thumbnail candidates; checksums are of stored bytes."""
    variants: list[ThumbnailVariant] = []
    for key in variant_keys:
        data = render_thumbnail(key, brand_profile, playlist_concept)
        stored = store.put(f"thumbnails/{brand_profile.brand_id}/{media_package_id_str}/{key}.png",
                           data, content_type="image/png")
        variants.append(ThumbnailVariant.create(
            media_package_id_str=media_package_id_str,
            variant_key=key,
            source_assets=[],
            uri=stored.uri,
            checksum=stored.sha256,
            text_payload=playlist_concept if key != "thumb_a_hero" else brand_profile.brand_name,
            width=THUMBNAIL_WIDTH,
            height=THUMBNAIL_HEIGHT,
            layout_version=f"{LAYOUT_VERSION}+{brand_profile.version}",
            now=now,
        ))
    return variants
