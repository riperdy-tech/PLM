"""The owner's thumbnail art inbox: one folder per package waiting for art.

``PLM_THUMBNAIL_ART_INBOX`` names the inbox. When it is set, a package P6 renders
is not offered for publishing with P6's own drawn thumbnails: it waits at
``awaiting_thumbnail_art`` and P6 writes ``<inbox>/<media_package_id>/``:

* ``PROMPTS.md``: three Nano Banana Pro prompts for the owner to paste, and where to
  save each image (``A.png``, ``B.png``, ``C.png``; JPEG or WebP also accepted);
* ``job.json``: the same prompts and their styles, as the import reads them.

``plm media thumbnail-import`` then checks every image (:func:`check_image`) and,
only when all three pass, renders the video as image A held still and releases the
package with them. Unset, P6
publishes its own drawn thumbnails as before (development and tests).
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from PIL import Image, ImageStat, UnidentifiedImageError

from .guide import ArtPrompt

__all__ = ["INBOX_VAR", "JOB_FILE", "MAX_BYTES", "ArtCheck", "art_inbox", "check_image",
           "find_art", "read_job", "write_job"]

INBOX_VAR: Final[str] = "PLM_THUMBNAIL_ART_INBOX"
JOB_FILE: Final[str] = "job.json"
SIZE: Final[tuple[int, int]] = (1280, 720)
MAX_BYTES: Final[int] = 2 * 1024 * 1024
"""YouTube's custom-thumbnail limit."""
_ASPECT_TOLERANCE: Final[float] = 0.02
_MIN_DETAIL: Final[float] = 8.0
"""Below this per-channel standard deviation the image is a flat fill, not artwork."""
_SUFFIXES: Final[tuple[str, ...]] = (".png", ".jpg", ".jpeg", ".webp")


def art_inbox(env: Mapping[str, str] | None = None) -> Path | None:
    raw = (os.environ if env is None else env).get(INBOX_VAR, "").strip()
    return Path(raw) if raw else None


def _letter(variant_key: str) -> str:
    return variant_key.removeprefix("art_").upper()


def write_job(inbox: Path, *, package_id: str, title: str, guide_version: str,
              prompts: list[ArtPrompt]) -> Path:
    """Write the package's job folder once; an existing job is never rewritten, so the
    prompts the owner is already working from stay the ones the import expects."""
    folder = inbox / package_id
    job_path = folder / JOB_FILE
    if job_path.is_file():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"# Thumbnails for: {title}", "",
             f"Package `{package_id}`, style guide {guide_version}.", "",
             "Paste each prompt into Nano Banana Pro (16:9). Save the image you like into "
             "this folder under the name shown (PNG, JPEG or WebP, at least 1280 px wide), "
             "then run `plm media thumbnail-import " + package_id + "`. Image A is also "
             "the video's picture, held still for the whole video, so save it at full "
             "size (1920 px wide or more).", ""]
    for p in prompts:
        lines += [f"## {_letter(p.variant_key)}.png: {p.style_id} (text: \"{p.text}\")", "",
                  "```", p.prompt, "```", ""]
    (folder / "PROMPTS.md").write_text("\n".join(lines), encoding="utf-8")
    job_path.write_text(json.dumps({
        "media_package_id": package_id, "title": title, "guide_version": guide_version,
        "variants": [asdict(p) for p in prompts],
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return folder


def read_job(folder: Path) -> dict[str, Any]:
    job: dict[str, Any] = json.loads((folder / JOB_FILE).read_text(encoding="utf-8"))
    return job


def find_art(folder: Path, variant_key: str) -> Path | None:
    """``A.png`` (or ``a.jpg``, ``art_a.webp``...) for ``art_a``; ``None`` if absent."""
    stems = {_letter(variant_key).lower(), variant_key.lower()}
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.stem.lower() in stems and path.suffix.lower() in _SUFFIXES:
            return path
    return None


class ArtCheck:
    """One image's check: ``problem`` names why it is refused, else ``data`` is the
    1280x720 image to upload and ``content_type`` its type."""

    def __init__(self, problem: str | None, data: bytes = b"",
                 content_type: str = "") -> None:
        self.problem = problem
        self.data = data
        self.content_type = content_type


def check_image(raw: bytes) -> ArtCheck:
    """Accept a 16:9 image at least 1280 px wide that is not a flat fill; resize it to
    1280x720 and keep it under YouTube's 2 MB (PNG, else JPEG)."""
    try:
        opened = Image.open(io.BytesIO(raw))
        opened.load()
    except (UnidentifiedImageError, OSError):
        return ArtCheck("not a readable PNG, JPEG or WebP image")
    width, height = opened.size
    if abs(width / height - 16 / 9) > _ASPECT_TOLERANCE * 16 / 9:
        return ArtCheck(f"{width}x{height} is not 16:9")
    if width < SIZE[0]:
        return ArtCheck(f"{width} px wide; at least {SIZE[0]} px is needed")
    image = opened.convert("RGB")
    if max(ImageStat.Stat(image).stddev) < _MIN_DETAIL:
        return ArtCheck("the image is a flat fill, not artwork")
    if image.size != SIZE:
        image = image.resize(SIZE, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    if out.tell() <= MAX_BYTES:
        return ArtCheck(None, out.getvalue(), "image/png")
    for quality in (92, 85, 75):
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality)
        if out.tell() <= MAX_BYTES:
            return ArtCheck(None, out.getvalue(), "image/jpeg")
    return ArtCheck("over 2 MB even as JPEG")
