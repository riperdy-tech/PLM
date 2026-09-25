"""Repository access functions for the `music` schema (PHASE-P4 §65, R6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..domain.enums import (
    ArtifactType,
    AttemptStatus,
    AttemptType,
    CandidateStatus,
    CommercialUseStatus,
    CostType,
    PlanStatus,
)
from ..domain.models import (
    AudioArtifact,
    CostRecord,
    CreativeVariant,
    GeneratedCandidate,
    GenerationAttempt,
    GenerationPlan,
    RightsSnapshot,
)
from .models import (
    audio_artifacts_table,
    creative_variants_table,
    generated_candidates_table,
    generation_attempts_table,
    generation_plans_table,
    provider_cost_records_table,
    rights_snapshots_table,
)

__all__ = [
    "get_audio_artifact",
    "get_candidate",
    "get_generation_attempt",
    "get_generation_plan",
    "get_rights_snapshot_for_attempt",
    "insert_audio_artifact",
    "insert_candidate",
    "insert_cost_record",
    "insert_creative_variant",
    "insert_generation_attempt",
    "insert_generation_plan",
    "insert_rights_snapshot",
    "list_artifacts_for_candidate",
    "list_candidates_by_track",
    "list_creative_variants",
    "list_generation_attempts",
    "update_candidate_status",
    "update_plan_status",
]


def insert_generation_plan(session: Session, plan: GenerationPlan) -> None:
    session.execute(
        sa.insert(generation_plans_table).values(
            id=plan.plan_id,
            track_spec_id=plan.track_spec_id,
            experiment_id=plan.experiment_id,
            candidate_target=plan.candidate_target,
            max_attempts=plan.max_attempts,
            budget_limit=plan.budget_limit,
            credit_limit=plan.credit_limit,
            policy_version=plan.policy_version,
            status=plan.status.value,
            created_at=plan.created_at,
            started_at=plan.started_at,
            finished_at=plan.finished_at,
        )
    )


def get_generation_plan(session: Session, plan_id: str) -> GenerationPlan | None:
    row = session.execute(
        sa.select(generation_plans_table).where(generation_plans_table.c.id == plan_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    return GenerationPlan(
        plan_id=row["id"],
        track_spec_id=row["track_spec_id"],
        experiment_id=row["experiment_id"],
        candidate_target=row["candidate_target"],
        max_attempts=row["max_attempts"],
        budget_limit=row["budget_limit"],
        credit_limit=row["credit_limit"],
        policy_version=row["policy_version"],
        status=PlanStatus(row["status"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def update_plan_status(
    session: Session,
    plan_id: str,
    status: PlanStatus,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {"status": status.value}
    if started_at is not None:
        values["started_at"] = started_at
    if finished_at is not None:
        values["finished_at"] = finished_at
    session.execute(
        sa.update(generation_plans_table)
        .where(generation_plans_table.c.id == plan_id)
        .values(**values)
    )


def insert_creative_variant(session: Session, variant: CreativeVariant) -> None:
    session.execute(
        sa.insert(creative_variants_table).values(
            id=variant.variant_id,
            generation_plan_id=variant.generation_plan_id,
            variant_code=variant.variant_code,
            parent_variant_id=variant.parent_variant_id,
            changed_dimensions_json=json.dumps(variant.changed_dimensions, sort_keys=True),
            change_reason=variant.change_reason,
            prompt_delta_json=json.dumps(variant.prompt_delta, sort_keys=True),
            created_at=variant.created_at,
        )
    )


def list_creative_variants(session: Session, plan_id: str) -> list[CreativeVariant]:
    rows = session.execute(
        sa.select(creative_variants_table)
        .where(creative_variants_table.c.generation_plan_id == plan_id)
        .order_by(creative_variants_table.c.created_at)
    ).mappings().all()
    return [
        CreativeVariant(
            variant_id=r["id"],
            generation_plan_id=r["generation_plan_id"],
            variant_code=r["variant_code"],
            parent_variant_id=r["parent_variant_id"],
            changed_dimensions=json.loads(r["changed_dimensions_json"]),
            change_reason=r["change_reason"],
            prompt_delta=json.loads(r["prompt_delta_json"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


def insert_generation_attempt(session: Session, attempt: GenerationAttempt) -> None:
    session.execute(
        sa.insert(generation_attempts_table).values(
            id=attempt.attempt_id,
            generation_plan_id=attempt.generation_plan_id,
            creative_variant_id=attempt.creative_variant_id,
            provider_id=attempt.provider_id,
            model_id=attempt.model_id,
            provider_job_id=attempt.provider_job_id,
            attempt_type=attempt.attempt_type.value,
            transport_retry_of=attempt.transport_retry_of,
            request_hash=attempt.request_hash,
            request_payload_hash=attempt.request_payload_hash,
            status=attempt.status.value,
            failure_code=attempt.failure_code,
            started_at=attempt.started_at,
            submitted_at=attempt.submitted_at,
            completed_at=attempt.completed_at,
            cost_estimate=attempt.cost_estimate,
            actual_cost=attempt.actual_cost,
            credit_estimate=attempt.credit_estimate,
            actual_credits=attempt.actual_credits,
        )
    )


def get_generation_attempt(session: Session, attempt_id: str) -> GenerationAttempt | None:
    row = session.execute(
        sa.select(generation_attempts_table).where(generation_attempts_table.c.id == attempt_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    return GenerationAttempt(
        attempt_id=row["id"],
        generation_plan_id=row["generation_plan_id"],
        creative_variant_id=row["creative_variant_id"],
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        provider_job_id=row["provider_job_id"],
        attempt_type=AttemptType(row["attempt_type"]),
        transport_retry_of=row["transport_retry_of"],
        request_hash=row["request_hash"],
        request_payload_hash=row["request_payload_hash"],
        status=AttemptStatus(row["status"]),
        failure_code=row["failure_code"],
        started_at=row["started_at"],
        submitted_at=row["submitted_at"],
        completed_at=row["completed_at"],
        cost_estimate=row["cost_estimate"],
        actual_cost=row["actual_cost"],
        credit_estimate=row["credit_estimate"],
        actual_credits=row["actual_credits"],
    )


def list_generation_attempts(session: Session, plan_id: str) -> list[GenerationAttempt]:
    rows = session.execute(
        sa.select(generation_attempts_table)
        .where(generation_attempts_table.c.generation_plan_id == plan_id)
        .order_by(generation_attempts_table.c.started_at)
    ).mappings().all()
    return [
        GenerationAttempt(
            attempt_id=r["id"],
            generation_plan_id=r["generation_plan_id"],
            creative_variant_id=r["creative_variant_id"],
            provider_id=r["provider_id"],
            model_id=r["model_id"],
            provider_job_id=r["provider_job_id"],
            attempt_type=AttemptType(r["attempt_type"]),
            transport_retry_of=r["transport_retry_of"],
            request_hash=r["request_hash"],
            request_payload_hash=r["request_payload_hash"],
            status=AttemptStatus(r["status"]),
            failure_code=r["failure_code"],
            started_at=r["started_at"],
            submitted_at=r["submitted_at"],
            completed_at=r["completed_at"],
            cost_estimate=r["cost_estimate"],
            actual_cost=r["actual_cost"],
            credit_estimate=r["credit_estimate"],
            actual_credits=r["actual_credits"],
        )
        for r in rows
    ]


def insert_candidate(session: Session, candidate: GeneratedCandidate) -> None:
    session.execute(
        sa.insert(generated_candidates_table).values(
            id=candidate.candidate_id,
            track_spec_id=candidate.track_spec_id,
            experiment_id=candidate.experiment_id,
            generation_plan_id=candidate.generation_plan_id,
            parent_candidate_id=candidate.parent_candidate_id,
            status=candidate.status.value,
            provider_id=candidate.provider_id,
            model_id=candidate.model_id,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
        )
    )


def get_candidate(session: Session, candidate_id: str) -> GeneratedCandidate | None:
    row = session.execute(
        sa.select(generated_candidates_table).where(
            generated_candidates_table.c.id == candidate_id
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    return GeneratedCandidate(
        candidate_id=row["id"],
        track_spec_id=row["track_spec_id"],
        experiment_id=row["experiment_id"],
        generation_plan_id=row["generation_plan_id"],
        parent_candidate_id=row["parent_candidate_id"],
        status=CandidateStatus(row["status"]),
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_candidates_by_track(session: Session, track_spec_id: str) -> list[GeneratedCandidate]:
    rows = session.execute(
        sa.select(generated_candidates_table)
        .where(generated_candidates_table.c.track_spec_id == track_spec_id)
        .order_by(generated_candidates_table.c.created_at)
    ).mappings().all()
    return [
        GeneratedCandidate(
            candidate_id=r["id"],
            track_spec_id=r["track_spec_id"],
            experiment_id=r["experiment_id"],
            generation_plan_id=r["generation_plan_id"],
            parent_candidate_id=r["parent_candidate_id"],
            status=CandidateStatus(r["status"]),
            provider_id=r["provider_id"],
            model_id=r["model_id"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


def update_candidate_status(
    session: Session, candidate_id: str, status: CandidateStatus
) -> None:
    now = datetime.now(UTC)
    session.execute(
        sa.update(generated_candidates_table)
        .where(generated_candidates_table.c.id == candidate_id)
        .values(status=status.value, updated_at=now)
    )


def insert_audio_artifact(session: Session, artifact: AudioArtifact) -> None:
    session.execute(
        sa.insert(audio_artifacts_table).values(
            id=artifact.artifact_id,
            candidate_id=artifact.candidate_id,
            artifact_type=artifact.artifact_type.value,
            object_uri=artifact.object_uri,
            sha256=artifact.sha256,
            mime_type=artifact.mime_type,
            byte_size=artifact.byte_size,
            duration_seconds=artifact.duration_seconds,
            sample_rate=artifact.sample_rate,
            channels=artifact.channels,
            codec=artifact.codec,
            probe_version=artifact.probe_version,
            created_at=artifact.created_at,
        )
    )


def get_audio_artifact(session: Session, artifact_id: str) -> AudioArtifact | None:
    row = session.execute(
        sa.select(audio_artifacts_table).where(audio_artifacts_table.c.id == artifact_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    return AudioArtifact(
        artifact_id=row["id"],
        candidate_id=row["candidate_id"],
        artifact_type=ArtifactType(row["artifact_type"]),
        object_uri=row["object_uri"],
        sha256=row["sha256"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        duration_seconds=row["duration_seconds"],
        sample_rate=row["sample_rate"],
        channels=row["channels"],
        codec=row["codec"],
        probe_version=row["probe_version"],
        created_at=row["created_at"],
    )


def list_artifacts_for_candidate(session: Session, candidate_id: str) -> list[AudioArtifact]:
    rows = session.execute(
        sa.select(audio_artifacts_table)
        .where(audio_artifacts_table.c.candidate_id == candidate_id)
        .order_by(audio_artifacts_table.c.created_at)
    ).mappings().all()
    return [
        AudioArtifact(
            artifact_id=r["id"],
            candidate_id=r["candidate_id"],
            artifact_type=ArtifactType(r["artifact_type"]),
            object_uri=r["object_uri"],
            sha256=r["sha256"],
            mime_type=r["mime_type"],
            byte_size=r["byte_size"],
            duration_seconds=r["duration_seconds"],
            sample_rate=r["sample_rate"],
            channels=r["channels"],
            codec=r["codec"],
            probe_version=r["probe_version"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def insert_rights_snapshot(session: Session, snapshot: RightsSnapshot) -> None:
    session.execute(
        sa.insert(rights_snapshots_table).values(
            id=snapshot.snapshot_id,
            attempt_id=snapshot.attempt_id,
            provider_id=snapshot.provider_id,
            commercial_use_status=snapshot.commercial_use_status.value,
            basis=snapshot.basis,
            license_reference=snapshot.license_reference,
            policy_version=snapshot.policy_version,
            checked_at=snapshot.checked_at,
            plan_or_tier=snapshot.plan_or_tier,
            account_identity_ref=snapshot.account_identity_ref,
            terms_version=snapshot.terms_version,
            created_at=snapshot.created_at,
            downloaded_at=snapshot.downloaded_at,
        )
    )


def get_rights_snapshot_for_attempt(session: Session, attempt_id: str) -> RightsSnapshot | None:
    row = session.execute(
        sa.select(rights_snapshots_table).where(rights_snapshots_table.c.attempt_id == attempt_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    return RightsSnapshot(
        snapshot_id=row["id"],
        attempt_id=row["attempt_id"],
        provider_id=row["provider_id"],
        commercial_use_status=CommercialUseStatus(row["commercial_use_status"]),
        basis=row["basis"],
        license_reference=row["license_reference"],
        policy_version=row["policy_version"],
        checked_at=row["checked_at"],
        plan_or_tier=row["plan_or_tier"],
        account_identity_ref=row["account_identity_ref"],
        terms_version=row["terms_version"],
        created_at=row["created_at"],
        downloaded_at=row["downloaded_at"],
    )


def insert_cost_record(session: Session, cost: CostRecord) -> None:
    session.execute(
        sa.insert(provider_cost_records_table).values(
            id=cost.cost_record_id,
            attempt_id=cost.attempt_id,
            cost_type=cost.cost_type.value,
            estimated_amount=cost.estimated_amount,
            actual_amount=cost.actual_amount,
            currency=cost.currency,
            credits_estimated=cost.credits_estimated,
            credits_actual=cost.credits_actual,
            pricing_version=cost.pricing_version,
            created_at=cost.created_at,
        )
    )
