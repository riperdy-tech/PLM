"""Tests for P4 domain contracts and enums (Task P4.1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from services.music_factory.domain.enums import (
    ArtifactType,
    AttemptStatus,
    AttemptType,
    CandidateStatus,
    CommercialUseStatus,
    CostType,
    PlanStatus,
)
from services.music_factory.domain.ids import (
    artifact_id,
    attempt_id,
    candidate_id,
    plan_id,
    rights_snapshot_id,
    variant_id,
)
from services.music_factory.domain.models import (
    AudioArtifact,
    CostRecord,
    CreativeVariant,
    GeneratedCandidate,
    GenerationAttempt,
    GenerationPlan,
    GenerationRequest,
    GenerationResponse,
    MeasuredAudio,
    ProviderCapabilities,
    RightsSnapshot,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def test_provider_capabilities_defaults_and_validation() -> None:
    caps = ProviderCapabilities(provider_id="mock", model_id="mock-v1")
    assert caps.supports_stems is True
    assert caps.supports_lyrics is True
    assert "rnb" in caps.supported_genres
    assert caps.max_duration_seconds == 300


def test_generation_plan_and_attempt_lifecycle() -> None:
    pid = plan_id("trk_01", "exp_01", "p4-v1")
    assert pid.startswith("gen_plan_")

    plan = GenerationPlan(
        plan_id=pid,
        track_spec_id="trk_01",
        experiment_id="exp_01",
        candidate_target=3,
        budget_limit=60.0,
        status=PlanStatus.PLANNED,
        created_at=NOW,
    )
    assert plan.status == PlanStatus.PLANNED
    assert plan.candidate_target == 3

    aid = attempt_id(pid, "var_a", 0)
    assert aid.startswith("gen_att_")
    attempt = GenerationAttempt(
        attempt_id=aid,
        generation_plan_id=pid,
        creative_variant_id="var_a",
        provider_id="mock",
        model_id="mock-v1",
        provider_job_id="job_001",
        attempt_type=AttemptType.INITIAL,
        status=AttemptStatus.SUBMITTED,
        request_hash="req_h_01",
        request_payload_hash="payload_h_01",
        started_at=NOW,
        submitted_at=NOW,
    )
    assert attempt.status == AttemptStatus.SUBMITTED


def test_candidate_and_audio_artifact() -> None:
    cid = candidate_id("plan_01", "att_01", 0)
    assert cid.startswith("cand_")

    cand = GeneratedCandidate(
        candidate_id=cid,
        track_spec_id="trk_01",
        experiment_id="exp_01",
        generation_plan_id="plan_01",
        status=CandidateStatus.GENERATED,
        provider_id="mock",
        model_id="mock-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    assert cand.status == CandidateStatus.GENERATED

    art_id = artifact_id(cid, ArtifactType.FULL_MIX.value, "sha256_mock_hash_12345")
    assert art_id.startswith("art_")

    artifact = AudioArtifact(
        artifact_id=art_id,
        candidate_id=cid,
        artifact_type=ArtifactType.FULL_MIX,
        object_uri="s3://plm-media/music/trk_01/full_mix.wav",
        sha256="sha256_mock_hash_12345",
        byte_size=1024 * 1024 * 15,
        duration_seconds=185.5,
        sample_rate=44100,
        channels=2,
        created_at=NOW,
    )
    assert artifact.channels == 2
    assert artifact.duration_seconds == 185.5


def test_rights_snapshot_r6_compliance() -> None:
    snap_id = rights_snapshot_id("att_01", "mock")
    assert snap_id.startswith("rgt_")

    snapshot = RightsSnapshot(
        snapshot_id=snap_id,
        attempt_id="att_01",
        provider_id="mock",
        commercial_use_status=CommercialUseStatus.PERMITTED,
        basis="paid_commercial_tier",
        license_reference="lic_ref_01",
        policy_version="p4-lic-v1",
        plan_or_tier="enterprise_commercial",
        account_identity_ref="acct_plm_prod",
        terms_version="terms_2026_01",
        created_at=NOW,
        downloaded_at=NOW,
    )
    assert snapshot.commercial_use_status == CommercialUseStatus.PERMITTED
    assert snapshot.plan_or_tier == "enterprise_commercial"
    assert snapshot.terms_version == "terms_2026_01"


def test_deterministic_ids_are_repeatable() -> None:
    id1 = plan_id("trk_100", "exp_200", "v1")
    id2 = plan_id("trk_100", "exp_200", "v1")
    assert id1 == id2

    id3 = plan_id("trk_100", "exp_200", "v2")
    assert id1 != id3
