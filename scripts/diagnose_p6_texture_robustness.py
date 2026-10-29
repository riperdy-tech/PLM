"""Read-only sensitivity probe of four-window P6 texture on real, verified audio.

Takes exactly two explicitly named eligible P6 packages. Reads only the first
source recording of each and constructs gain, speed/pitch and time-offset
variants entirely in memory. Scores are diagnostic, NOT clearance or a gate.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections.abc import Iterator

import numpy as np
import soundfile as sf
import sqlalchemy as sa
from media_factory.artifacts import MinioArtifactStore
from media_factory.audio.features import measure_features
from media_factory.audio.texture_alignment import PLAYBACK_RATES, offset_aligned_texture
from media_factory.audio.texture_label_evaluation import EVALUATION, POLICY_VERSION
from media_factory.playlist.diversity import (
    DEFAULT_ALBUM_TEXTURE_POLICY,
    valid_texture_signature,
)
from media_factory.texture_sample_audit import load_sample_sources
from plm_platform.config import load_settings
from plm_platform.db import build_engine
from sqlalchemy.orm import Session

_MAX_AUDIO_BYTES = 64 * 1024 * 1024
_MAX_DECODED_FRAMES = 12_000_000


def encode_pcm16(samples: np.ndarray, sample_rate: int) -> bytes:
    output = io.BytesIO()
    sf.write(output, samples, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def audio_variants(source: bytes) -> Iterator[tuple[str, bytes]]:
    """Bounded same-source variants; speed changes pitch as well as duration."""
    if len(source) > _MAX_AUDIO_BYTES:
        raise ValueError("audio exceeds the diagnostic byte limit")
    with sf.SoundFile(io.BytesIO(source)) as stream:
        if stream.frames > _MAX_DECODED_FRAMES or stream.channels > 2:
            raise ValueError("audio exceeds the diagnostic frames/channels limit")
        rate = stream.samplerate
        samples = stream.read(dtype="float32", always_2d=True)
    if len(samples) < rate * 2:
        raise ValueError("source is too short for the robustness diagnostic")

    yield "gain_0.65", encode_pcm16(samples * 0.65, rate)
    # 5% faster sample playback changes both time and pitch; not a
    # pitch-preserving tempo stretch and not a musical key transposition.
    old = np.arange(len(samples))
    new = np.arange(0, len(samples) - 1, 1.05)
    shifted = np.column_stack([
        np.interp(new, old, samples[:, channel])
        for channel in range(samples.shape[1])
    ])
    yield "speed_1.05_pitch_shifted", encode_pcm16(shifted, rate)
    del shifted
    lead = min(round(rate * 0.5), len(samples) // 4)
    delayed = np.concatenate([
        np.zeros((lead, samples.shape[1]), dtype="float32"),
        samples[:-lead],
    ])
    yield "time_offset_0.5s", encode_pcm16(delayed, rate)


def _measured_texture(audio: bytes, *, label: str) -> tuple[float, ...]:
    signature = measure_features(label, audio).texture_signature
    bins = DEFAULT_ALBUM_TEXTURE_POLICY.expected_signature_bins
    if not valid_texture_signature(signature, bins=bins):
        raise ValueError(f"{label} has no valid texture")
    assert signature is not None
    return signature


def _similarity(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return math.fsum(a * b for a, b in zip(first, second, strict=True)) / 4


def texture_score(original: bytes, modified: bytes) -> float:
    return _similarity(
        _measured_texture(original, label="original"),
        _measured_texture(modified, label="variant"),
    )


def profile_audio(source: bytes) -> dict[str, float]:
    """Score three in-memory variants without re-extracting the unchanged baseline."""
    if len(source) > _MAX_AUDIO_BYTES:
        raise ValueError("audio exceeds the diagnostic byte limit")
    baseline = _measured_texture(source, label="original")
    result: dict[str, float] = {}
    for variant, modified in audio_variants(source):
        result[variant] = _similarity(
            baseline, _measured_texture(modified, label=variant)
        )
    return result


def profile_audio_offset_search(
    source: bytes, *, search_playback_rates: bool = False,
) -> dict[str, dict[str, float | int]]:
    """Bounded diagnostic search: original offsets or opt-in speed plus offsets."""
    if len(source) > _MAX_AUDIO_BYTES:
        raise ValueError("audio exceeds the diagnostic byte limit")
    results: dict[str, dict[str, float | int]] = {}
    rates = PLAYBACK_RATES if search_playback_rates else (1.0,)
    for name, variant in audio_variants(source):
        comparison = offset_aligned_texture(source, variant, playback_rates=rates)
        if search_playback_rates:
            results[name] = {
                "fixed_score": comparison.baseline_score,
                "best_rate_offset_score": comparison.best_offset_score,
                "best_playback_rate": comparison.best_candidate_playback_rate,
                "best_offset_seconds": comparison.best_candidate_shift_seconds,
                "valid_rate_offset_pairs": comparison.examined_offsets,
            }
        else:
            results[name] = {
                "fixed_score": comparison.baseline_score,
                "best_offset_score": comparison.best_offset_score,
                "best_offset_seconds": comparison.best_candidate_shift_seconds,
                "valid_offsets": comparison.examined_offsets,
            }
    return results


def profile_unlabelled_cross_sources(
    first: bytes, second: bytes,
) -> dict[str, dict[str, float | int]]:
    """Compare two source recordings in both directions without assuming a label."""
    scores: dict[str, dict[str, float | int]] = {}
    for direction, reference, candidate in (
        ("first_to_second", first, second),
        ("second_to_first", second, first),
    ):
        aligned = offset_aligned_texture(reference, candidate)
        combined = offset_aligned_texture(
            reference, candidate, playback_rates=PLAYBACK_RATES
        )
        scores[direction] = {
            "fixed_score": aligned.baseline_score,
            "best_offset_score": aligned.best_offset_score,
            "best_rate_offset_score": combined.best_offset_score,
            "best_playback_rate": combined.best_candidate_playback_rate,
            "best_offset_seconds": combined.best_candidate_shift_seconds,
            "valid_rate_offset_pairs": combined.examined_offsets,
        }
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only same-recording texture-robustness sensitivity audit"
    )
    parser.add_argument("-p", "--package-id", action="append", required=True)
    args = parser.parse_args(argv)
    if len(args.package_id) != 2 or len(set(args.package_id)) != 2:
        parser.error("specify exactly two distinct eligible P6 package IDs")

    try:
        settings = load_settings()
        engine = build_engine(settings)
        try:
            with Session(engine) as session:
                session.execute(sa.text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                ))
                try:
                    sources, _ = load_sample_sources(
                        session, args.package_id, samples_per_album=1
                    )
                finally:
                    session.rollback()
        finally:
            engine.dispose()
        store = MinioArtifactStore(settings)
        reports: list[dict[str, object]] = []
        originals: list[bytes] = []
        for source in sources:
            original = store.fetch_verified(source.audio_uri, source.sha256)
            originals.append(original)
            comparison = profile_audio_offset_search(original)
            rate_comparison = profile_audio_offset_search(
                original, search_playback_rates=True
            )
            fixed = {
                name: float(scores["fixed_score"])
                for name, scores in comparison.items()
            }
            reports.append({
                "package_id": source.package_id,
                "song_id": source.song_id,
                "variant_scores": fixed,
                "offset_search_diagnostic": comparison,
                "rate_offset_search_diagnostic": rate_comparison,
                "variants_below_0_998": sorted(
                    name for name, score in fixed.items()
                    if score < DEFAULT_ALBUM_TEXTURE_POLICY.near_copy_threshold
                ),
            })
        cross_source = profile_unlabelled_cross_sources(originals[0], originals[1])
        aggregate = {
            score: max(float(measured[score]) for measured in cross_source.values())
            for score in ("fixed_score", "best_offset_score", "best_rate_offset_score")
        }
        pair_scores = (
            [{"left_sha256": sources[0].sha256, "right_sha256": sources[1].sha256,
              **aggregate}]
            if sources[0].sha256 != sources[1].sha256 else []
        )
        print(json.dumps({
            "unlabelled_pair_score_report": {
                "policy_version": POLICY_VERSION,
                "evaluation": EVALUATION,
                "pairs": pair_scores,
            },
            "cross_source_diagnostic": {
                "label": "unlabelled_different_recorded_checksums_not_different_music",
                "checksum_equal": sources[0].sha256 == sources[1].sha256,
                "scores": cross_source,
            },
            "scope": "two_checksum_verified_real_tracks_synthetic_in_memory_transforms",
            "threshold": DEFAULT_ALBUM_TEXTURE_POLICY.near_copy_threshold,
            "source_metadata_read_only": True,
            "store_writes": False,
            "actual_rights_or_originality_decision": False,
            "reports": reports,
        }, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - diagnostic fails, never fabricates pass
        print(json.dumps({
            "status": "unverifiable", "exception_type": type(exc).__name__,
            "store_writes": False,
        }), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
