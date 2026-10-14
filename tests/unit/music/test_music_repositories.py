"""Tests for P4 repositories on SQLite harness (Task P4.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from services.music_factory.domain.enums import (
    ArtifactType,
    AttemptStatus,
    AttemptType,
    CandidateStatus,
    CommercialUseStatus,
    CostType,
    PlanStatus,
)
from services.music_factory.domain.models import (
    AudioArtifact,
    CostRecord,
    CreativeVariant,
    GeneratedCandidate,
    GenerationAttempt,
    GenerationPlan,
    RightsSnapshot,
)
from services.music_factory.storage import repositories
from tests.support.music_db import create_music_engine, music_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def test_generation_plan_and_variant_crud() -> None:
    engine = create_music_engine()
    with music_session(engine) as session:
        plan = GenerationPlan(
            plan_id="plan_001",
            track_spec_id="trk_001",
            experiment_id="exp_001",
            lineage=sample_lineage(experiment_id="exp_001"),
            track_spec_wire=None,
            candidate_target=2,
            max_attempts=4,
            budget_limit=40.0,
            credit_limit=80.0,
            policy_version="p4-v1",
            status=PlanStatus.PLANNED,
            created_at=NOW,
        )
        repositories.insert_generation_plan(session, plan)

        variant = CreativeVariant(
            variant_id="var_001",
            generation_plan_id="plan_001",
            variant_code="v1",
            changed_dimensions={"bpm": 85},
            change_reason="initial_variant",
            prompt_delta={"style": "smoother_rnb"},
            created_at=NOW,
        )
        repositories.insert_creative_variant(session, variant)
        session.commit()

        loaded_plan = repositories.get_generation_plan(session, "plan_001")
        assert loaded_plan is not None
        assert loaded_plan.plan_id == "plan_001"
        assert loaded_plan.status == PlanStatus.PLANNED

        variants = repositories.list_creative_variants(session, "plan_001")
        assert len(variants) == 1
        assert variants[0].variant_code == "v1"
        assert variants[0].changed_dimensions == {"bpm": 85}

        # Update status
        repositories.update_plan_status(session, "plan_001", PlanStatus.RUNNING, started_at=NOW)
        session.commit()
        updated_plan = repositories.get_generation_plan(session, "plan_001")
        assert updated_plan is not None
        assert updated_plan.status == PlanStatus.RUNNING


def test_attempt_and_candidate_and_artifact_crud() -> None:
    engine = create_music_engine()
    with music_session(engine) as session:
        # 1. Attempt
        attempt = GenerationAttempt(
            attempt_id="att_001",
            generation_plan_id="plan_001",
            creative_variant_id="var_001",
            provider_id="mock",
            model_id="mock-v1",
            provider_job_id="job_001",
            attempt_type=AttemptType.INITIAL,
            status=AttemptStatus.SUCCEEDED,
            request_hash="req_h_001",
            request_payload_hash="payload_h_001",
            started_at=NOW,
            submitted_at=NOW,
            completed_at=NOW,
            cost_estimate=1.0,
            actual_cost=1.0,
        )
        repositories.insert_generation_attempt(session, attempt)

        # 2. Candidate
        cand = GeneratedCandidate(
            source_attempt_id=None,
            candidate_id="cand_001",
            track_spec_id="trk_001",
            experiment_id="exp_001",
            generation_plan_id="plan_001",
            status=CandidateStatus.GENERATED,
            provider_id="mock",
            model_id="mock-v1",
            created_at=NOW,
            updated_at=NOW,
        )
        repositories.insert_candidate(session, cand)

        # 3. Audio Artifact
        artifact = AudioArtifact(
            artifact_id="art_001",
            candidate_id="cand_001",
            artifact_type=ArtifactType.FULL_MIX,
            object_uri="s3://plm-media/music/cand_001/mix.wav",
            sha256="hash_001_abc",
            mime_type="audio/wav",
            byte_size=1024 * 500,
            duration_seconds=180.0,
            sample_rate=44100,
            channels=2,
            created_at=NOW,
        )
        repositories.insert_audio_artifact(session, artifact)

        # 4. Rights snapshot
        snap = RightsSnapshot(
            snapshot_id="rgt_001",
            attempt_id="att_001",
            provider_id="mock",
            commercial_use_status=CommercialUseStatus.ELIGIBLE,
            basis="enterprise_license",
            license_reference="lic_ref_123",
            policy_version="p4-lic-v1",
            checked_at=NOW,
            plan_or_tier="enterprise_tier",
            account_identity_ref="acct_123",
            terms_version="2026_01",
            created_at=NOW,
            downloaded_at=NOW,
        )
        repositories.insert_rights_snapshot(session, snap)

        # 5. Cost record
        cost = CostRecord(
            cost_record_id="cst_001",
            attempt_id="att_001",
            cost_type=CostType.API_CREDIT,
            estimated_amount=1.5,
            actual_amount=1.5,
            currency="USD",
            credits_estimated=10.0,
            credits_actual=10.0,
            pricing_version="price-v1",
            created_at=NOW,
        )
        repositories.insert_cost_record(session, cost)
        session.commit()

        # Verifications
        loaded_attempt = repositories.get_generation_attempt(session, "att_001")
        assert loaded_attempt is not None
        assert loaded_attempt.status == AttemptStatus.SUCCEEDED

        loaded_cand = repositories.get_candidate(session, "cand_001")
        assert loaded_cand is not None
        assert loaded_cand.status == CandidateStatus.GENERATED

        cand_list = repositories.list_candidates_by_track(session, "trk_001")
        assert len(cand_list) == 1

        loaded_art = repositories.get_audio_artifact(session, "art_001")
        assert loaded_art is not None
        assert loaded_art.sha256 == "hash_001_abc"

        artifacts = repositories.list_artifacts_for_candidate(session, "cand_001")
        assert len(artifacts) == 1

        loaded_snap = repositories.get_rights_snapshot_for_attempt(session, "att_001")
        assert loaded_snap is not None
        assert loaded_snap.plan_or_tier == "enterprise_tier"
        assert loaded_snap.terms_version == "2026_01"

        # Update candidate status
        repositories.update_candidate_status(session, "cand_001", CandidateStatus.ACCEPTED)
        session.commit()
        updated_cand = repositories.get_candidate(session, "cand_001")
        assert updated_cand is not None
        assert updated_cand.status == CandidateStatus.ACCEPTED
