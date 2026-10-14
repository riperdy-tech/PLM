"""Controlled production dry-run smoke test for PHASE-P5 (P5 §94, R7, R8).

Runs the full P5 release gate pipeline hermetically:
C4 candidate ready event → technical audio validation → musical analysis
→ rights & R6 provenance verification → policy check → gate decision
→ release manifest generation (Seam C5).
"""

from __future__ import annotations

import hashlib
import math
import struct
import tempfile
import wave
from pathlib import Path

from plm_contracts import CommercialUseEligibility
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_contracts.seams.c4 import CandidateReadyPayload, MeasuredAudio, RightsSnapshotPayload
from plm_platform.clock import Clock, SystemClock
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..domain.enums import GateDisposition
from ..gate.engine import run_candidate_evaluation
from ..storage.repositories import (
    get_release_manifest_by_candidate,
    insert_candidate_evaluation,
    insert_evaluation_snapshot,
    insert_gate_decision,
    insert_release_manifest,
)

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending quality gate service token (PLM_SERVICE_TOKEN)"


def _create_temp_wav(
    *, stamp: int, duration_seconds: float = 2.0, sample_rate: int = 44100
) -> Path:
    """Create a deterministic clean PCM 16-bit stereo WAV tone for test."""
    temp_dir = tempfile.gettempdir()
    path = Path(temp_dir) / f"p5_smoke_{stamp}.wav"
    total_frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(total_frames):
            t = float(i) / sample_rate
            val = int(16000.0 * math.sin(2.0 * math.pi * 440.0 * t))
            sample_bytes = struct.pack("<hh", val, val)
            frames.extend(sample_bytes)
        wf.writeframes(frames)
    return path


def run_smoke_test(*, clock: Clock | None = None) -> str | None:
    """Run the P5 dry-run smoke test. Returns None on success, or blocked reason string."""
    clock = clock or SystemClock()
    now = clock.now()
    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON

    # Test database connectivity
    engine = build_engine(platform)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return f"blocked: database unreachable ({exc})"

    wav_path = _create_temp_wav(stamp=int(now.timestamp()), duration_seconds=2.0)
    try:
        factory = session_factory(engine)
        with factory() as session:
            candidate_id = f"cand_smoke_{int(now.timestamp())}"
            artifact_id = f"art_smoke_{int(now.timestamp())}"

            event = CandidateReadyPayload.build(
                event_type="music_candidate_ready_for_p5",
                candidate_id=candidate_id,
                artifact_id=artifact_id,
                artifact_uri=str(wav_path),
                artifact_sha256=hashlib.sha256(wav_path.read_bytes()).hexdigest(),
                track_spec_id="spec_smoke_01",
                lineage=ExperimentLineage(
                    experiment_id="exp_smoke_01",
                    experiment_family_id="fam_smoke_01",
                    content_arm_id="arm_smoke_01",
                    content_genome_id="gen_smoke_01",
                    production_directive_id="dir_smoke_01",
                ),
                provider_id="mock",
                model_id="mock-v1",
                measured_audio=MeasuredAudio(duration_seconds=2.0, sample_rate=44100, channels=2),
                requested_duration_seconds=2,
                requested_duration_range=DurationRangeSeconds(min_seconds=1, max_seconds=3),
                vocal_mode="instrumental",
                genre_tags=("ambient",),
                prompt_text="ambient electronic relaxing beat",
                lyrics_text=None,
                rights=RightsSnapshotPayload(
                    commercial_use_status=CommercialUseEligibility.ELIGIBLE,
                    basis="plm_internal_synthesis",
                    license_reference="plm-mock-provider",
                    plan_or_tier="internal",
                    account_identity_ref="plm-mock",
                    terms_version="mock-v1",
                    attempt_id="att_smoke_01",
                    provider_id="mock",
                    policy_version="p4-v1",
                    checked_at=now,
                    downloaded_at=now,
                ),
            )
            result = run_candidate_evaluation(event=event, audio_path=wav_path, clock=clock)

            if result.disposition not in (GateDisposition.PASSED, GateDisposition.PASSED_WITH_WARNINGS):
                return f"smoke test evaluation failed with disposition: {result.disposition}"

            if not result.release_manifest:
                return "smoke test failed: no release manifest generated for cleared candidate"

            # Persist and query back
            insert_candidate_evaluation(session, result.evaluation)
            insert_evaluation_snapshot(session, result.snapshot)
            insert_gate_decision(session, result.decision)
            insert_release_manifest(session, result.release_manifest)
            session.commit()

            fetched = get_release_manifest_by_candidate(session, candidate_id)
            if not fetched:
                return "smoke test failed: persisted release manifest could not be retrieved"
            if fetched.p5_manifest_id != result.release_manifest.p5_manifest_id:
                return "smoke test failed: manifest ID mismatch"

    finally:
        if wav_path.is_file():
            try:
                wav_path.unlink()
            except OSError:
                pass

    return None
