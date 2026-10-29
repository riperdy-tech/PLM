"""A measured near-copy check runs before any album render or external publishing."""
from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import soundfile as sf
from media_factory.audio.features import measure_features
from media_factory.errors import MediaAssemblyError
from media_factory.playlist.diversity import check_album_texture
from media_factory.rendering.assembler import assemble_media_package
from media_factory.visuals.planner import BrandProfile
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_platform.clock import FixedClock

from tests.support.media_audio import synth_track
from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    stored_manifest,
    synth_specs,
)

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def test_near_identical_audio_from_different_candidates_requires_review() -> None:
    clip = synth_track(seconds=4, bpm=120, tonic=0, minor=False)
    measured = {
        key: measure_features(key, clip)
        for key in ("a", "b")
    }
    assert measured["a"].texture_signature is not None
    with pytest.raises(MediaAssemblyError) as exc:
        check_album_texture(["a", "b"], measured)
    assert exc.value.code == "album_near_duplicate_audio"
    assert "a/b" in str(exc.value)


def test_gain_change_of_same_recording_still_requires_review() -> None:
    original = synth_track(seconds=4, bpm=120, tonic=0, minor=False)
    samples, rate = sf.read(io.BytesIO(original), always_2d=True)
    output = io.BytesIO()
    sf.write(output, samples * 0.6, rate, format="WAV", subtype="PCM_16")
    assert output.getvalue() != original
    checked = {
        "a": measure_features("a", original),
        "b": measure_features("b", output.getvalue()),
    }
    with pytest.raises(MediaAssemblyError, match="potentially near-identical"):
        check_album_texture(["a", "b"], checked)


def test_distinct_musical_keys_not_blocked_and_coverage_is_recorded() -> None:
    specs = synth_specs()
    measured = {
        f"candidate_{i}": measure_features(f"candidate_{i}", synth_track(seconds=4, **s))
        for i, s in enumerate(specs)
    }
    report = check_album_texture(list(measured), measured)
    assert report["compared_pairs"] == 3
    assert report["max_similarity"] is not None
    assert report["unmeasured_candidate_ids"] == []


def test_unmeasured_track_is_not_fabricated_as_zero_similarity() -> None:
    record = measure_features("silent", synth_track(seconds=4, bpm=120))
    missing = record.__class__(
        candidate_id="missing", bpm=None, bpm_confidence=None, camelot=None,
        key_confidence=None, energy_in=None, energy_out=None, energy_mean=None,
        vocal_density=None, extractor_version=record.extractor_version,
    )
    with pytest.raises(MediaAssemblyError) as exc:
        check_album_texture(["silent", "missing"], {"silent": record, "missing": missing})
    assert exc.value.code == "album_texture_unmeasurable"
    assert "missing" in str(exc.value)


@pytest.mark.parametrize("bad_signature", [[0.0] * 192, [1.0] * 192])
def test_non_normalised_current_texture_is_unknown(
    bad_signature: list[float],
) -> None:
    measured = measure_features("a", synth_track(seconds=4))
    malformed = replace(measured, texture_signature=tuple(bad_signature))
    with pytest.raises(MediaAssemblyError) as exc:
        check_album_texture(["a"], {"a": malformed})
    assert exc.value.code == "album_texture_unmeasurable"


def test_outdated_extractor_texture_is_unknown() -> None:
    measured = measure_features("a", synth_track(seconds=4))
    outdated = replace(measured, extractor_version="p6-features-legacy")
    with pytest.raises(MediaAssemblyError) as exc:
        check_album_texture(["a"], {"a": outdated})
    assert exc.value.code == "album_texture_unmeasurable"


def test_p6_refuses_near_copies_before_render_calls() -> None:
    store = InMemoryArtifactStore()
    common = {"bpm": 120.0, "tonic": 0, "minor": False}
    manifests = [
        stored_manifest(store, "cand_a", measured_duration_seconds=60, synth=common),
        stored_manifest(store, "cand_b", measured_duration_seconds=60, synth=common),
        stored_manifest(store, "cand_c", measured_duration_seconds=60, synth=synth_specs()[1]),
    ]
    toolchain = StandInToolchain()
    with pytest.raises(MediaAssemblyError) as exc:
        assemble_media_package(
            track_manifests=manifests,
            brand_profile=BrandProfile(brand_id="brand_neon", brand_name="Neon Records"),
            duration_range=DurationRangeSeconds(min_seconds=170, max_seconds=180),
            visual_profile="neon_waveform",
            playlist_concept="Rain at Midnight",
            store=store,
            toolchain=toolchain,
            render_profile_name="preview_360p_v1",
            clock=FixedClock(NOW),
        )
    assert exc.value.code == "album_near_duplicate_audio"
    assert toolchain.calls == []
