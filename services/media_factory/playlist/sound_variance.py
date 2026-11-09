"""Warn when a new video sounds like one of the brand's last ones (owner 2026-11-09).

The catalog check (:mod:`.catalog`) refuses only near-identical recordings. Two videos
can share no recording and still sound alike, which viewers notice from one upload to
the next. This compares the album's average sound (the mean of its tracks' measured
four-part spectral signatures) with each of the brand's last
:attr:`SoundVariancePolicy.window` earlier albums, by cosine similarity.

The threshold comes from the brand's own catalog (measured 2026-11-09): the early lofi
and R&B albums the owner found too alike were 0.94-0.97 apart; the focus videos
"Working Late at the Observatory" and "Quiet Focus at Sunrise", which sound different,
0.88; albums of different genres 0.78-0.88.

It never blocks: the result rides in the render report and the Studio shows it at the
owner's review, because whether two videos are too alike is the owner's call.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .catalog import CatalogAlbum

__all__ = ["DEFAULT_SOUND_VARIANCE_POLICY", "SoundVariancePolicy", "check_sound_variance"]


@dataclass(frozen=True, slots=True)
class SoundVariancePolicy:
    version: str = "sound-variance-v1"
    window: int = 5
    too_similar: float = 0.94


DEFAULT_SOUND_VARIANCE_POLICY = SoundVariancePolicy()


def _signatures(tracks: object) -> list[list[float]]:
    found: list[list[float]] = []
    if not isinstance(tracks, list):
        return found
    for track in tracks:
        signature = track.get("signature") if isinstance(track, dict) else None
        if isinstance(signature, list | tuple) and signature and all(
                type(v) in (int, float) and math.isfinite(v) for v in signature):
            found.append([float(v) for v in signature])
    return found


def _mean(vectors: list[list[float]]) -> list[float] | None:
    if not vectors or len({len(v) for v in vectors}) != 1:
        return None
    return [sum(column) / len(vectors) for column in zip(*vectors, strict=True)]


def _cosine(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b):
        return None
    norm = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return None if norm == 0 else sum(x * y for x, y in zip(a, b, strict=True)) / norm


def check_sound_variance(current_tracks: object, *, current_release_manifest_id: str,
                         history: Sequence[CatalogAlbum],
                         policy: SoundVariancePolicy = DEFAULT_SOUND_VARIANCE_POLICY,
                         ) -> dict[str, Any]:
    """How alike this album sounds to each of the last earlier albums (newest first, as
    :func:`list_catalog_albums` loads them); ``too_similar`` names the ones at or above
    the policy's threshold. A re-render of the same release is not an earlier album; an
    album without usable signatures is counted, not compared."""
    report: dict[str, Any] = {"policy_version": policy.version,
                              "threshold": policy.too_similar, "compared": [],
                              "unmeasured_packages": 0, "too_similar": []}
    mine = _mean(_signatures(current_tracks))
    if mine is None:
        report["unmeasured_current"] = True
        return report
    earlier = [a for a in history if a.release_manifest_id != current_release_manifest_id]
    for album in earlier[:policy.window]:
        theirs = _mean(_signatures(album.track_signatures))
        similarity = None if theirs is None else _cosine(mine, theirs)
        if similarity is None:
            report["unmeasured_packages"] += 1
            continue
        titles = (album.variation or {}).get("titles") or []
        entry = {"media_package_id": album.media_package_id,
                 "title": str(titles[0]) if titles else None,
                 "similarity": round(similarity, 4)}
        report["compared"].append(entry)
        if similarity >= policy.too_similar:
            report["too_similar"].append(entry)
    return report
