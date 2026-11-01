"""P5's channel-identity audio checks (ADR-0044): tempo band and sudden loudness rises.

Synthetic audio: click tracks at a known tempo and a quiet-then-loud tone. These prove
the evaluator's logic and thresholds, not that the estimator is right on real music
(the policy notes record the calibration on real tracks).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf  # type: ignore[import-untyped]

from services.quality_gate.evaluators.identity import (
    evaluate_identity_audio,
    load_identity_audio_policy,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
RATE = 44100


def _write(path: Path, mono: np.ndarray) -> Path:
    sf.write(str(path), np.stack([mono, mono], axis=1), RATE, subtype="PCM_16")
    return path


def _clicks(path: Path, bpm: float, seconds: float = 60.0) -> Path:
    rng = np.random.default_rng(7)
    signal = 0.001 * rng.standard_normal(int(seconds * RATE))
    click = 0.6 * np.exp(-np.arange(4000) / 400.0) * rng.standard_normal(4000)
    step = 60.0 / bpm
    for beat in np.arange(0.0, seconds - 0.1, step):
        start = int(beat * RATE)
        signal[start:start + len(click)] += click[:len(signal) - start]
    return _write(path, signal)


def _codes(result: object) -> list[str]:
    return [f.finding_code for f in result.findings]  # type: ignore[attr-defined]


def _tempo_check(result: object) -> dict[str, object]:
    return next(c.segment_data for c in result.checks  # type: ignore[attr-defined]
                if c.check_type == "identity_tempo")


@pytest.mark.parametrize("bpm", [80.0, 100.0])
def test_a_tempo_inside_the_band_passes(tmp_path: Path, bpm: float) -> None:
    result = evaluate_identity_audio("ev", _clicks(tmp_path / "t.wav", bpm),
                                     ["focus_electronic"], now=NOW)
    assert "TEMPO_OUTSIDE_IDENTITY" not in _codes(result)
    assert _tempo_check(result)["bpm"] == pytest.approx(bpm, abs=2.0)


def test_a_dance_tempo_is_rejected(tmp_path: Path) -> None:
    result = evaluate_identity_audio("ev", _clicks(tmp_path / "t.wav", 124.0),
                                     ["focus_electronic", "calm"], now=NOW)
    finding = next(f for f in result.findings if f.finding_code == "TEMPO_OUTSIDE_IDENTITY")
    assert finding.blocking is True
    assert _tempo_check(result)["bpm"] == pytest.approx(124.0, abs=2.0)


def test_half_time_counts_as_inside(tmp_path: Path) -> None:
    """140 BPM chillstep is felt at 70: inside the band, half included."""
    result = evaluate_identity_audio("ev", _clicks(tmp_path / "t.wav", 140.0),
                                     ["focus_electronic"], now=NOW)
    assert "TEMPO_OUTSIDE_IDENTITY" not in _codes(result)


def test_beatless_audio_is_not_judged(tmp_path: Path) -> None:
    t = np.arange(int(40 * RATE)) / RATE
    result = evaluate_identity_audio("ev", _write(tmp_path / "pad.wav",
                                                  0.2 * np.sin(2 * np.pi * 220 * t)),
                                     ["focus_electronic"], now=NOW)
    assert _codes(result) == []
    check = _tempo_check(result)
    assert check["bpm"] is None and check["unknown_reason"] is not None


def test_a_genre_without_a_profile_is_not_checked(tmp_path: Path) -> None:
    result = evaluate_identity_audio("ev", _clicks(tmp_path / "t.wav", 124.0), ["rnb"],
                                     now=NOW)
    assert result.checks == [] and result.findings == []


def test_a_sudden_rise_warns_without_blocking(tmp_path: Path) -> None:
    t = np.arange(int(60 * RATE)) / RATE
    tone = np.sin(2 * np.pi * 330 * t)
    tone *= np.where(t < 30.0, 0.005, 0.5)  # +40 dB at 30 s
    result = evaluate_identity_audio("ev", _write(tmp_path / "drop.wav", tone),
                                     ["focus_electronic"], now=NOW)
    finding = next(f for f in result.findings if f.finding_code == "SUDDEN_LOUDNESS_RISE")
    assert finding.blocking is False


def test_a_steady_track_has_no_rise_warning(tmp_path: Path) -> None:
    result = evaluate_identity_audio("ev", _clicks(tmp_path / "t.wav", 90.0),
                                     ["focus_electronic"], now=NOW)
    assert "SUDDEN_LOUDNESS_RISE" not in _codes(result)


def test_the_policy_declares_the_focus_band() -> None:
    profile = load_identity_audio_policy().profiles["focus_electronic"]
    assert (profile.bpm_min, profile.bpm_max) == (70.0, 110.0)
    assert profile.tempo_inside(140.0) and not profile.tempo_inside(124.0)
