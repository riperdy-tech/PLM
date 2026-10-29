"""Explicit, read-only diagnostic of measured P6 cross-album texture samples.

Unlike the render gate, this is a *sample* of original checksum-verified audio,
not a full-catalog originality test. No legacy texture evidence is persisted.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .artifacts import ArtifactStore
from .audio.features import DEFAULT_FEATURE_POLICY, measure_features
from .domain.enums import PackageStatus
from .playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY, valid_texture_signature
from .storage.repositories import get_media_package_by_id, get_playlist_session

MAX_SAMPLE_ALBUMS = 8
MAX_SAMPLES_PER_ALBUM = 3
MAX_FULL_AUDIT_TRACKS = 200
MAX_FULL_AUDIT_TRACKS_PER_ALBUM = 30


@dataclass(frozen=True, slots=True)
class SampleSource:
    package_id: str
    release_id: str
    song_id: str
    audio_uri: str
    sha256: str


@dataclass(frozen=True, slots=True)
class MeasuredSample:
    package_id: str
    release_id: str
    song_id: str
    sha256: str
    signature: tuple[float, ...]


def sample_positions(track_count: int, requested: int) -> tuple[int, ...]:
    """Select reproducible positions across each complete playlist."""
    if not 1 <= requested <= MAX_SAMPLES_PER_ALBUM or track_count < requested:
        raise ValueError("sample count must be 1..3 and no larger than track count")
    if requested == 1:
        return (0,)
    if requested == 2:
        return (0, track_count - 1)
    return (0, (track_count - 1) // 2, track_count - 1)


def load_sample_sources(
    session: Session, package_ids: Sequence[str], *, samples_per_album: int = 2,
    full_album: bool = False,
) -> tuple[tuple[SampleSource, ...], int]:
    """Validate all original track identities; optionally select all bounded tracks.

    A full audit is never an unbounded catalog scan: at most eight explicitly
    named packages, 30 tracks per package and 200 tracks in total.
    """
    ids = list(package_ids)
    if (
        not 2 <= len(ids) <= MAX_SAMPLE_ALBUMS or len(set(ids)) != len(ids)
        or any(not isinstance(pid, str) or not pid.strip() for pid in ids)
    ):
        raise ValueError("provide 2..8 distinct, nonempty P6 package IDs")
    sources: list[SampleSource] = []
    brand: str | None = None
    releases: set[str] = set()
    total_tracks = 0
    for package_id in ids:
        package = get_media_package_by_id(session, package_id)
        if package is None or package.status not in (
            PackageStatus.READY_FOR_PUBLISH, PackageStatus.PUBLISHED,
        ):
            raise ValueError(f"package {package_id} is missing or not eligible")
        if brand is None:
            brand = package.brand_id
        if package.brand_id != brand:
            raise ValueError("cross-brand comparison is not supported")
        releases.add(package.release_manifest_id)
        playlist = get_playlist_session(session, package.playlist_session_id)
        if (
            playlist is None or playlist.brand_id != package.brand_id
            or playlist.release_manifest_id != package.release_manifest_id
            or playlist.experiment_id != package.experiment_id
            or playlist.content_arm_id != package.content_arm_id
        ):
            raise ValueError(f"source playlist identity mismatch for {package_id}")
        tracks = sorted(playlist.tracks, key=lambda track: track.position)
        if not tracks or len(tracks) != len(package.tracklist):
            raise ValueError(f"missing source tracklist for {package_id}")
        if full_album and (
            len(tracks) > MAX_FULL_AUDIT_TRACKS_PER_ALBUM
            or total_tracks + len(tracks) > MAX_FULL_AUDIT_TRACKS
        ):
            raise ValueError("full audio audit exceeds bounded track-count limits")
        seen: set[str] = set()
        for idx, (track, rendered) in enumerate(
            zip(tracks, package.tracklist, strict=True)
        ):
            if (
                not isinstance(rendered, dict) or rendered.get("position") != idx + 1
                or track.position != idx
                or not track.song_id or track.song_id in seen
                or rendered.get("song_id") != track.song_id
                or rendered.get("source_artifact_id") != track.source_artifact_id
                or not track.audio_uri
                or not isinstance(track.audio_checksum, str)
                or len(track.audio_checksum) != 64
                or any(c not in "0123456789abcdef" for c in track.audio_checksum.lower())
            ):
                raise ValueError(f"source track metadata mismatch for {package_id}")
            seen.add(track.song_id)
        total_tracks += len(tracks)
        selected_positions = (
            range(len(tracks)) if full_album
            else sample_positions(len(tracks), samples_per_album)
        )
        for idx in selected_positions:
            track = tracks[idx]
            sources.append(SampleSource(
                package_id, package.release_manifest_id, track.song_id,
                track.audio_uri, track.audio_checksum,
            ))
    if len(releases) < 2:
        raise ValueError("comparison requires two different releases")
    return tuple(sources), total_tracks


def measure_sources(
    store: ArtifactStore, sources: Sequence[SampleSource],
) -> tuple[MeasuredSample, ...]:
    """Fetch each immutable source with SHA256 verification; never write it."""
    measured: list[MeasuredSample] = []
    for source in sources:
        audio = store.fetch_verified(source.audio_uri, source.sha256)
        features = measure_features(source.song_id, audio)
        signature = features.texture_signature
        if (
            features.extractor_version != DEFAULT_FEATURE_POLICY.version
            or not valid_texture_signature(
                signature, bins=DEFAULT_ALBUM_TEXTURE_POLICY.expected_signature_bins
            )
        ):
            raise ValueError(f"texture measurement unavailable for {source.package_id}")
        assert signature is not None
        measured.append(MeasuredSample(
            source.package_id, source.release_id, source.song_id, source.sha256,
            signature,
        ))
    return tuple(measured)


def compare_cross_release_samples(
    tracks: Sequence[MeasuredSample], *, top_k: int = 5, full_album: bool = False,
) -> dict[str, object]:
    """Report bounded spectral similarities; even full source coverage is not originality."""
    maximum = MAX_FULL_AUDIT_TRACKS if full_album else 24
    if not 1 <= top_k <= 10 or len(tracks) < 2 or len(tracks) > maximum:
        raise ValueError(f"expected 2..{maximum} tracks and top_k 1..10")
    seen: set[tuple[str, str]] = set()
    for track in tracks:
        if (
            not track.song_id or not track.release_id or not track.package_id
            or len(track.sha256) != 64
            or any(c not in "0123456789abcdef" for c in track.sha256.lower())
            or (track.package_id, track.song_id) in seen
            or not valid_texture_signature(
                track.signature, bins=DEFAULT_ALBUM_TEXTURE_POLICY.expected_signature_bins
            )
        ):
            raise ValueError("missing, repeated or invalid measured sample")
        seen.add((track.package_id, track.song_id))
    scores: list[tuple[float, MeasuredSample, MeasuredSample]] = []
    for index, left in enumerate(tracks):
        for right in tracks[index + 1:]:
            if left.release_id == right.release_id:
                continue
            score = math.fsum(
                a * b for a, b in zip(left.signature, right.signature, strict=True)
            ) / 4
            if not math.isfinite(score):
                raise ValueError("invalid texture comparison")
            scores.append((max(0.0, min(1.0, score)), left, right))
    if not scores:
        raise ValueError("no cross-release pairs in sample")
    ranked = sorted(
        scores, key=lambda entry: (
            -entry[0], entry[1].package_id, entry[1].song_id,
            entry[2].package_id, entry[2].song_id,
        ),
    )
    threshold = DEFAULT_ALBUM_TEXTURE_POLICY.near_copy_threshold
    return {
        "sampled_tracks": len(tracks),
        "compared_pairs": len(scores),
        "max_similarity": ranked[0][0],
        "at_or_above_near_copy_threshold": sum(s >= threshold for s, _, _ in scores),
        "exact_sha256_reuse_pairs": sum(
            a.sha256 == b.sha256 for _, a, b in scores
        ),
        "threshold": threshold,
        "extractor_version": DEFAULT_FEATURE_POLICY.version,
        "scope": (
            "read_only_explicit_album_full_source_spectral_audit_not_rights_or_originality"
            if full_album
            else "read_only_selected_track_samples_not_full_album_or_rights_clearance"
        ),
        "top_pairs": [
            {
                "score": score,
                "left_package": left.package_id,
                "left_song": left.song_id,
                "right_package": right.package_id,
                "right_song": right.song_id,
            }
            for score, left, right in ranked[:top_k]
        ],
    }
