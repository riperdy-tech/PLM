"""The thumbnail style guide and the image prompts written from it (owner decision 2026-11-04).

P6 cannot draw a thumbnail that wins clicks: its own artwork is Pillow shapes. The owner
makes each package's thumbnails in an image model (Nano Banana Pro) from three prompts
P6 writes here. ``styles.json`` holds the styles the 2026-11-04 trend scan found winning
in the focus-music niche, with the scan ranks each one came from, the rules every image
keeps, and one scene per visual world. It is reviewed against each new scan
(``scripts/thumbnail_trend_scan.py``); its ``guide_version`` is recorded on every job.

Variant A uses the style P2 chose for the package (its ``thumbnail_family``), so P2 can
learn from it; B and C use two other styles, so a YouTube "Test & Compare" run tests
styles against each other. Every choice is a stable hash of the package id: the same
package always gets the same prompts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

__all__ = ["ART_VARIANTS", "DEFAULT_STYLE", "GUIDE_PATH", "ArtPrompt", "StyleGuide",
           "build_prompts", "load_style_guide"]

GUIDE_PATH: Final[Path] = Path(__file__).with_name("styles.json")
ART_VARIANTS: Final[tuple[str, ...]] = ("art_a", "art_b", "art_c")
DEFAULT_STYLE: Final[str] = "cinematic_workspace_vista"
"""Variant A's style when P2 names none or one the guide does not hold: the scan's
most frequent winner (11 of the top 30)."""


@dataclass(frozen=True, slots=True)
class ArtPrompt:
    variant_key: str
    style_id: str
    text: str
    prompt: str


@dataclass(frozen=True, slots=True)
class StyleGuide:
    guide_version: str
    rules: tuple[str, ...]
    styles: dict[str, dict[str, Any]]
    worlds: dict[str, dict[str, str]]
    default_world: dict[str, str]
    meme_backgrounds: tuple[str, ...]


def load_style_guide(path: Path = GUIDE_PATH) -> StyleGuide:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "thumbnail-styles-v1":
        raise ValueError(f"{path}: unknown style guide schema {raw.get('schema_version')!r}")
    styles = raw["styles"]
    if DEFAULT_STYLE not in styles or len(styles) < len(ART_VARIANTS):
        raise ValueError(f"{path}: needs {DEFAULT_STYLE} and at least three styles")
    for style_id, style in styles.items():
        if not style.get("texts") or "{text}" not in style.get("typography", ""):
            raise ValueError(f"{path}: style {style_id} needs texts and a {{text}} typography")
    return StyleGuide(
        guide_version=str(raw["guide_version"]), rules=tuple(raw["rules"]), styles=styles,
        worlds=raw["worlds"], default_world=raw["default_world"],
        meme_backgrounds=tuple(raw["meme_backgrounds"]),
    )


def _pick(options: list[str] | tuple[str, ...], *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return options[int.from_bytes(digest[:4], "big") % len(options)]


def build_prompts(guide: StyleGuide, *, package_id: str, thumbnail_family: str | None,
                  visual_world: str) -> list[ArtPrompt]:
    """Three prompts, one per art variant, each a different style."""
    first = thumbnail_family if thumbnail_family in guide.styles else DEFAULT_STYLE
    others = sorted((s for s in guide.styles if s != first),
                    key=lambda s: hashlib.sha256(f"{package_id}|{s}".encode()).hexdigest())
    world = guide.worlds.get(visual_world, guide.default_world)
    rules = " ".join(guide.rules)
    prompts: list[ArtPrompt] = []
    for variant_key, style_id in zip(ART_VARIANTS, (first, *others), strict=False):
        style = guide.styles[style_id]
        text = _pick(style["texts"], package_id, style_id, "text")
        scene = style["prompt"].format(
            scene=world["scene"], vista=world["vista"],
            object=_pick(style.get("objects") or ["a plain unbranded desk lamp"],
                         package_id, style_id, "object"),
            background=_pick(guide.meme_backgrounds, package_id, "background"))
        prompts.append(ArtPrompt(
            variant_key=variant_key, style_id=style_id, text=text,
            prompt=f"{scene} {style['typography'].format(text=text)} {rules}"))
    return prompts
