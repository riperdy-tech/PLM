"""Golden-fixture behaviour of the P5 technical and musical audio evaluators (R4).

Each fixture is synthesised deterministically (``tests.support.audio_fixtures``) and
written as a WAV, so these run on any host. They prove what the evaluators decide for
known signals through the WAV/libsndfile path. FFmpeg decoding of non-WAV inputs is
proved separately by the ``media`` tests in ``test_qa_audio_media.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from plm_contracts.seams.c3 import DurationRangeSeconds
from quality_gate.audio import decode as decode_module
from quality_gate.audio.policy import load_audio_qa_policy, parse_audio_qa_policy
from quality_gate.domain.enums import CheckStatus
from quality_gate.evaluators.musical import evaluate_musical_qualities
from quality_gate.evaluators.technical import (
    TechnicalValidationResult,
    validate_technical_audio,
)

from tests.support.audio_fixtures import (
    CLEAN_LUFS,
    RATE,
    clean_sine,
    inter_sample_peak,
    reference_minus_14_lufs,
    sine,
    write_wav,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ANY_SHORT = DurationRangeSeconds(min_seconds=1, max_seconds=30)
POLICY = load_audio_qa_policy()


def _evaluate(path: Path, **kwargs: object) -> TechnicalValidationResult:
    return validate_technical_audio(
        "eval_golden", path, ANY_SHORT, now=NOW, **kwargs  # type: ignore[arg-type]
    )


def _codes(res: TechnicalValidationResult) -> dict[str, bool]:
    return {f.finding_code: f.blocking for f in res.findings}


def _check(res: TechnicalValidationResult, check_type: str):  # type: ignore[no-untyped-def]
    return next(c for c in res.checks if c.check_type == check_type)


def test_clean_fixture_passes_every_technical_check(tmp_path: Path) -> None:
    """Proves a defect-free -14 LUFS stereo sine passes with no findings at all, and
    that each check carries the policy version and a measured value."""
    res = _evaluate(write_wav(tmp_path / "clean.wav", clean_sine()))
    assert res.findings == []
    assert {c.status for c in res.checks} == {CheckStatus.PASS}
    assert {c.config_version for c in res.checks} == {POLICY.policy_version}
    assert res.policy_version == POLICY.policy_version
    assert _check(res, "integrated_loudness").measured_value == pytest.approx(CLEAN_LUFS, abs=0.1)
    assert res.measurements is not None and res.measurements.decoder.startswith("libsndfile")


def test_reference_fixture_measures_minus_14_lufs_and_records_evidence(tmp_path: Path) -> None:
    """Proves the measured loudness of the -14 LUFS reference file is within 0.1 LU, and
    that every measurement is recorded once as hashed evidence naming the policy."""
    res = _evaluate(reference_minus_14_lufs(tmp_path / "ref.wav"))
    assert _check(res, "integrated_loudness").measured_value == pytest.approx(-14.0, abs=0.1)
    evidence = next(e for e in res.evidence if e.evidence_type == "audio_measurements")
    record = json.loads(evidence.summary)
    assert record["policy_version"] == POLICY.policy_version
    assert record["integrated_lufs"] == pytest.approx(-14.0, abs=0.1)
    # 48 kHz is a recorded variance against the 44.1 kHz profile, not a rejection.
    assert _check(res, "sample_rate").status == CheckStatus.WARN
    assert not res.has_blocking_defects


def test_hard_clipped_signal_is_blocked(tmp_path: Path) -> None:
    """Proves sample-domain clipping (runs of full-scale samples) is a blocking finding
    carrying the measured run count."""
    clipped = np.clip(sine(220.0, 2.0, 2.0), -1.0, 1.0)
    res = _evaluate(write_wav(tmp_path / "clipped.wav", clipped))
    assert _codes(res)["SEVERE_CLIPPING_DETECTED"] is True
    check = _check(res, "clipping_check")
    assert check.status == CheckStatus.FAIL
    assert check.measured_value > 0
    assert res.has_blocking_defects


def test_inter_sample_peak_is_caught_by_true_peak_not_sample_peak(tmp_path: Path) -> None:
    """Proves the true-peak check flags an over that the sample-domain checks cannot
    see: sample peak below 0 dBFS, no clipping, true peak above 0 dBTP."""
    res = _evaluate(write_wav(tmp_path / "isp.wav", inter_sample_peak()))
    assert _check(res, "sample_peak").measured_value < 0.0
    assert _check(res, "clipping_check").status == CheckStatus.PASS
    assert _check(res, "true_peak").measured_value > 0.0
    finding = next(f for f in res.findings if f.finding_code == "TRUE_PEAK_ABOVE_CEILING")
    assert finding.severity == "MEDIUM" and not finding.blocking
    assert "SEVERE_CLIPPING_DETECTED" not in _codes(res)


def test_three_seconds_of_leading_silence_is_flagged(tmp_path: Path) -> None:
    """Proves leading silence is measured in seconds against the declared threshold and
    flagged when it exceeds the declared maximum (2 s)."""
    audio = np.concatenate([np.zeros((3 * RATE, 2)), clean_sine(3.0)])
    res = _evaluate(write_wav(tmp_path / "lead.wav", audio))
    finding = next(f for f in res.findings if f.finding_code == "LEADING_SILENCE_EXCESSIVE")
    assert not finding.blocking
    assert finding.start_seconds == 0.0
    assert _check(res, "leading_silence").measured_value == pytest.approx(3.0, abs=0.001)
    assert _check(res, "trailing_silence").status == CheckStatus.PASS


def test_inverted_polarity_stereo_pair_is_blocked(tmp_path: Path) -> None:
    """Proves an L = -R pair measures correlation -1 and is blocked as phase-inverted
    (its mono fold-down cancels)."""
    left = clean_sine()[:, 0]
    res = _evaluate(write_wav(tmp_path / "inverted.wav", np.stack([left, -left], axis=1)))
    assert _check(res, "stereo_correlation").measured_value == pytest.approx(-1.0, abs=1e-6)
    assert _codes(res)["STEREO_PHASE_INVERTED"] is True


def test_total_silence_is_blocked_and_its_loudness_is_unknown(tmp_path: Path) -> None:
    """Proves a silent file is blocked, and its unmeasurable loudness and correlation are
    UNKNOWN blocking findings rather than numbers."""
    res = _evaluate(write_wav(tmp_path / "silent.wav", np.zeros((2 * RATE, 2))))
    codes = _codes(res)
    assert codes["TOTAL_SILENCE_DETECTED"] is True
    assert codes["LOUDNESS_UNMEASURABLE"] is True
    assert codes["STEREO_CORRELATION_UNMEASURABLE"] is True


def test_corrupt_wav_is_a_blocking_decode_failure(tmp_path: Path) -> None:
    """Proves a file with a WAVE header but a malformed body is never passed: decoding
    fails with a reason code that blocks."""
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\xff" * 40)
    res = _evaluate(path)
    assert _codes(res) == {"DECODE_FAILED": True}
    assert _check(res, "container_valid").status == CheckStatus.FAIL
    assert len(res.sha256) == 64  # the bytes were still checksummed as evidence


def test_non_wav_without_a_decoder_is_blocked_not_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves that when FFmpeg is absent a non-WAV input is a blocking
    DECODER_UNAVAILABLE, never a skipped check."""
    monkeypatch.setattr(decode_module.shutil, "which", lambda _name: None)
    path = tmp_path / "track.mp3"
    path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(range(256)) * 8)
    res = _evaluate(path)
    assert _codes(res) == {"DECODER_UNAVAILABLE": True}


