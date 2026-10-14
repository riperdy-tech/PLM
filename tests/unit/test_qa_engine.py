"""Unit tests for Phase P5 Gate Engine and Acceptance Scenarios (P5 §45-§51, §90, R7, R8)."""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

from quality_gate.domain.enums import GateDisposition, QuarantineStatus
from quality_gate.gate.engine import run_candidate_evaluation

from tests.support.seams import candidate_event, lineage_fields, rights_fields


def _make_wav(duration: float = 1.0, amplitude: int = 15000) -> Path:
    temp_dir = tempfile.gettempdir()
    path = Path(temp_dir) / f"engine_test_{int(duration*1000)}_{amplitude}.wav"
    sample_rate = 44100
    total_frames = int(duration * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(total_frames):
            t = float(i) / sample_rate
            val = int(amplitude * math.sin(2.0 * math.pi * 440.0 * t))
            frames.extend(struct.pack("<hh", val, val))
        wf.writeframes(frames)
    return path


def test_scenario_1_clean_candidate_passes() -> None:
    wav_path = _make_wav(duration=1.0)
    try:
        rights_snap = rights_fields(plan_or_tier='pro', account_identity_ref='acc_001', terms_version='v1.0', commercial_use_status='eligible')
        res = run_candidate_evaluation(
            event=candidate_event(
                candidate_id="cand_clean_01",
                artifact_id="art_01",
                track_spec_id="spec_01",
                vocal_mode="instrumental",
                prompt_text="ambient synth pad electronic chill",
                requested_duration_seconds=int(1.0),
                lineage=lineage_fields(experiment_id="exp_01", content_arm_id="arm_01"),
                rights=rights_snap,
            ),
            audio_path=wav_path,
        )
        assert res.disposition == GateDisposition.PASSED
        assert res.quarantine_case is None
        assert res.release_manifest is not None
        assert res.release_manifest.disposition == GateDisposition.PASSED
        assert res.release_manifest.ai_disclosure_required is True
        assert res.evaluation.technical_status == "PASSED"
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_scenario_2_soft_warning_passes_with_warnings() -> None:
    # Use quiet amplitude to induce a loudness deviation warning
    wav_path = _make_wav(duration=1.0, amplitude=200)
    try:
        rights_snap = rights_fields(plan_or_tier='pro', account_identity_ref='acc_001', terms_version='v1.0', commercial_use_status='eligible')
        res = run_candidate_evaluation(
            event=candidate_event(
                candidate_id="cand_warn_01",
                artifact_id="art_01",
                track_spec_id="spec_01",
                vocal_mode="instrumental",
                prompt_text="ambient electronic",
                requested_duration_seconds=int(1.0),
                lineage=lineage_fields(experiment_id="exp_01", content_arm_id="arm_01"),
                rights=rights_snap,
            ),
            audio_path=wav_path,
        )
        assert res.disposition == GateDisposition.PASSED_WITH_WARNINGS
        assert res.quarantine_case is None
        assert res.release_manifest is not None
        assert res.release_manifest.disposition == GateDisposition.PASSED_WITH_WARNINGS
        assert len(res.release_manifest.warning_codes) > 0
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_scenario_3_unknown_license_routes_to_quarantine() -> None:
    wav_path = _make_wav(duration=1.0)
    try:
        # Incomplete R6 provenance fields
        rights_snap = rights_fields(plan_or_tier='pro', commercial_use_status='unknown')
        res = run_candidate_evaluation(
            event=candidate_event(
                candidate_id="cand_quar_01",
                artifact_id="art_01",
                track_spec_id="spec_01",
                requested_duration_seconds=int(1.0),
                lineage=lineage_fields(experiment_id="exp_01", content_arm_id="arm_01"),
                rights=rights_snap,
            ),
            audio_path=wav_path,
        )
        assert res.disposition == GateDisposition.QUARANTINED
        assert res.quarantine_case is not None
        assert res.quarantine_case.status == QuarantineStatus.OPEN
        assert res.release_manifest is None  # Quarantined candidates must NOT produce release manifest
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_scenario_4_free_tier_hard_reject() -> None:
    wav_path = _make_wav(duration=1.0)
    try:
        rights_snap = rights_fields(plan_or_tier='free', account_identity_ref='acc_001', terms_version='v1.0', commercial_use_status='eligible')
        res = run_candidate_evaluation(
            event=candidate_event(
                candidate_id="cand_free_01",
                artifact_id="art_01",
                track_spec_id="spec_01",
                requested_duration_seconds=int(1.0),
                lineage=lineage_fields(experiment_id="exp_01", content_arm_id="arm_01"),
                rights=rights_snap,
            ),
            audio_path=wav_path,
        )
        assert res.disposition == GateDisposition.REJECTED
        assert res.release_manifest is None
        assert any(f.finding_code == "COMMERCIAL_USE_INELIGIBLE_TIER" for f in res.evaluation.risk_findings)
    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_scenario_5_corrupt_or_missing_audio_hard_reject() -> None:
    res = run_candidate_evaluation(
            event=candidate_event(
                candidate_id="cand_missing_01",
                artifact_id="art_01",
                track_spec_id="spec_01",
                requested_duration_seconds=int(1.0),
                lineage=lineage_fields(experiment_id="exp_01", content_arm_id="arm_01"),
                rights=None,
            ),
            audio_path="missing_file.wav",
        )
    assert res.disposition == GateDisposition.REJECTED
    assert res.evaluation.technical_status == "FAILED"
    assert res.release_manifest is None


def test_scenario_6_protected_artist_likeness_hard_reject() -> None:
    wav_path = _make_wav(duration=1.0)
    try:
        rights_snap = rights_fields(plan_or_tier='pro', account_identity_ref='acc_001', terms_version='v1.0', commercial_use_status='eligible')
        res = run_candidate_evaluation(
            event=candidate_event(
                candidate_id="cand_likeness_01",
                artifact_id="art_01",
                track_spec_id="spec_01",
                prompt_text="synth pop song sounding exactly like Drake",
                requested_duration_seconds=int(1.0),
                lineage=lineage_fields(experiment_id="exp_01", content_arm_id="arm_01"),
                rights=rights_snap,
            ),
            audio_path=wav_path,
        )
        assert res.disposition == GateDisposition.REJECTED
        assert res.evaluation.policy_status == "NON_COMPLIANT"
        assert res.release_manifest is None
    finally:
        if wav_path.is_file():
            wav_path.unlink()
