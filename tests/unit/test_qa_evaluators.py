"""Unit tests for Phase P5 individual evaluators and validators (P5 §11-§40)."""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

from quality_gate.evaluators.lyrics import evaluate_lyrics
from quality_gate.evaluators.musical import evaluate_musical_qualities
from quality_gate.evaluators.policy import screen_policy_and_risk
from quality_gate.evaluators.rights import validate_provenance_and_rights
from quality_gate.evaluators.technical import validate_technical_audio


def _make_test_wav(
    duration: float = 1.0,
    sample_rate: int = 44100,
    amplitude: int = 16000,
    clip: bool = False,
) -> Path:
    temp_dir = tempfile.gettempdir()
    path = Path(temp_dir) / f"test_eval_{int(duration*1000)}_{amplitude}_{clip}.wav"
    total_frames = int(duration * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(total_frames):
            t = float(i) / sample_rate
            val = 32767 if clip else int(amplitude * math.sin(2.0 * math.pi * 440.0 * t))
            val = max(-32768, min(32767, val))
            frames.extend(struct.pack("<hh", val, val))
        wf.writeframes(frames)
    return path


def test_technical_audio_validator_clean_file() -> None:
    wav_path = _make_test_wav(duration=1.0, sample_rate=44100, amplitude=15000)
    try:
        res = validate_technical_audio(
            evaluation_id="eval_test",
            audio_path=wav_path,
            expected_duration_seconds=1.0,
            expected_sample_rate=44100,
            expected_channels=2,
        )
        assert not res.has_blocking_defects
        assert len(res.sha256) == 64
        assert res.sample_rate == 44100
        assert res.channels == 2
        assert len(res.evidence) >= 1
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_technical_audio_validator_file_not_found() -> None:
    res = validate_technical_audio(
        evaluation_id="eval_test",
        audio_path="nonexistent_audio_file.wav",
        expected_duration_seconds=1.0,
    )
    assert res.has_blocking_defects
    assert any(f.finding_code == "FILE_NOT_FOUND" for f in res.findings)


def test_technical_audio_validator_total_silence() -> None:
    wav_path = _make_test_wav(duration=1.0, sample_rate=44100, amplitude=0)
    try:
        res = validate_technical_audio(
            evaluation_id="eval_test",
            audio_path=wav_path,
            expected_duration_seconds=1.0,
        )
        assert res.has_blocking_defects
        assert any(f.finding_code == "TOTAL_SILENCE_DETECTED" for f in res.findings)
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_technical_audio_validator_clipping() -> None:
    wav_path = _make_test_wav(duration=0.5, sample_rate=44100, amplitude=32767, clip=True)
    try:
        res = validate_technical_audio(
            evaluation_id="eval_test",
            audio_path=wav_path,
            expected_duration_seconds=0.5,
        )
        # Clipping is a warning, not hard blocking
        assert not res.has_blocking_defects
        assert any(f.finding_code == "SEVERE_CLIPPING_DETECTED" for f in res.findings)
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_musical_evaluator() -> None:
    wav_path = _make_test_wav(duration=1.0, sample_rate=44100, amplitude=16000)
    try:
        res = evaluate_musical_qualities("eval_test", wav_path)
        assert 0.0 <= res.composite_score <= 1.0
        assert res.confidence > 0.5
        assert len(res.checks) >= 2
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_lyric_evaluator() -> None:
    # 1. Clean instrumental
    res_inst = evaluate_lyrics("eval_test", vocal_mode="instrumental", lyrics_text="")
    assert res_inst.is_clean
    assert len(res_inst.findings) == 0

    # 2. Unexpected lyrics in instrumental
    res_unexp = evaluate_lyrics("eval_test", vocal_mode="instrumental", lyrics_text="la la la singing lyrics")
    assert res_unexp.is_clean  # warning, not blocking
    assert any(f.finding_code == "UNEXPECTED_LYRICS_FOR_INSTRUMENTAL" for f in res_unexp.findings)

    # 3. Prohibited content
    res_tox = evaluate_lyrics("eval_test", vocal_mode="vocal", lyrics_text="contains hate_speech_sample_word here")
    assert not res_tox.is_clean
    assert any(f.finding_code == "PROHIBITED_CONTENT_IN_LYRICS" for f in res_tox.findings)


def test_rights_and_provenance_evaluator() -> None:
    # 1. Clean R6 compliant pro tier
    valid_snap = {
        "plan_or_tier": "pro",
        "account_identity_ref": "acc_001",
        "terms_version": "terms_2026",
        "downloaded_at": "2026-09-25T10:00:00Z",
        "commercial_use_status": "ELIGIBLE",
    }
    res_clean = validate_provenance_and_rights("eval_test", valid_snap)
    assert res_clean.is_cleared
    assert res_clean.commercial_use_status == "ELIGIBLE"
    assert res_clean.provenance_status == "COMPLETE"

    # 2. Free tier (ineligible for commercial use)
    free_snap = {
        "plan_or_tier": "free",
        "account_identity_ref": "acc_001",
        "terms_version": "terms_2026",
        "downloaded_at": "2026-09-25T10:00:00Z",
    }
    res_free = validate_provenance_and_rights("eval_test", free_snap)
    assert not res_free.is_cleared
    assert res_free.commercial_use_status == "INELIGIBLE"
    assert any(f.finding_code == "COMMERCIAL_USE_INELIGIBLE_TIER" for f in res_free.findings)

    # 3. Missing R6 fields -> quarantine
    missing_r6 = {
        "plan_or_tier": "pro",
        "terms_version": "terms_2026",
    }
    res_miss = validate_provenance_and_rights("eval_test", missing_r6)
    assert not res_miss.is_cleared
    assert res_miss.provenance_status == "INCOMPLETE"
    assert res_miss.requires_quarantine


def test_policy_screener() -> None:
    # 1. Clean prompt with AI disclosure
    res_clean = screen_policy_and_risk("eval_test", "lo-fi study beats chill instrumental", ["lofi", "ambient"])
    assert res_clean.is_compliant
    assert res_clean.ai_disclosure_required is True

    # 2. Protected artist name in prompt
    res_art = screen_policy_and_risk("eval_test", "song in the style of Taylor Swift vocals", ["pop"])
    assert not res_art.is_compliant
    assert any(f.finding_code == "PROTECTED_ARTIST_REFERENCE_DETECTED" for f in res_art.findings)
