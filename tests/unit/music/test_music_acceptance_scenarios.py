"""Acceptance scenarios for PHASE-P4 Music Generation Factory (AC-01 .. AC-10, §111, R6)."""

from __future__ import annotations

import pytest
from plm_contracts.seams.c3 import TrackSpecPayload

from services.music_factory.api.events import build_candidate_ready_event
from services.music_factory.candidates.bank import (
    accept_candidate,
    mark_candidate_exported_to_p5,
)
from services.music_factory.domain.enums import (
    CandidateStatus,
    CommercialUseStatus,
    PlanStatus,
)
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.generation.prompts import build_generation_prompt
from services.music_factory.ingestion.p3_adapter import TrackSpecValidationError, adapt_track_spec
from services.music_factory.providers.mock_provider import synthesize_mock_wav
from services.music_factory.providers.registry import select_provider_and_model
from services.music_factory.storage.artifacts import InMemoryArtifactStorage, probe_audio_bytes
from services.music_factory.storage.repositories import get_rights_snapshot_for_attempt
from tests.support.music_db import music_db_session
from tests.support.seams import lineage_fields, track_spec_fields

SAMPLE_C3_SPEC = TrackSpecPayload.build(
    **track_spec_fields(
        track_spec_id='trk_ac_01',
        playlist_spec_id='ply_ac_01',
        brand_profile_id='neon_seoul',
        hard_constraints={'genre': 'rnb', 'language': 'english', 'vocal_mode': 'vocal'},
        creative_latitude={},
        lyric_spec={},
        vocal_spec={'vocal_mode': 'vocal'},
        instrumentation_spec={},
        production_style='lofi_contemporary',
        requested_duration=180,
        candidate_target=2,
        priority=1,
        budget_limit=60.0,
        lineage=lineage_fields(experiment_id='exp_ac_01', content_arm_id='arm_ac_01'),
        bounded_preferences={'bpm': 82,
         'key_profile': None,
         'mood': ['melancholic', 'intimate'],
         'use_case': ['late_night_drive']},
        provider_policy={'allowed_providers': ['mock']},
    )
).to_wire()


# AC-01: Ingest valid C3 TrackSpec
def test_ac01_ingest_valid_c3_spec() -> None:
    adapted = adapt_track_spec(SAMPLE_C3_SPEC)
    assert adapted.track_spec_id == "trk_ac_01"
    assert adapted.content_arm_id == "arm_ac_01"
    assert adapted.genre == "rnb"
    assert adapted.bpm == 82
    assert adapted.requested_duration == 180
    assert adapted.candidate_target == 2


# AC-02: Fail closed on malformed C3 TrackSpec
def test_ac02_fail_closed_on_malformed_spec() -> None:
    # Missing track_spec_id
    bad_spec1 = dict(SAMPLE_C3_SPEC)
    bad_spec1["track_spec_id"] = ""
    with pytest.raises(TrackSpecValidationError):
        adapt_track_spec(bad_spec1)

    # Missing content_arm_id
    bad_spec2 = dict(SAMPLE_C3_SPEC)
    bad_spec2["content_arm_id"] = ""
    with pytest.raises(TrackSpecValidationError):
        adapt_track_spec(bad_spec2)

    # Non-positive duration
    bad_spec3 = dict(SAMPLE_C3_SPEC)
    bad_spec3["requested_duration"] = 0
    with pytest.raises(TrackSpecValidationError):
        adapt_track_spec(bad_spec3)


# AC-03: Provider capability discovery and matching algorithm
def test_ac03_provider_capability_matching() -> None:
    provider_id, model_id = select_provider_and_model(
        requested_duration=120,
        genre="rnb",
        vocal_mode="vocal",
    )
    assert provider_id in ("mock", "suno", "elevenlabs")
    assert model_id != ""


# AC-04: Deterministic prompt construction with style tags and prompt hash
def test_ac04_deterministic_prompt_compilation() -> None:
    adapted = adapt_track_spec(SAMPLE_C3_SPEC)
    prompt1 = build_generation_prompt(adapted)
    prompt2 = build_generation_prompt(adapted)

    assert prompt1.prompt == prompt2.prompt
    assert prompt1.prompt_hash == prompt2.prompt_hash
    assert "RNB track" in prompt1.prompt
    assert "82 BPM" in prompt1.prompt
    assert "rnb" in prompt1.style_tags
    assert len(prompt1.prompt_hash) == 64


