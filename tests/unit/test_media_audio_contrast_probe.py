"""Read-only P6 contrast proxies: distinct known signals, unknowns and CLI safety."""
from __future__ import annotations

import hashlib
import io
import json

import numpy as np
import pytest
import soundfile as sf
from media_factory.audio.contrast_probe import (
    POLICY_VERSION,
    ContrastFeatures,
    measure_contrast,
    probe_album_contrast,
    probe_album_features,
)

from scripts import probe_album_audio_contrast
from tests.support.media_audio import synth_track


@pytest.fixture(scope="module")
def samples() -> dict[str, bytes]:
    return {
        "base": synth_track(seconds=4, bpm=120, tonic=0),
        "same": synth_track(seconds=4, bpm=120, tonic=0),
        "tonic": synth_track(seconds=4, bpm=120, tonic=5),
        "rhythm": synth_track(seconds=4, bpm=84, tonic=0),
    }


def test_same_delivered_audio_is_similar_but_not_cleared(samples: dict[str, bytes]) -> None:
    report = probe_album_contrast({"one": samples["base"], "two": samples["same"]})
    assert report["policy_version"] == POLICY_VERSION
    assert report["evaluation"] == "diagnostic_only_no_threshold_no_clearance"
    pair = report["pairs"][0]
    assert all(value is not None and value > 0.999 for value in pair["metrics"].values())
    assert report["coverage"]["one"]["sha256"] == hashlib.sha256(samples["base"]).hexdigest()
    assert report["limitations"] and not any(
        word in report for word in ("passed", "approved", "cleared")
    )


def test_gain_change_keeps_proxy_scores_high(samples: dict[str, bytes]) -> None:
    louder = synth_track(seconds=4, bpm=120, tonic=0, amplitude=0.4)
    report = probe_album_contrast({"loud": louder, "quiet": samples["base"]})
    scores = report["pairs"][0]["metrics"]
    assert all(value is not None and value > 0.99 for value in scores.values())


def test_longer_multiblock_audio_and_alternate_sample_rate() -> None:
    left = synth_track(seconds=12.1, bpm=110, tonic=0, rate=48000)
    right = synth_track(seconds=12.1, bpm=110, tonic=0, rate=48000, amplitude=0.4)
    report = probe_album_contrast({"left": left, "right": right})
    scores = report["pairs"][0]["metrics"]
    assert all(score is not None and score > 0.99 for score in scores.values())
    assert all(not row["unknown"] for row in report["coverage"].values())


def test_one_second_clip_never_produces_nonfinite_similarity() -> None:
    short = synth_track(seconds=1.01, bpm=120, tonic=0)
    report = probe_album_contrast({"left": short, "right": short})
    scores = report["pairs"][0]["metrics"]
    assert scores["pitch_class_motion_proxy"] is None
    assert "pitch_class_motion_proxy" in report["coverage"]["left"]["unknown"]
    assert all(score is None or 0 <= score <= 1 for score in scores.values())


def test_precomputed_features_match_in_memory_pair_report(samples: dict[str, bytes]) -> None:
    raw = {"first": samples["base"], "second": samples["tonic"]}
    measured = {name: measure_contrast(payload) for name, payload in raw.items()}
    assert probe_album_features(measured) == probe_album_contrast(raw)


def test_overlong_audio_header_refused_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    class OversizedReader:
        samplerate = 44100
        channels = 2
        frames = 44100 * 901

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, **_kwargs):
            pytest.fail("unsupported duration must be refused without PCM decoding")

    monkeypatch.setattr(
        "media_factory.audio.contrast_probe.sf.SoundFile", lambda _: OversizedReader(),
    )
    result = measure_contrast(b"untrusted header placeholder")
    assert all(vector is None for vector in result.vectors.values())
    assert set(result.unknown.values()) == {"unsupported_rate_channels_or_length"}


