"""Unit tests for durable generation engine (§4.4, §65, §111, R6)."""
from datetime import UTC, datetime

from plm_contracts.seams.c3 import TrackSpecPayload
from plm_platform.clock import FixedClock

from services.music_factory.domain.enums import (
    AttemptStatus,
    CandidateStatus,
    CommercialUseStatus,
    PlanStatus,
)
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.repositories import (
    get_candidate,
    get_generation_plan,
    get_rights_snapshot_for_attempt,
    list_artifacts_for_candidate,
    list_generation_attempts,
)
from tests.support.music_db import music_db_session
from tests.support.seams import lineage_fields, track_spec_fields

SAMPLE_C3_SPEC = TrackSpecPayload.build(
    **track_spec_fields(
        track_spec_id='trk_eng_01',
        playlist_spec_id='ply_01',
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
        budget_limit=50.0,
        lineage=lineage_fields(experiment_id='exp_eng_01', content_arm_id='arm_eng_01'),
        bounded_preferences={'bpm': 84,
         'key_profile': None,
         'mood': ['melancholic', 'intimate'],
         'use_case': ['late_night_drive']},
        provider_policy={'allowed_providers': ['mock']},
    )
).to_wire()

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_generation_engine_successful_execution() -> None:
    spec = adapt_track_spec(dict(SAMPLE_C3_SPEC))
    plan, variants = plan_generation(spec, created_at=FIXED_NOW)

    storage = InMemoryArtifactStorage()
    engine = GenerationEngine(clock=FixedClock(FIXED_NOW), storage=storage)

    with music_db_session() as session:
        result = engine.execute_plan(
            session,
            plan=plan,
            variants=variants,
            spec=spec,
        )

        assert result.status == PlanStatus.COMPLETED
        assert len(result.candidates) == 2
        assert len(result.attempts) == 2
        assert len(result.artifacts) == 2
        assert len(result.rights_snapshots) == 2

        # Verify DB persistence
        stored_plan = get_generation_plan(session, plan.plan_id)
        assert stored_plan is not None
        assert stored_plan.status == PlanStatus.COMPLETED

        cand = get_candidate(session, result.candidates[0].candidate_id)
        assert cand is not None
        assert cand.status == CandidateStatus.GENERATED

        attempts = list_generation_attempts(session, plan.plan_id)
        assert len(attempts) == 2
        assert attempts[0].status == AttemptStatus.SUCCEEDED

        arts = list_artifacts_for_candidate(session, cand.candidate_id)
        assert len(arts) == 1
        assert arts[0].byte_size > 0
        assert arts[0].sample_rate == 44100
        assert arts[0].channels == 2
        assert arts[0].duration_seconds > 0

        # Verify Ruling R6 Rights Snapshot invariants
        rgt = get_rights_snapshot_for_attempt(session, attempts[0].attempt_id)
        assert rgt is not None
        assert rgt.commercial_use_status == CommercialUseStatus.ELIGIBLE
        assert rgt.plan_or_tier == "internal"  # declared by the mock provider (ADR-0027)
        assert rgt.account_identity_ref == "plm-mock"
        assert rgt.terms_version == "mock-v1"
        assert rgt.downloaded_at is not None


def test_generation_engine_retry_on_failure() -> None:
    raw_spec = dict(SAMPLE_C3_SPEC)
    raw_spec["hard_constraints"] = {
        "genre": "[SIMULATE_TIMEOUT]",
        "language": "english",
        "vocal_mode": "vocal",
    }
    spec = adapt_track_spec(raw_spec)
    plan, variants = plan_generation(spec, created_at=FIXED_NOW)

    storage = InMemoryArtifactStorage()
    engine = GenerationEngine(clock=FixedClock(FIXED_NOW), storage=storage)

    with music_db_session() as session:
        result = engine.execute_plan(
            session,
            plan=plan,
            variants=variants,
            spec=spec,
            max_retries_per_variant=2,
        )

        assert result.status == PlanStatus.FAILED
        assert len(result.candidates) == 0
        assert len(result.attempts) >= 2
        for attempt in result.attempts:
            assert attempt.status == AttemptStatus.FAILED
            assert "timed out" in (attempt.failure_code or "").lower()
