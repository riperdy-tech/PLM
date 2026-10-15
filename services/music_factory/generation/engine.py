"""Durable music generation engine coordinating attempts, variants, and candidates (§4.4, §65, §111)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from plm_platform.clock import Clock, stable_seed
from plm_platform.exceptions import ExceptionQueue
from sqlalchemy.orm import Session

from ..domain.enums import (
    ArtifactType,
    AttemptStatus,
    AttemptType,
    CandidateStatus,
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
from .prompts import ProtectedArtistPromptRefused, build_generation_prompt

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
        clock: Clock,
        storage: ArtifactStorageProtocol,
        default_provider_id: str = "mock",
    ) -> None:
        # ``storage`` is required (R4): an engine never invents a default store, so a
        # configured service cannot silently keep artifacts in memory.
        self.clock = clock
        self.storage = storage
        self.default_provider_id = default_provider_id

    def _refused(
        self,
        session: Session,
        plan: GenerationPlan,
        refusal: ProtectedArtistPromptRefused,
        *,
        attempts: list[GenerationAttempt],
        candidates: list[GeneratedCandidate],
        artifacts: list[AudioArtifact],
        rights_snapshots: list[RightsSnapshot],
        errors: list[str],
    ) -> GenerationEngineResult:
        """P29: a protected-artist or impersonation prompt is a hard stop. Nothing reaches
        a provider; the plan ends FAILED and each reason opens a hold on the plan, which
        only the policy's external process can close."""
        now = self.clock.now()
        queue = ExceptionQueue()
        for code in refusal.reason_codes:
            queue.raise_exception(
                session, origin_phase="P4", subject_ref=f"music.generation_plan/{plan.plan_id}",
                reason_code=code, raised_by="music_factory.generation", now=now,
                details={"hits": [h.term_id for h in refusal.hits],
                         "policy_version": refusal.policy_version},
            )
        update_plan_status(session, plan.plan_id, PlanStatus.FAILED, finished_at=now)
        session.flush()
        return GenerationEngineResult(
            plan_id=plan.plan_id, status=PlanStatus.FAILED, candidates=candidates,
            attempts=attempts, artifacts=artifacts, rights_snapshots=rights_snapshots,
            errors=[*errors, str(refusal)],
        )

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
        now = self.clock.now()
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

            try:
                prompt_obj = build_generation_prompt(spec, variant_delta=variant.prompt_delta)
            except ProtectedArtistPromptRefused as refusal:
                return self._refused(session, plan, refusal, attempts=attempts,
                                     candidates=candidates, artifacts=artifacts,
                                     rights_snapshots=rights_snapshots, errors=errors)
            retry_count = 0
            transport_parent: str | None = None
            base_seed = stable_seed("p4.generation", spec.track_spec_id, variant.variant_code)

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
                    started_at=self.clock.now(),
                    submitted_at=self.clock.now(),
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
                        model_version=gen_res.model_version,
                        lyrics_text=gen_res.lyrics_text,
                        attempt_type=attempt.attempt_type,
                        transport_retry_of=attempt.transport_retry_of,
                        request_hash=attempt.request_hash,
                        request_payload_hash=attempt.request_payload_hash,
                        status=AttemptStatus.SUCCEEDED,
                        started_at=attempt.started_at,
                        submitted_at=attempt.submitted_at,
                        completed_at=self.clock.now(),
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
                        created_at=self.clock.now(),
                        updated_at=self.clock.now(),
                        source_attempt_id=completed_attempt.attempt_id,
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
                        created_at=self.clock.now(),
                    )
                    insert_audio_artifact(session, art)
                    artifacts.append(art)

                    # Ruling R6 rights snapshot: what the provider declared, or nothing.
                    terms = adapter.license_terms()
                    if terms is not None:
                        recorded_at = self.clock.now()
                        rgt = RightsSnapshot(
                            snapshot_id=rights_snapshot_id(
                                completed_attempt.attempt_id, adapter.provider_id
                            ),
                            attempt_id=completed_attempt.attempt_id,
                            provider_id=adapter.provider_id,
                            commercial_use_status=terms.commercial_use_status,
                            basis=terms.basis,
                            license_reference=terms.license_reference,
                            policy_version=plan.policy_version,
                            checked_at=recorded_at,
                            plan_or_tier=terms.plan_or_tier,
                            account_identity_ref=terms.account_identity_ref,
                            terms_version=terms.terms_version,
                            created_at=recorded_at,
                            downloaded_at=recorded_at,
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
                        created_at=self.clock.now(),
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
                        completed_at=self.clock.now(),
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
        update_plan_status(session, plan.plan_id, final_status, finished_at=self.clock.now())
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