@pytest.mark.parametrize(("replacement", "reason"), [
    (tuple([float("nan")] + [0.0] * 31), "invalid_feature_vector"),
    (tuple([float("inf")] + [0.0] * 31), "invalid_feature_vector"),
    ((1.0,), "invalid_feature_vector"),
    (tuple([2.0] + [0.0] * 31), "invalid_feature_vector"),
    (tuple([-1.0] + [0.0] * 31), "invalid_feature_vector"),
    (None, "not_measured"),
])
def test_invalid_precomputed_vectors_become_unknown_not_high_similarity(
    samples: dict[str, bytes], replacement: tuple[float, ...] | None, reason: str,
) -> None:
    baseline = measure_contrast(samples["base"])
    assert baseline.vectors["rhythm_onset_proxy"] is not None
    invalid = ContrastFeatures(
        sha256=baseline.sha256,
        vectors={**baseline.vectors, "rhythm_onset_proxy": replacement},
        unknown={},
    )
    report = probe_album_features({"left": invalid, "right": baseline})
    pair = report["pairs"][0]["metrics"]
    assert pair["rhythm_onset_proxy"] is None
    assert report["coverage"]["left"]["unknown"]["rhythm_onset_proxy"] == reason
    assert pair["pitch_class_motion_proxy"] is not None
    assert baseline.vectors["rhythm_onset_proxy"] is not None  # no mutation


def test_resampler_nonfinite_values_fail_unknown(
    monkeypatch: pytest.MonkeyPatch, samples: dict[str, bytes],
) -> None:
    monkeypatch.setattr(
        "media_factory.audio.contrast_probe.resample_poly",
        lambda samples, *_args: np.full(len(samples), float("nan")),
    )
    result = measure_contrast(samples["base"])
    assert all(v is None for v in result.vectors.values())
    assert set(result.unknown.values()) == {"nonfinite_resampling"}


def test_out_of_range_float_audio_fails_unknown() -> None:
    buffer = io.BytesIO()
    sf.write(buffer, np.full(44100 * 2, 20.0), 44100, format="WAV", subtype="FLOAT")
    result = measure_contrast(buffer.getvalue())
    assert all(v is None for v in result.vectors.values())
    assert set(result.unknown.values()) == {"out_of_range_pcm"}


def test_different_tonal_content_reduces_pitch_proxy(samples: dict[str, bytes]) -> None:
    report = probe_album_contrast({"a": samples["base"], "b": samples["tonic"]})
    pitch = report["pairs"][0]["metrics"]["pitch_class_motion_proxy"]
    assert pitch is not None and pitch < 0.99


def test_different_beat_pattern_reduces_onset_proxy(samples: dict[str, bytes]) -> None:
    report = probe_album_contrast({"a": samples["base"], "b": samples["rhythm"]})
    rhythm = report["pairs"][0]["metrics"]["rhythm_onset_proxy"]
    assert rhythm is not None and rhythm < 0.99


def test_short_and_silent_audio_remain_unknown_not_zero(samples: dict[str, bytes]) -> None:
    wav = io.BytesIO()
    sf.write(wav, np.zeros(44100 * 2), 44100, format="WAV")
    report = probe_album_contrast({"short": b"not a wav", "silence": wav.getvalue()})
    for item in report["pairs"]:
        assert all(score is None for score in item["metrics"].values())
    assert set(report["coverage"]["short"]["unknown"]) == {
        "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
    }
    assert all(report["coverage"]["silence"]["unknown"].values())


@pytest.mark.parametrize("files", [{}, {"solo": b""}, {str(i): b"" for i in range(21)}])
def test_out_of_bounds_album_is_rejected(files: dict[str, bytes]) -> None:
    with pytest.raises(ValueError):
        probe_album_contrast(files)


def test_cli_requires_matching_sha256_and_never_writes_input(
    tmp_path, capsys, samples: dict[str, bytes],
) -> None:
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    a.write_bytes(samples["base"])
    b.write_bytes(samples["tonic"])
    original = a.read_bytes()
    base_args = ["--track", f"A={a}", "--track", f"B={b}"]
    failed = base_args + ["--sha256", "A=" + "0" * 64]
    assert probe_album_audio_contrast.main(failed) == 2
    assert "checksum mismatch" in capsys.readouterr().err
    valid = base_args + ["--sha256", "A=" + hashlib.sha256(original).hexdigest()]
    assert probe_album_audio_contrast.main(valid) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provided_checksum_verified"] == ["A"]
    assert report["evaluation"] == "diagnostic_only_no_threshold_no_clearance"
    assert a.read_bytes() == original
    assert b.read_bytes() == samples["tonic"]


def test_cli_rejects_duplicate_ids(tmp_path, samples: dict[str, bytes], capsys) -> None:
    path = tmp_path / "audio.wav"
    path.write_bytes(samples["base"])
    assert probe_album_audio_contrast.main([
        "--track", f"x={path}", "--track", f"x={path}",
    ]) == 2
    assert "unique" in capsys.readouterr().err
