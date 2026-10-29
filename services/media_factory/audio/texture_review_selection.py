"""Deterministic, bounded selection of *unlabelled* archived P6 review pairs.

Use different relative spectral score ranges to reduce cherry-picking, not to
supply true-negative labels or probability-sample accuracy estimates.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ..playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY, valid_texture_signature
from ..texture_sample_audit import MeasuredSample

MAX_REVIEW_TRACKS = 24
MAX_REVIEW_PAIRS = 12


@dataclass(frozen=True, slots=True)
class ReviewPair:
    left: MeasuredSample
    right: MeasuredSample
    fixed_prescreen_score: float
    relative_score_band: str


def _release_pair(a: str, b: str) -> tuple[str, str]:
    return min(a, b), max(a, b)


def select_review_pairs(
    tracks: Sequence[MeasuredSample], *, max_pairs: int = 6,
) -> tuple[ReviewPair, ...]:
    """Spread a limited listening queue over high/mid/low spectral strata.

    Pair identities and ordering are stable across permutations of the input.
    Different SHA256 sources are *not* inferred to be different musical works.
    """
    if not 2 <= max_pairs <= MAX_REVIEW_PAIRS or not 4 <= len(tracks) <= MAX_REVIEW_TRACKS:
        raise ValueError("review selection requires 4..24 tracks and 2..12 pairs")
    packages = {track.package_id for track in tracks}
    if not 2 <= len(packages) <= 8:
        raise ValueError("review selection requires 2..8 packages")
    sha_seen: set[str] = set()
    identity_seen: set[tuple[str, str]] = set()
    for track in tracks:
        if (
            not track.package_id or not track.release_id or not track.song_id
            or len(track.sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in track.sha256)
            or track.sha256 in sha_seen
            or (track.package_id, track.song_id) in identity_seen
            or not valid_texture_signature(
                track.signature, bins=DEFAULT_ALBUM_TEXTURE_POLICY.expected_signature_bins
            )
        ):
            raise ValueError("invalid, duplicate or exact-reused source in selection")
        sha_seen.add(track.sha256)
        identity_seen.add((track.package_id, track.song_id))
    ranked: list[tuple[float, MeasuredSample, MeasuredSample]] = []
    ordered = sorted(tracks, key=lambda t: (t.sha256, t.package_id, t.song_id))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if left.release_id == right.release_id:
                continue
            score = math.fsum(
                a * b for a, b in zip(left.signature, right.signature, strict=True)
            ) / 4
            if not math.isfinite(score) or not 0 <= score <= 1.0 + 1e-10:
                raise ValueError("nonfinite or invalid prescreen similarity")
            ranked.append((min(score, 1.0), left, right))
    ranked.sort(key=lambda item: (-item[0], item[1].sha256, item[2].sha256))
    if not ranked:
        raise ValueError("no cross-release sample pairs")
    target = min(max_pairs, len(ranked))
    groups: dict[str, list[tuple[float, MeasuredSample, MeasuredSample]]] = {
        "high_relative": [], "middle_relative": [], "low_relative": [],
    }
    bands = tuple(groups)
    for index, item in enumerate(ranked):
        groups[bands[min(2, index * 3 // len(ranked))]].append(item)
    taken: set[tuple[str, str]] = set()
    usage: Counter[str] = Counter()
    album_usage: Counter[tuple[str, str]] = Counter()
    chosen: list[ReviewPair] = []
    while len(chosen) < target:
        progressed = False
        for band in bands:
            available = [
                item for item in groups[band]
                if (item[1].sha256, item[2].sha256) not in taken
            ]
            if not available:
                continue
            candidate = min(available, key=lambda item: (
                usage[item[1].sha256] + usage[item[2].sha256],
                album_usage[_release_pair(item[1].release_id, item[2].release_id)],
                -item[0] if band == "high_relative" else (
                    item[0] if band == "low_relative" else abs(item[0] - 0.5)
                ),
                item[1].sha256, item[2].sha256,
            ))
            score, first, second = candidate
            taken.add((first.sha256, second.sha256))
            usage[first.sha256] += 1
            usage[second.sha256] += 1
            album_usage[_release_pair(first.release_id, second.release_id)] += 1
            chosen.append(ReviewPair(first, second, score, band))
            progressed = True
            if len(chosen) == target:
                break
        if not progressed:
            raise ValueError("unable to fill bounded review queue")
    return tuple(chosen)
