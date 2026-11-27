"""Half-time only with an equally strong beat, a fast-tempo warning, a vocal-presence
hold and a stuck-loop warning (review 2026-11-26 #50, #51), and one decode per file.

Synthetic, deterministic signals: click tracks at known tempi, a harmonic tone whose
pitch moves like a voice (vibrato and a slow glide), held pads, a stepped melody and a
clip tiled end to end. They prove the logic and the thresholds, not that the estimates
are right on real music.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf  # type: ignore[import-untyped]

from services.quality_gate.audio import decode
from services.quality_gate.audio.rhythm import loop_repetition, vocal_presence
from services.quality_gate.evaluators.identity import (
    evaluate_identity_audio,
    load_identity_audio_policy,
)
from tests.unit.test_qa_identity_audio import _clicks

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
RATE = 22050


def _codes(result: object) -> list[str]:
    return [f.finding_code for f in result.findings]  # type: ignore[attr-defined]


def _write(path: Path, mono: np.ndarray) -> Path:
    sf.write(str(path), mono, RATE, subtype="PCM_16")
    return path


def _voice(seconds: float = 30.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    f0 = 220 * 2 ** ((40 / 1200) * np.sin(2 * np.pi * 5.5 * t) + 0.2 * np.sin(2 * np.pi * 0.3 * t))
    phase = 2 * np.pi * np.cumsum(f0) / RATE
    return 0.3 * sum(np.sin(k * phase) / k for k in range(1, 8))


def _pad(seconds: float = 30.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return 0.2 * sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.2, 329.6))


def _melody() -> np.ndarray:
    rng = np.random.default_rng(5)
    note = np.arange(int(RATE * 0.5)) / RATE
    return np.concatenate([0.3 * np.sin(2 * np.pi * f * note)
                           for f in rng.choice([220, 247, 262, 294, 330, 349, 392], 60)])


@pytest.mark.parametrize(("bpm", "blocked", "fast"), [
    (120.0, True, False), (128.0, True, False), (140.0, False, True), (170.0, False, True),
    (90.0, False, False),
])
def test_dance_tempi_are_held_and_fast_half_time_warns(
    tmp_path: Path, bpm: float, blocked: bool, fast: bool,
) -> None:
    result = evaluate_identity_audio("ev", _clicks(tmp_path / "c.wav", bpm),
                                     ["focus_electronic"], now=NOW)
    assert ("TEMPO_OUTSIDE_IDENTITY" in _codes(result)) is blocked
    assert ("TEMPO_POSSIBLY_FAST" in _codes(result)) is fast
    warning = [f for f in result.findings if f.finding_code == "TEMPO_POSSIBLY_FAST"]
    assert all(not f.blocking for f in warning)


def test_half_time_needs_an_equally_strong_beat_at_half_the_tempo() -> None:
    profile = load_identity_audio_policy().profiles["focus_electronic"]
    assert profile.tempo_reading(140.0, 0.9, 0.85, None) == (70.0, 140.0)
    assert profile.tempo_reading(140.0, 0.9, 0.3, None) == (None, None)  # weak half
    assert profile.tempo_reading(140.0, 0.9, None, None) == (None, None)  # unmeasured
    assert profile.tempo_reading(50.0, 0.9, None, 0.9) == (100.0, 100.0)
    assert profile.tempo_reading(85.0, 0.9, 0.9, 0.9) == (85.0, 170.0)
    assert profile.tempo_reading(85.0, 0.9, 0.9, 0.2) == (85.0, 85.0)


def test_a_voice_like_pitch_scores_and_steady_or_stepped_sound_does_not() -> None:
    assert vocal_presence(_voice(), RATE) == pytest.approx(1.0, abs=0.05)
    rng = np.random.default_rng(3)
    for signal in (_pad(), _melody(), 0.1 * rng.standard_normal(RATE * 30)):
        assert vocal_presence(signal, RATE) == 0.0


def test_vocals_in_an_instrumental_are_a_hold(tmp_path: Path) -> None:
    path = _write(tmp_path / "v.wav", _voice())
    held = evaluate_identity_audio("ev", path, ["focus_electronic"], now=NOW,
                                   vocal_mode="instrumental")
    (finding,) = [f for f in held.findings if f.finding_code == "VOCAL_PRESENCE_SUSPECTED"]
    assert finding.blocking is False  # a hold for a listener, not a rejection
    from services.quality_gate.detectors.reason_codes import P5_HOLD_REASON_CODES

    assert "VOCAL_PRESENCE_SUSPECTED" in P5_HOLD_REASON_CODES
    sung = evaluate_identity_audio("ev", path, ["focus_electronic"], now=NOW,
                                   vocal_mode="vocal")
    assert "VOCAL_PRESENCE_SUSPECTED" not in _codes(sung)
    pad = evaluate_identity_audio("ev", _write(tmp_path / "p.wav", _pad()),
                                  ["focus_electronic"], now=NOW, vocal_mode="instrumental")
    assert "VOCAL_PRESENCE_SUSPECTED" not in _codes(pad)


def test_a_clip_tiled_end_to_end_warns_and_a_varied_one_does_not(tmp_path: Path) -> None:
    rng = np.random.default_rng(9)
    clip = (0.1 * rng.standard_normal(RATE * 4)
            + 0.2 * np.sin(2 * np.pi * 110 * np.arange(RATE * 4) / RATE))
    tiled = np.tile(clip, 30)
    assert loop_repetition(tiled, RATE) is not None
    stuck = evaluate_identity_audio("ev", _write(tmp_path / "l.wav", 0.5 * tiled), ["rnb"],
                                    now=NOW)
    (finding,) = [f for f in stuck.findings if f.finding_code == "STUCK_LOOP_SUSPECTED"]
    assert finding.blocking is False
    varied = 0.5 * tiled + 0.05 * rng.standard_normal(len(tiled))
    ok = evaluate_identity_audio("ev", _write(tmp_path / "v.wav", varied), ["rnb"], now=NOW)
    assert "STUCK_LOOP_SUSPECTED" not in _codes(ok)
    steady = evaluate_identity_audio("ev", _write(tmp_path / "p.wav", _pad()), ["rnb"],
                                     now=NOW)
    assert "STUCK_LOOP_SUSPECTED" not in _codes(steady)


def test_inside_shared_decodes_a_file_is_decoded_once(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path / "p.wav", _pad(5.0))
    calls: list[Path] = []
    real = decode._decode_audio

    def counting(p: Path, **kwargs: object) -> object:
        calls.append(p)
        return real(p, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(decode, "_decode_audio", counting)
    with decode.shared_decodes():
        first = decode.decode_audio(path, timeout_seconds=5)
        assert decode.decode_audio(path, timeout_seconds=5) is first
    decode.decode_audio(path, timeout_seconds=5)
    assert len(calls) == 2  # once inside the block, once outside it
