"""Conservative near-copy screening of a proposed album's measured audio.

This is a *high-confidence texture duplicate* gate, not an embedding-based
semantic music similarity service. Only near-identical four-part spectral
signatures trigger a hold. Musical originality still needs listening review.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..audio.features import DEFAULT_FEATURE_POLICY, TrackFeatures
from ..errors import MediaAssemblyError


@dataclass(frozen=True, slots=True)
class AlbumTexturePolicy:
    version: str = "album-texture-v1"
    near_copy_threshold: float = 0.998
    expected_signature_bins: int = 192


DEFAULT_ALBUM_TEXTURE_POLICY = AlbumTexturePolicy()


def valid_texture_signature(signature: object, *, bins: int) -> bool:
    """Only accept four nonnegative, independently unit-normalised spectra.

    Finite 192-element arrays alone are not evidence: zero or arbitrarily
    scaled vectors can make a catalog comparison falsely pass or fail.
    """
    if (
        not isinstance(signature, (list, tuple))
        or bins <= 0 or bins % 4 != 0 or len(signature) != bins
    ):
        return False
    for value in signature:
        if (
            type(value) not in (int, float)
            or not 0.0 <= value <= 1.0 + 1e-6
            or not math.isfinite(value)
        ):
            return False
    window = bins // 4
    return all(
        math.isclose(
            math.fsum(value * value for value in signature[start:start + window]),
            1.0, rel_tol=1e-5, abs_tol=1e-5,
        )
        for start in range(0, bins, window)
    )


def check_album_texture(
    track_ids: Sequence[str],
    features: Mapping[str, TrackFeatures],
    *,
    policy: AlbumTexturePolicy = DEFAULT_ALBUM_TEXTURE_POLICY,
) -> dict[str, object]:
    """Refuse highly similar recording pairs before rendering; report coverage.

    An unavailable measurement blocks this pre-render check rather than
    becoming artificial zero similarity or an unjustified pass.
    P5 still owns exact-match fingerprint and licensing decisions.
    """
    if len(set(track_ids)) != len(track_ids):
        raise MediaAssemblyError("duplicate_track", "an album repeats a candidate ID")
    unavailable: list[str] = []
    measurements: list[tuple[str, tuple[float, ...]]] = []
    for candidate_id in track_ids:
        entry = features.get(candidate_id)
        signature = entry.texture_signature if entry is not None else None
        if (
            entry is None
            or signature is None
            or entry.extractor_version != DEFAULT_FEATURE_POLICY.version
            or not valid_texture_signature(signature, bins=policy.expected_signature_bins)
        ):
            unavailable.append(candidate_id)
        else:
            measurements.append((candidate_id, signature))
    if unavailable:
        raise MediaAssemblyError(
            "album_texture_unmeasurable",
            f"cannot exclude near-copy tracks without measured audio texture: {sorted(unavailable)}",
            ["candidate_ids"],
        )
    comparisons: list[tuple[str, str, float]] = []
    suspected: list[tuple[str, str, float]] = []
    for index, (left_id, left) in enumerate(measurements):
        for right_id, right in measurements[index + 1:]:
            # Four independently unit-normalised windows, so the dot product
            # divided by four is a 0..1 cosine-like agreement score.
            score = max(0.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True)) / 4))
            comparisons.append((left_id, right_id, score))
            if score >= policy.near_copy_threshold:
                suspected.append((left_id, right_id, score))
    if suspected:
        examples = ", ".join(f"{a}/{b} ({score:.4f})" for a, b, score in suspected[:5])
        raise MediaAssemblyError(
            "album_near_duplicate_audio",
            f"potentially near-identical tracks need replacement/review: {examples}",
            ["candidate_ids"],
        )
    return {
        "policy_version": policy.version,
        "threshold": policy.near_copy_threshold,
        "compared_pairs": len(comparisons),
        "max_similarity": max((score for _, _, score in comparisons), default=None),
        "unmeasured_candidate_ids": unavailable,
        "scope": "within_album_near_copy_only",
    }
