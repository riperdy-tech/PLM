"""Hold an upload that would feel like the last ones (growth spec 2026-11-03, rank 1).

YouTube does not monetize "generic or repetitive" content: videos that look templated or
"feel repetitive to viewers after watching several videos in a row" from one channel. The
recordings are already held to that by the catalog check (no recording of an earlier
package may return, see :mod:`.catalog`). This check covers the packaging: before any
render, the package's concept, visual world and titles are compared with the brand's last
:attr:`VariationPolicy.window` packages that recorded a fingerprint (newest first):

* the same concept (case, spacing and punctuation ignored) is refused;
* the visual world of any of the last :attr:`VariationPolicy.visual_world_gap` packages is
  refused (P3 rotates the world over the same window, so this only fires when a brief
  names a recently used world);
* a title that differs from an earlier one by at most one swapped word (a template fill)
  is refused.

A package stored before the fingerprint existed is counted, not compared.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import MediaAssemblyError
from .catalog import CatalogAlbum

__all__ = [
    "DEFAULT_VARIATION_POLICY",
    "VariationPolicy",
    "check_upload_variation",
    "variation_fingerprint",
]


@dataclass(frozen=True, slots=True)
class VariationPolicy:
    version: str = "upload-variation-v1"
    window: int = 10
    visual_world_gap: int = 3
    max_title_word_difference: int = 2
    """Words in one title and not the other (both ways); 2 is one word swapped."""


DEFAULT_VARIATION_POLICY = VariationPolicy()

_WORD = re.compile(r"[^\W_]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def variation_fingerprint(*, concept: str, visual_world: str, titles: Sequence[str],
                          policy: VariationPolicy = DEFAULT_VARIATION_POLICY) -> dict[str, Any]:
    """What this check compares, stored in the package's render report."""
    return {"policy_version": policy.version, "concept": concept,
            "visual_world": visual_world, "titles": list(titles)}


def check_upload_variation(
    current: dict[str, Any], *, current_release_manifest_id: str,
    history: Sequence[CatalogAlbum], policy: VariationPolicy = DEFAULT_VARIATION_POLICY,
) -> dict[str, Any]:
    """Refuse a package too like the brand's recent ones; else report what was compared.

    ``history`` is newest first (as :func:`list_catalog_albums` loads it); a re-render of
    the same release is not an earlier upload."""
    earlier = [a for a in history if a.release_manifest_id != current_release_manifest_id]
    recent = [a for a in earlier if isinstance(a.variation, dict)][:policy.window]
    concept = _words(str(current["concept"]))
    titles = [set(_words(t)) for t in current["titles"]]
    for index, album in enumerate(recent):
        prior = album.variation or {}
        where = f"earlier package {album.media_package_id}"
        if concept and _words(str(prior.get("concept", ""))) == concept:
            _refuse(f"the concept {current['concept']!r} repeats {where}")
        if index < policy.visual_world_gap and prior.get("visual_world") == current[
                "visual_world"]:
            _refuse(f"the visual world {current['visual_world']!r} was used by {where}, "
                    f"one of the last {policy.visual_world_gap}")
        for title, words in zip(current["titles"], titles, strict=True):
            for other in prior.get("titles") or ():
                if len(words ^ set(_words(str(other)))) <= policy.max_title_word_difference:
                    _refuse(f"the title {title!r} is a template fill of {other!r} in {where}")
    return {"policy_version": policy.version, "compared_packages": len(recent),
            "unfingerprinted_packages": sum(1 for a in earlier if a.variation is None)}


def _refuse(message: str) -> None:
    raise MediaAssemblyError("upload_variation_insufficient",
                             f"{message}; change the concept, visual world or title",
                             ["playlist_concept"])