def test_too_short_file_has_unknown_loudness_and_is_not_passed(tmp_path: Path) -> None:
    """Proves a 0.2 s file (below one 400 ms gating block) gets an UNKNOWN loudness
    check and a blocking UNKNOWN finding, and the musical evaluator scores it 0 with a
    blocking finding instead of a pass."""
    path = write_wav(tmp_path / "short.wav", clean_sine(0.2))
    res = _evaluate(path)
    check = _check(res, "integrated_loudness")
    assert check.status == CheckStatus.UNKNOWN and check.measured_value is None
    finding = next(f for f in res.findings if f.finding_code == "LOUDNESS_UNMEASURABLE")
    assert finding.blocking and finding.status == "UNKNOWN"
    assert "too_short_for_gating_block" in finding.message

    musical = evaluate_musical_qualities("eval_golden", path, now=NOW)
    assert musical.composite_score == 0.0 and musical.confidence == 0.0
    assert [(f.finding_code, f.blocking) for f in musical.findings] == [
        ("MUSICAL_ANALYSIS_UNAVAILABLE", True)
    ]


def test_same_bytes_give_identical_measurements_and_findings(tmp_path: Path) -> None:
    """Proves determinism: two evaluations of the same bytes produce equal checks,
    findings (ids included), evidence and measurements."""
    audio = np.concatenate([np.zeros((3 * RATE, 2)), inter_sample_peak()])
    path = write_wav(tmp_path / "det.wav", audio)
    first, second = _evaluate(path), _evaluate(path)
    assert first.checks == second.checks
    assert first.findings == second.findings
    assert first.evidence == second.evidence
    assert first.measurements == second.measurements
    # The fixture writer is deterministic too: regenerating gives the same bytes.
    again = write_wav(tmp_path / "det2.wav", audio)
    assert again.read_bytes() == path.read_bytes()


def test_thresholds_come_from_the_policy_object(tmp_path: Path) -> None:
    """Proves the decision follows the declared policy, not a constant in code: the same
    3 s lead-in passes under a policy allowing 4 s, and the checks carry that policy's
    version."""
    doc = load_audio_qa_policy().model_dump(mode="json")
    doc["policy_version"] = "test-lenient-silence"
    doc["silence"]["max_leading_seconds"] = 4.0
    lenient = parse_audio_qa_policy(json.dumps(doc))
    audio = np.concatenate([np.zeros((3 * RATE, 2)), clean_sine(3.0)])
    res = _evaluate(write_wav(tmp_path / "lead.wav", audio), policy=lenient)
    assert "LEADING_SILENCE_EXCESSIVE" not in _codes(res)
    assert {c.config_version for c in res.checks} == {"test-lenient-silence"}


def test_musical_loudness_is_judged_against_the_declared_target(tmp_path: Path) -> None:
    """Proves the musical evaluator judges measured BS.1770 loudness against the policy
    target: a -14 LUFS file is on target, a -40 LUFS file is a loudness deviation."""
    on_target = evaluate_musical_qualities(
        "eval_golden", write_wav(tmp_path / "on.wav", clean_sine()), now=NOW
    )
    assert on_target.findings == []
    loudness = next(c for c in on_target.checks if c.check_type == "loudness_lufs")
    assert loudness.segment_data["integrated_lufs"] == pytest.approx(-14.0, abs=0.1)
    assert loudness.model_version == POLICY.policy_version

    quiet = evaluate_musical_qualities(
        "eval_golden", write_wav(tmp_path / "quiet.wav", sine(997.0, 0.01, 2.0)), now=NOW
    )
    finding = next(f for f in quiet.findings if f.finding_code == "LOUDNESS_DEVIATION")
    assert not finding.blocking and finding.severity == "MEDIUM"
