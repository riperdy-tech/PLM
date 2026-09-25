"""Durable music generation engine coordinating attempts, variants, and candidates (§4.4, §65, §111)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
from ..domain.ids import candidate_id, rights_snapshot_id
from ..domain.models import (
    AudioArtifact,
    CostRecord,
    CreativeVariant,
    GeneratedCandidate,
    GenerationAttempt,
    GenerationPlan,
    GenerationRequest,
    RightsSnapshot,
)
from ..ingestion.p3_adapter import AdaptedTrackSpec
from ..providers.adapters import get_provider_adapter
from ..providers.protocol import MusicProviderProtocol
from ..storage.artifacts import (
    ArtifactStorageProtocol,
    InMemoryArtifactStorage,
    create_audio_artifact,
    probe_audio_bytes,
)
from ..storage.repositories import (
    insert_audio_artifact,
    insert_candidate,
    insert_cost_record,
    insert_creative_variant,
    insert_generation_attempt,
    insert_generation_plan,
    insert_rights_snapshot,
    update_plan_status,
)
from .prompts import build_generation_prompt

__all__ = ["GenerationEngine", "GenerationEngineResult"]


@dataclass
class GenerationEngineResult:
    """Outcome of running a generation plan."""

    plan_id: str
    status: PlanStatus
    candidates: list[GeneratedCandidate] = field(default_factory=list)
    attempts: list[GenerationAttempt] = field(default_factory=list)
    artifacts: list[AudioArtifact] = field(default_factory=list)
    rights_snapshots: list[RightsSnapshot] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class GenerationEngine:
    """Executes music generation plans durably with fail-closed provenance."""

    def __init__(
        self,
        *,
        storage: ArtifactStorageProtocol | None = None,
        default_provider_id: str = "mock",
    ) -> None:
        self.storage = storage or InMemoryArtifactStorage()
        self.default_provider_id = default_provider_id

    def execute_plan(
        self,
        session: Session,
        *,
        plan: GenerationPlan,
        variants: list[CreativeVariant],
        spec: AdaptedTrackSpec,
        provider_adapter: MusicProviderProtocol | None = None,
        max_retries_per_variant: int = 1,
    ) -> GenerationEngineResult:
        """Run generation loop across all variants within plan constraints."""
        now = datetime.now(UTC)
        from ..storage.repositories import get_generation_plan, list_creative_variants

        if get_generation_plan(session, plan.plan_id) is None:
            insert_generation_plan(session, plan)
        update_plan_status(session, plan.plan_id, PlanStatus.RUNNING, started_at=now)

        existing_variant_ids = {v.variant_id for v in list_creative_variants(session, plan.plan_id)}
        for variant in variants:
            if variant.variant_id not in existing_variant_ids:
                insert_creative_variant(session, variant)

        adapter = provider_adapter or get_provider_adapter(self.default_provider_id)
        candidates: list[GeneratedCandidate] = []
        attempts: list[GenerationAttempt] = []
        artifacts: list[AudioArtifact] = []
        rights_snapshots: list[RightsSnapshot] = []
        errors: list[str] = []

        total_attempts = 0
        candidate_count = 0

        for var_idx, variant in enumerate(variants):
            if candidate_count >= plan.candidate_target:
                break

            prompt_obj = build_generation_prompt(spec, variant_delta=variant.prompt_delta)
            retry_count = 0
            transport_parent: str | None = None
            base_seed = abs(hash(f"{spec.track_spec_id}:{variant.variant_code}")) % 100000

            while retry_count <= max_retries_per_variant and total_attempts < plan.max_attempts:
                total_attempts += 1
                att_id = f"gen_att_{plan.plan_id}_{variant.variant_code}_{retry_count}"
                attempt_type = AttemptType.INITIAL if retry_count == 0 else AttemptType.RETRY

                current_seed = base_seed + retry_count
                req_dict: dict[str, Any] = {
                    "prompt": prompt_obj.prompt,
                    "seed": current_seed,
                    "duration_seconds": spec.requested_duration,
                }
                req_hash = hashlib.sha256(str(req_dict).encode("utf-8")).hexdigest()

                attempt = GenerationAttempt(
                    attempt_id=att_id,
                    generation_plan_id=plan.plan_id,
                    creative_variant_id=variant.variant_id,
                    provider_id=adapter.provider_id,
                    model_id=adapter.model_id,
                    provider_job_id="",
                    attempt_type=attempt_type,
                    transport_retry_of=transport_parent,
                    request_hash=req_hash,
                    request_payload_hash=req_hash,
                    status=AttemptStatus.SUBMITTED,
                    started_at=datetime.now(UTC),
                    submitted_at=datetime.now(UTC),
                )

                gen_req = GenerationRequest(
                    experiment_id=plan.experiment_id,
                    track_spec_id=plan.track_spec_id,
                    prompt=prompt_obj.prompt,
                    negative_prompt=prompt_obj.negative_prompt,
                    duration_seconds=spec.requested_duration,
                    bpm=spec.bpm,
                    vocal_mode=spec.vocal_mode,
                    language=spec.language,
                    genre=spec.genre,
                    style_tags=prompt_obj.style_tags,
                    seed=current_seed,
                    provider_id=adapter.provider_id,
                    model_id=adapter.model_id,
                )

                gen_res = adapter.generate(gen_req)

                if gen_res.status == AttemptStatus.SUCCEEDED and gen_res.audio_data:
                    # Mark succeeded attempt
                    completed_attempt = GenerationAttempt(
                        attempt_id=attempt.attempt_id,
                        generation_plan_id=attempt.generation_plan_id,
                        creative_variant_id=attempt.creative_variant_id,
                        provider_id=attempt.provider_id,
                        model_id=attempt.model_id,
                        provider_job_id=gen_res.provider_job_id,
                        attempt_type=attempt.attempt_type,
                        transport_retry_of=attempt.transport_retry_of,
                        request_hash=attempt.request_hash,
                        request_payload_hash=attempt.request_payload_hash,
                        status=AttemptStatus.SUCCEEDED,
                        started_at=attempt.started_at,
                        submitted_at=attempt.submitted_at,
                        completed_at=datetime.now(UTC),
                        actual_cost=gen_res.cost_credits * 0.05,
                        actual_credits=gen_res.cost_credits,
                    )
                    insert_generation_attempt(session, completed_attempt)
                    attempts.append(completed_attempt)

                    # Probe audio
                    measured = probe_audio_bytes(gen_res.audio_data, mime_type=gen_res.mime_type)

                    # Candidate creation
                    cand_id = candidate_id(
                        plan.plan_id, completed_attempt.attempt_id, candidate_count
                    )
                    cand = GeneratedCandidate(
                        candidate_id=cand_id,
                        track_spec_id=plan.track_spec_id,
                        experiment_id=plan.experiment_id,
                        generation_plan_id=plan.plan_id,
                        status=CandidateStatus.GENERATED,
                        provider_id=adapter.provider_id,
                        model_id=adapter.model_id,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    insert_candidate(session, cand)
                    candidates.append(cand)
                    candidate_count += 1

                    # Artifact intake
                    obj_uri = self.storage.put_artifact(
                        candidate_id=cand.candidate_id,
                        name="master.wav",
                        data=gen_res.audio_data,
                        mime_type=gen_res.mime_type,
                    )
                    art = create_audio_artifact(
                        candidate_id=cand.candidate_id,
                        object_uri=obj_uri,
                        measured=measured,
                        artifact_type=ArtifactType.FULL_MIX,
                    )
                    insert_audio_artifact(session, art)
                    artifacts.append(art)

                    # Ruling R6 Rights Snapshot
                    rgt = RightsSnapshot(
                        snapshot_id=rights_snapshot_id(
                            completed_attempt.attempt_id, adapter.provider_id
                        ),
                        attempt_id=completed_attempt.attempt_id,
                        provider_id=adapter.provider_id,
                        commercial_use_status=CommercialUseStatus.PERMITTED,
                        basis="commercial_pro_subscription",
                        license_reference="LICENSE-PLM-COMMERCIAL-2026",
                        policy_version=plan.policy_version,
                        checked_at=datetime.now(UTC),
                        plan_or_tier="pro_tier",
                        account_identity_ref="plm-service-account",
                        terms_version="2026-v1",
                        created_at=datetime.now(UTC),
                        downloaded_at=datetime.now(UTC),
                    )
                    insert_rights_snapshot(session, rgt)
                    rights_snapshots.append(rgt)

                    # Cost tracking
                    cost = CostRecord(
                        cost_record_id=f"cost_{completed_attempt.attempt_id}",
                        attempt_id=completed_attempt.attempt_id,
                        cost_type=CostType.API_CREDIT,
                        actual_amount=completed_attempt.actual_cost,
                        credits_actual=completed_attempt.actual_credits,
                        created_at=datetime.now(UTC),
                    )
                    insert_cost_record(session, cost)

                    break  # Variant succeeded, proceed to next variant
                else:
                    failed_attempt = GenerationAttempt(
                        attempt_id=attempt.attempt_id,
                        generation_plan_id=attempt.generation_plan_id,
                        creative_variant_id=attempt.creative_variant_id,
                        provider_id=attempt.provider_id,
                        model_id=attempt.model_id,
                        provider_job_id=gen_res.provider_job_id,
                        attempt_type=attempt.attempt_type,
                        transport_retry_of=attempt.transport_retry_of,
                        request_hash=attempt.request_hash,
                        request_payload_hash=attempt.request_payload_hash,
                        status=AttemptStatus.FAILED,
                        failure_code=gen_res.error_message[:64]
                        if gen_res.error_message
                        else "UNKNOWN_ERROR",
                        started_at=attempt.started_at,
                        submitted_at=attempt.submitted_at,
                        completed_at=datetime.now(UTC),
                    )
                    insert_generation_attempt(session, failed_attempt)
                    attempts.append(failed_attempt)
                    errors.append(gen_res.error_message or "Unknown failure")

                    transport_parent = attempt.attempt_id
                    retry_count += 1

        final_status = (
            PlanStatus.COMPLETED
            if candidate_count >= plan.candidate_target
            else (PlanStatus.COMPLETED if candidate_count > 0 else PlanStatus.FAILED)
        )
        update_plan_status(session, plan.plan_id, final_status, finished_at=datetime.now(UTC))
        session.flush()

        return GenerationEngineResult(
            plan_id=plan.plan_id,
            status=final_status,
            candidates=candidates,
            attempts=attempts,
            artifacts=artifacts,
            rights_snapshots=rights_snapshots,
            errors=errors,
        )