# AC-05: Creative variant planning
def test_ac05_creative_variant_planning() -> None:
    adapted = adapt_track_spec(SAMPLE_C3_SPEC)
    plan, variants = plan_generation(adapted)

    assert plan.candidate_target == 2
    assert len(variants) == 2
    assert variants[0].variant_code == "v1"
    assert variants[1].variant_code == "v2"
    assert variants[1].parent_variant_id == variants[0].variant_id


# AC-06: Synthesis and audio byte probing
def test_ac06_synthesis_and_audio_probe() -> None:
    wav_bytes = synthesize_mock_wav(duration_seconds=3.0, sample_rate=44100, channels=2, seed=123)
    measured = probe_audio_bytes(wav_bytes)

    assert measured.channels == 2
    assert measured.sample_rate == 44100
    assert measured.codec == "pcm_s16le"
    assert 2.9 <= measured.duration_seconds <= 3.1
    assert len(measured.sha256) == 64


# AC-07: Durable attempt logging and status transitions
def test_ac07_durable_attempt_logging_and_retry() -> None:
    raw_spec = dict(SAMPLE_C3_SPEC)
    raw_spec["hard_constraints"] = {
        "genre": "[SIMULATE_TIMEOUT]",
        "language": "english",
        "vocal_mode": "vocal",
    }
    spec = adapt_track_spec(raw_spec)
    plan, variants = plan_generation(spec)

    storage = InMemoryArtifactStorage()
    engine = GenerationEngine(storage=storage)

    with music_db_session() as session:
        result = engine.execute_plan(
            session,
            plan=plan,
            variants=variants,
            spec=spec,
            max_retries_per_variant=2,
        )

        assert result.status == PlanStatus.FAILED
        assert len(result.attempts) >= 2
        for attempt in result.attempts:
            assert "timed out" in (attempt.failure_code or "").lower()


# AC-08: Ruling R6 compliance: full rights snapshot created
def test_ac08_ruling_r6_rights_snapshot() -> None:
    spec = adapt_track_spec(SAMPLE_C3_SPEC)
    plan, variants = plan_generation(spec)
    engine = GenerationEngine()

    with music_db_session() as session:
        result = engine.execute_plan(session, plan=plan, variants=variants, spec=spec)
        assert result.status == PlanStatus.COMPLETED

        attempt_id = result.attempts[0].attempt_id
        rgt = get_rights_snapshot_for_attempt(session, attempt_id)
        assert rgt is not None
        assert rgt.commercial_use_status == CommercialUseStatus.PERMITTED
        assert rgt.plan_or_tier == "pro_tier"
        assert rgt.account_identity_ref == "plm-service-account"
        assert rgt.terms_version == "2026-v1"
        assert rgt.downloaded_at is not None


# AC-09: Candidate bank lifecycle
def test_ac09_candidate_bank_lifecycle() -> None:
    spec = adapt_track_spec(SAMPLE_C3_SPEC)
    plan, variants = plan_generation(spec)
    engine = GenerationEngine()

    with music_db_session() as session:
        result = engine.execute_plan(session, plan=plan, variants=variants, spec=spec)
        cand_id = result.candidates[0].candidate_id

        # 1. GENERATED -> ACCEPTED
        c_acc = accept_candidate(session, cand_id)
        assert c_acc.status == CandidateStatus.ACCEPTED

        # 2. ACCEPTED -> EXPORTED_TO_P5
        c_exp = mark_candidate_exported_to_p5(session, cand_id)
        assert c_exp.status == CandidateStatus.EXPORTED_TO_P5


# AC-10: Seam C4 event generation and validation
def test_ac10_seam_c4_event_generation() -> None:
    event = build_candidate_ready_event(
        candidate_id="cand_ac_01",
        artifact_id="art_ac_01",
        track_spec_id="trk_ac_01",
        experiment_id="exp_ac_01",
        content_arm_id="arm_ac_01",
        provider_id="mock",
        model_id="mock-v1",
        duration_seconds=180.0,
        sample_rate=44100,
        channels=2,
        rights_status="permitted",
        provenance_complete=True,
    )
    dumped = event.model_dump(mode="json")
    assert dumped["event_type"] == "music_candidate_ready_for_p5"
    assert dumped["event_version"] == "1"
    assert dumped["measured_audio"]["duration_seconds"] == 180.0
    assert dumped["rights_status"] == "permitted"
    assert dumped["provenance_complete"] is True
