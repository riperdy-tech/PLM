"""Within-brand, cross-album near-copy screening of persisted P6 audio textures.

This adds an independent P6 quality gate without changing frozen C6 seams or
calling another phase. Only recorded, measured fingerprints are compared; old
packages with no fingerprints require explicit catalog remediation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..audio.features import DEFAULT_FEATURE_POLICY, TrackFeatures
from ..errors import MediaAssemblyError
from .diversity import (
    DEFAULT_ALBUM_TEXTURE_POLICY,
    AlbumTexturePolicy,
    valid_texture_signature,
)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(digit in "0123456789abcdef" for digit in value.lower())
    )


@dataclass(frozen=True, slots=True)
class CatalogAlbum:
    media_package_id: str
    release_manifest_id: str
    track_signatures: object
    policy_version: str | None = None
    extractor_version: str | None = None


def serialize_album_signatures(
    selected_ids: Sequence[str],
    features: Mapping[str, TrackFeatures],
    checksums: Mapping[str, str],
    *,
    policy: AlbumTexturePolicy = DEFAULT_ALBUM_TEXTURE_POLICY,
) -> dict[str, object]:
    """Save the same measured evidence P6 used to accept a given playlist."""
    entries: list[dict[str, Any]] = []
    for candidate_id in selected_ids:
        measured = features.get(candidate_id)
        signature = measured.texture_signature if measured is not None else None
        checksum = checksums.get(candidate_id)
        if (
            signature is None
            or not valid_texture_signature(signature, bins=policy.expected_signature_bins)
            or measured is None or measured.extractor_version != DEFAULT_FEATURE_POLICY.version
            or not isinstance(checksum, str) or not _is_sha256(checksum)
        ):
            raise MediaAssemblyError(
                "album_texture_unmeasurable",
                f"missing a measured signature or source SHA256 for {candidate_id!r}",
                ["candidate_ids"],
            )
        entries.append({
            "candidate_id": candidate_id,
            "artifact_sha256": checksum,
            "signature": list(signature),
        })
    return {
        "policy_version": policy.version,
        "extractor_version": DEFAULT_FEATURE_POLICY.version,
        "track_signatures": entries,
    }


def ordered_track_evidence_matches(tracklist: object, signatures: object) -> bool:
    """Require exact archived track order, consecutive positions and source IDs.

    Catalog loading and operator inventory must agree on the same persisted
    structural evidence. Numeric positions are strictly integers, not booleans.
    """
    if (
        not isinstance(tracklist, list) or not tracklist
        or not isinstance(signatures, list) or len(tracklist) != len(signatures)
    ):
        return False
    seen: set[str] = set()
    for position, (track, signature) in enumerate(
        zip(tracklist, signatures, strict=True), 1
    ):
        if not isinstance(track, dict) or not isinstance(signature, dict):
            return False
        song_id = track.get("song_id")
        source_id = track.get("source_artifact_id")
        if (
            not isinstance(song_id, str) or not song_id or song_id in seen
            or not isinstance(source_id, str) or not source_id
            or type(track.get("position")) is not int
            or track["position"] != position
            or signature.get("candidate_id") != song_id
        ):
            return False
        seen.add(song_id)
    return True


def validate_catalog_album(
    album: CatalogAlbum, *, policy: AlbumTexturePolicy,
) -> list[tuple[str, str, tuple[float, ...]]]:
    """Legacy/incomplete/corrupt historical data cannot be a passing decision."""
    if (
        album.policy_version != policy.version
        or album.extractor_version != DEFAULT_FEATURE_POLICY.version
    ):
        raise MediaAssemblyError(
            "album_catalog_version_incompatible",
            f"previous album {album.media_package_id} has missing or incompatible "
            "texture policy/extractor version; verified remeasurement required",
            ["catalog"],
        )
    raw = album.track_signatures
    if not isinstance(raw, list) or not raw:
        raise MediaAssemblyError(
            "album_catalog_unmeasurable",
            f"previous album {album.media_package_id} has no measured audio signature; backfill required",
            ["catalog"],
        )
    result: list[tuple[str, str, tuple[float, ...]]] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            break
        cid, checksum, values = (
            row.get("candidate_id"), row.get("artifact_sha256"), row.get("signature")
        )
        if (
            not isinstance(cid, str) or not cid or cid in seen
            or not isinstance(checksum, str) or not _is_sha256(checksum)
            or not isinstance(values, list)
            or not valid_texture_signature(values, bins=policy.expected_signature_bins)
        ):
            break
        seen.add(cid)
        result.append((cid, checksum, tuple(float(v) for v in values)))
    if len(result) != len(raw):
        raise MediaAssemblyError(
            "album_catalog_unmeasurable",
            f"previous album {album.media_package_id} contains invalid track signatures",
            ["catalog"],
        )
    return result


def check_catalog_texture(
    selected_ids: Sequence[str],
    features: Mapping[str, TrackFeatures],
    checksums: Mapping[str, str],
    *,
    current_release_manifest_id: str,
    history: Sequence[CatalogAlbum],
    policy: AlbumTexturePolicy = DEFAULT_ALBUM_TEXTURE_POLICY,
) -> dict[str, object]:
    """Hold likely reused recordings from a different release of this brand.

    Same-release profile re-renders are intentionally ignored; cross-brand
    similarity, altered melodic structure and human listening remain outside
    this conservative near-copy detector.
    """
    current = serialize_album_signatures(selected_ids, features, checksums, policy=policy)
    current_tracks = current["track_signatures"]
    assert isinstance(current_tracks, list)
    prior_packages = 0
    compared_pairs = 0
    highest: float | None = None
    for album in history:
        if album.release_manifest_id == current_release_manifest_id:
            continue
        prior = validate_catalog_album(album, policy=policy)
        prior_packages += 1
        for entry in current_tracks:
            assert isinstance(entry, dict)
            left = entry["signature"]
            assert isinstance(left, list)
            for candidate_id, checksum, signature in prior:
                score = sum(x * y for x, y in zip(left, signature, strict=True)) / 4
                score = max(0., min(1., score))
                compared_pairs += 1
                highest = score if highest is None else max(highest, score)
                if (
                    entry["candidate_id"] == candidate_id
                    or entry["artifact_sha256"] == checksum
                    or score >= policy.near_copy_threshold
                ):
                    raise MediaAssemblyError(
                        "album_catalog_near_duplicate",
                        f"candidate {entry['candidate_id']} resembles {candidate_id} "
                        f"in earlier package {album.media_package_id} ({score:.4f}); "
                        "replace or review the recording",
                        ["candidate_ids"],
                    )
    return {
        "policy_version": policy.version,
        "compared_packages": prior_packages,
        "compared_pairs": compared_pairs,
        "max_similarity": highest,
        "scope": "same_brand_stored_ready_or_published_packages",
        "evidence": current,
    }
