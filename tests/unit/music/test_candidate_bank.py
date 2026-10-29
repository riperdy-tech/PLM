"""Unit tests for candidate bank lifecycle and querying (§4.5, §65, R6)."""
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from plm_contracts.seams.c3 import TrackSpecPayload
from plm_platform.clock import FixedClock

from services.music_factory.candidates.bank import (
    CandidateNotFoundError,
    InvalidCandidateTransitionError,
    accept_candidate,
    get_candidate_detail,
    list_candidates_by_experiment,
    list_candidates_by_track,
    mark_candidate_exported_to_p5,
    reject_candidate,
)
from services.music_factory.domain.enums import CandidateStatus
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.models import generation_attempts_table
from tests.support.music_db import music_db_session
from tests.support.seams import lineage_fields, track_spec_fields

SAMPLE_C3_SPEC = TrackSpecPayload.build(
    **track_spec_fields(
        track_spec_id='trk_bank_01',
        playlist_spec_id='ply_01',
        brand_profile_id='neon_seoul',
        hard_constraints={'genre': 'rnb', 'language': 'english', 'vocal_mode': 'vocal'},
        creative_latitude={},
        lyric_spec={},
        vocal_spec={'vocal_mode': 'vocal'},
        instrumentation_spec={},
        production_style='lofi_contemporary',
        requested_duration=120,
        candidate_target=2,
        priority=1,
        budget_limit=50.0,
        lineage=lineage_fields(experiment_id='exp_bank_01', content_arm_id='arm_01'),
        bounded_preferences={'bpm': 80,
         'key_profile': None,
         'mood': ['melancholic'],
         'use_case': ['late_night_drive']},
        provider_policy={'allowed_providers': ['mock']},
    )
).to_wire()

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("legacy_job_id", ["", "   "])
def test_candidate_detail_with_missing_job_id_cannot_assert_provider_rights(
    legacy_job_id: str,
) -> None:
    """An old rights row is not sufficient without an originating job identity."""
    spec = adapt_track_spec({
        **SAMPLE_C3_SPEC,
        "requested_duration": 12,
        "duration_range": {"min_seconds": 10, "max_seconds": 20},
    })
    plan, variants = plan_generation(spec, created_at=FIXED_NOW)
    with music_db_session() as session:
        generated = GenerationEngine(
            clock=FixedClock(FIXED_NOW), storage=InMemoryArtifactStorage(),
        ).execute_plan(session, plan=plan, variants=variants, spec=spec)
        candidate = generated.candidates[0]
        assert get_candidate_detail(session, candidate.candidate_id).rights_snapshot is not None
        session.execute(
            sa.update(generation_attempts_table)
            .where(generation_attempts_table.c.id == candidate.source_attempt_id)
            .values(provider_job_id=legacy_job_id)
        )
        detail = get_candidate_detail(session, candidate.candidate_id)
        assert detail.rights_snapshot is None
        assert detail.attempt is None


def test_candidate_bank_transitions_and_queries() -> None:
    spec = adapt_track_spec(SAMPLE_C3_SPEC)
    plan, variants = plan_generation(spec, created_at=FIXED_NOW)
    engine = GenerationEngine(clock=FixedClock(FIXED_NOW), storage=InMemoryArtifactStorage())

    with music_db_session() as session:
        res = engine.execute_plan(session, plan=plan, variants=variants, spec=spec)
        assert len(res.candidates) >= 1

        cand_id = res.candidates[0].candidate_id

        # 1. Query full detail
        detail = get_candidate_detail(session, cand_id)
        assert detail.candidate.candidate_id == cand_id
        assert len(detail.artifacts) >= 1
        assert detail.rights_snapshot is not None
        assert detail.rights_snapshot.commercial_use_status.value == "eligible"

        # 2. List queries
        by_exp = list_candidates_by_experiment(session, "exp_bank_01")
        assert len(by_exp) == len(res.candidates)

        by_trk = list_candidates_by_track(session, "trk_bank_01")
        assert len(by_trk) == len(res.candidates)

        # 3. Transitions
        accepted = accept_candidate(session, cand_id, now=FIXED_NOW)
        assert accepted.status == CandidateStatus.ACCEPTED

        exported = mark_candidate_exported_to_p5(session, cand_id, now=FIXED_NOW)
        assert exported.status == CandidateStatus.EXPORTED_TO_P5

        # 4. Illegal transition: cannot reject already exported candidate
        with pytest.raises(InvalidCandidateTransitionError):
            reject_candidate(session, cand_id, now=FIXED_NOW)

        # 5. Nonexistent candidate
        with pytest.raises(CandidateNotFoundError):
            get_candidate_detail(session, "nonexistent_cand_id")
