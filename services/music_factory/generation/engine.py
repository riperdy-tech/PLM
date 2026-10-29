"""Durable music generation engine coordinating attempts, variants, and candidates (§4.4, §65, §111).

**Paid calls go through the write guard (R5).** Every provider ``generate`` call is an
external write: before it, :meth:`ExternalWriteGuard.authorize_external_write` decides it
under capability ``music.generate`` with the attempt id as the idempotency key and no
caller price (the ledger's rate card prices it, or refuses it as unpriced). Only
``SIMULATE`` (a ``ProviderMode.MOCK`` provider, no budget needed) and ``EXECUTE`` (live,
authorized, budget reserved) call the provider. Anything else stops the plan before the
call, with the guard's reason code in :attr:`GenerationEngineResult.stopped_reason`. After
an ``EXECUTE`` call the reservation is settled: committed at the reserved estimate when
the provider accepted a job (it reports credits, not money), released only on an
explicitly confirmed pre-submission refusal. A call that raises or has an ambiguous
outcome is left reserved until independently reconciled. Accepted audio without a
verified REAL-provider job ID cannot become an exportable candidate.

**Re-execution is resumable.** Running a plan again never pays twice for a variant that
already succeeded: its stored candidate is reused, attempt numbering continues after the
stored attempts, and the plan's attempt budget counts them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from plm_contracts import ProviderMode
from plm_platform.clock import Clock, stable_seed
from plm_platform.exceptions import ExceptionQueue
from plm_platform.execution import ExternalWriteGuard, GuardDecision, provider_mode_of
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
    list_candidates_by_track,
    list_generation_attempts,
    update_plan_status,
)
from .prompts import ProtectedArtistPromptRefused, build_generation_prompt

__all__ = [
    "GENERATE_CAPABILITY", "GenerationEngine", "GenerationEngineResult",
    "GenerationPlanSpecMismatch",
]

GENERATE_CAPABILITY = "music.generate"
"""The write-guard capability of one paid provider generation call."""

_ACCEPTED_OUTPUT_MISSING_FAILURE = "PROVIDER_ACCEPTED_OUTPUT_MISSING"
_ACCEPTED_JOB_ID_MISSING_FAILURE = "PROVIDER_ACCEPTED_JOB_ID_MISSING"
_UNKNOWN_SUBMISSION_FAILURE = "PROVIDER_OUTCOME_UNKNOWN"
_CONFIRMED_NO_SUBMISSION_FAILURE = "PROVIDER_CONFIRMED_NO_SUBMISSION"


class GenerationPlanSpecMismatch(ValueError):
    """The plan, persisted plan or C3 execution brief disagree before any paid call."""


class GenerationProviderNotAllowed(GenerationPlanSpecMismatch):
    """A selected provider was not authorized by the persisted C3 provider policy."""


def _prior_paid_credit_refusal(
    attempts: list[GenerationAttempt], credit_limit: float,
) -> str | None:
    """Conservatively inspect paid-plan usage before another real provider call.

    A completed paid attempt without a referenced usage amount is not zero.
    Known usage at or above the plan ceiling cannot buy more. This is only
    a safety brake: it does NOT reserve estimated credits for the next job,
    serialize concurrent plan executions, or enforce the money ceiling.
    """
    used = Decimal(0)
    for attempt in attempts:
        if attempt.status is not AttemptStatus.SUCCEEDED:
            continue
        if attempt.credit_basis == "mock_simulated":
            continue
        if (attempt.credit_basis != "provider_reported"
                or attempt.actual_credits is None
                or not attempt.credit_receipt_ref):
            return "plan_credit_usage_reconciliation_required"
        used += Decimal(str(attempt.actual_credits))
    if used >= Decimal(str(credit_limit)):
        return "plan_credit_budget_exhausted"
    return None


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
    stopped_reason: str | None = None
    """The write guard's reason code when it stopped the plan before a provider call."""
    stopped_decision: GuardDecision | None = None
    write_decisions: list[GuardDecision] = field(default_factory=list)
    """Every write-guard decision this run made, one per provider call considered."""


class GenerationEngine:
    """Executes music generation plans durably with fail-closed provenance."""

    def __init__(
        self,
        *,
        clock: Clock,
        storage: ArtifactStorageProtocol,
        default_provider_id: str = "mock",
        write_guard: ExternalWriteGuard | None = None,
    ) -> None:
        # ``storage`` is required (R4): an engine never invents a default store, so a
        # configured service cannot silently keep artifacts in memory.
        self.clock = clock
        self.storage = storage
        self.default_provider_id = default_provider_id
        # Without a configured guard every capability is DRY_RUN with no budget: a mock
        # provider simulates; a real one is never called.
        self.write_guard = write_guard or ExternalWriteGuard()

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

        # The caller cannot substitute a new brief under an existing plan ID:
        # the generated audio would then be mislabeled with the old C3 lineage,
        # rights and budget. Refuse before writing plan status or calling a provider.
        if (
            plan.track_spec_wire is None
            or plan.track_spec_wire != spec.raw_spec
            or plan.track_spec_id != spec.track_spec_id
            or plan.experiment_id != spec.experiment_id
            or plan.lineage != spec.lineage
        ):
            raise GenerationPlanSpecMismatch("plan and execution C3 track spec differ")
        stored_plan = get_generation_plan(session, plan.plan_id)
        if stored_plan is not None and any(
            getattr(stored_plan, name) != getattr(plan, name)
            for name in (
                "track_spec_wire", "track_spec_id", "experiment_id", "lineage",
                "candidate_target", "max_attempts", "budget_limit", "credit_limit",
                "policy_version",
            )
        ):
            raise GenerationPlanSpecMismatch("persisted generation plan differs from execution")
        existing_variants = {
            variant.variant_id: variant
            for variant in list_creative_variants(session, plan.plan_id)
        }
        if stored_plan is not None and (
            len(variants) != len(existing_variants)
            or {variant.variant_id for variant in variants} != set(existing_variants)
        ):
            raise GenerationPlanSpecMismatch("creative variant set differs from persisted plan")
        for variant in variants:
            if (
                variant.generation_plan_id != plan.plan_id
                or (stored_plan is not None and variant.variant_id not in existing_variants)
                or (
                    variant.variant_id in existing_variants
                    and existing_variants[variant.variant_id] != variant
                )
            ):
                raise GenerationPlanSpecMismatch("creative variant differs from persisted plan")
        if stored_plan is None:
            insert_generation_plan(session, plan)
        update_plan_status(session, plan.plan_id, PlanStatus.RUNNING, started_at=now)

        for variant in variants:
            if variant.variant_id not in existing_variants:
                insert_creative_variant(session, variant)

        adapter = provider_adapter or get_provider_adapter(self.default_provider_id)
        candidates: list[GeneratedCandidate] = []
        attempts: list[GenerationAttempt] = []
        artifacts: list[AudioArtifact] = []
        rights_snapshots: list[RightsSnapshot] = []
        errors: list[str] = []
        stopped: GuardDecision | None = None
        decisions: list[GuardDecision] = []

        # Resume: what a previous run of this plan already did is kept, never redone.
        stored_attempts = list_generation_attempts(session, plan.plan_id)
        stored_candidates = {
            c.source_attempt_id: c
            for c in list_candidates_by_track(session, plan.track_spec_id)
            if c.generation_plan_id == plan.plan_id
        }
        total_attempts = len(stored_attempts)
        candidate_count = 0
        # A live provider may have accepted/charged a job even without a
        # persisted provider_job_id (crash after submit, before the receipt).
        # SUBMITTED/PROCESSING/CANCELLED therefore remain unresolved until
        # independently reconciled; absence of a job ID does not prove
        # a no-charge outcome. A SUCCEEDED attempt without a real job ID
        # remains unverified even if an older code path created a candidate;
        # a missing candidate is also incomplete. A FAILED response is retryable
        # only without a job ID, accepted-outcome marker, or recorded charge evidence.
        # Mocks remain retryable; no account invoice is inferred here.
        unresolved_live_job = provider_mode_of(adapter) is ProviderMode.REAL and any(
            a.status in {
                AttemptStatus.SUBMITTED, AttemptStatus.PROCESSING, AttemptStatus.CANCELLED,
            }
            or (a.status is AttemptStatus.SUCCEEDED and (
                not a.provider_job_id.strip()
                or a.attempt_id not in stored_candidates
            ))
            or (a.status is AttemptStatus.FAILED and (
                a.failure_code != _CONFIRMED_NO_SUBMISSION_FAILURE
                or bool(a.provider_job_id)
                or a.credit_basis in {"provider_reported", "legacy_unverified"}
                or a.money_basis in {"provider_reported", "legacy_unverified"}
            ))
            for a in stored_attempts
        )
        if unresolved_live_job:
            errors.append("provider_job_reconciliation_required")

        for var_idx, variant in enumerate(variants):
            if (candidate_count >= plan.candidate_target
                    or stopped is not None or unresolved_live_job):
                break
            earlier = sorted(
                (a for a in stored_attempts if a.creative_variant_id == variant.variant_id),
                key=lambda a: (a.started_at, a.attempt_id),
            )
            done = next((stored_candidates[a.attempt_id] for a in earlier
                         if a.status == AttemptStatus.SUCCEEDED
                         and a.attempt_id in stored_candidates), None)
            if done is not None:
                candidates.append(done)
                candidate_count += 1
                continue

            try:
                prompt_obj = build_generation_prompt(spec, variant_delta=variant.prompt_delta)
            except ProtectedArtistPromptRefused as refusal:
                return self._refused(session, plan, refusal, attempts=attempts,
                                     candidates=candidates, artifacts=artifacts,
                                     rights_snapshots=rights_snapshots, errors=errors)
            retry_count = len(earlier)
            transport_parent: str | None = earlier[-1].attempt_id if earlier else None
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
                    bpm=prompt_obj.effective_bpm,
                    vocal_mode=spec.vocal_mode,
                    language=spec.language,
                    genre=prompt_obj.effective_genre,
                    style_tags=prompt_obj.style_tags,
                    seed=current_seed,
                    provider_id=adapter.provider_id,
                    model_id=adapter.model_id,
                )

                mode = provider_mode_of(adapter)
                # The global ledger does not enforce this plan's own ceilings.
                # A zero allowance, prior verified credit exhaustion, or
                # unpriced prior paid credit usage must stop future paid calls.
                # Remaining positive headroom alone is NOT an authorization:
                # per-call estimates, concurrency-safe plan reservations and
                # currency-aware money limits still require B03 completion.
                plan_refusal = None
                if mode is ProviderMode.REAL:
                    # C3's live-provider allowlist controls real external writes.
                    # Explicit MOCK is a simulation, not a claim that this
                    # mock provider was commercially permitted by P3. It must
                    # preserve mock provenance and never reserve paid budget.
                    if adapter.provider_id not in spec.allowed_providers:
                        plan_refusal = "plan_provider_not_allowed"
                    elif plan.budget_limit <= 0:
                        plan_refusal = "plan_money_budget_exhausted"
                    elif plan.credit_limit <= 0:
                        plan_refusal = "plan_credit_budget_exhausted"
                    else:
                        plan_refusal = _prior_paid_credit_refusal(
                            [*stored_attempts, *attempts], plan.credit_limit,
                        )
                decision = self.write_guard.authorize_external_write(
                    GENERATE_CAPABILITY,
                    provider_mode=mode,
                    idempotency_key=att_id,
                    cost_estimate=None,  # credits are not money: ledger rate card prices it
                    preflight_refusal=plan_refusal,
                    now=self.clock.now(),
                )
                decisions.append(decision)
                if not decision.decision.may_call_provider:
                    stopped = decision
                    errors.append(
                        f"{GENERATE_CAPABILITY} not performed for {att_id}: "
                        f"{decision.decision.value} ({decision.reason})"
                    )
                    break
                # A raise here leaves an EXECUTE reservation held (outcome unknown).
                gen_res = adapter.generate(gen_req)
                accepted = gen_res.status is AttemptStatus.SUCCEEDED or bool(
                    gen_res.provider_job_id
                )
                # Only an explicit, validated pre-submission refusal proves
                # that a REAL request had no external effect. A FAILED response
                # with no job ID may be a lost receipt after a charged submit;
                # its reservation must stay held until reconciliation.
                if mode is not ProviderMode.REAL or accepted or gen_res.confirmed_unsubmitted:
                    self.write_guard.settle_external_write(
                        decision, performed=accepted, actual_cost=None,
                        reason="provider_confirmed_unsubmitted",
                    )

                if (
                    gen_res.status is AttemptStatus.SUCCEEDED
                    and gen_res.audio_data
                    and (mode is not ProviderMode.REAL or gen_res.provider_job_id.strip())
                ):
                    # A paid REAL success with no actual provider job ID
                    # cannot become a usable candidate or rights provenance.
                    # The write was accepted and remains charged; require
                    # operator/provider reconciliation before publishing.
                    # A mock remains a simulated output without that receipt.
                    # A mock's usage is an explicitly simulated amount. For a
                    # live provider, only a referenced observation can be
                    # recorded as reported usage; everything else is UNKNOWN.
                    reported = gen_res.cost_credits
                    is_mock = provider_mode_of(adapter) == ProviderMode.MOCK
                    credit_basis: Literal["unknown", "mock_simulated", "provider_reported"] = (
                        "mock_simulated" if is_mock and reported is not None
                        else "provider_reported" if (
                            not is_mock and reported is not None
                            and gen_res.credit_receipt_ref
                        ) else "unknown"
                    )
                    recorded_credits = reported if credit_basis != "unknown" else None
                    credit_ref = (
                        gen_res.credit_receipt_ref
                        if credit_basis == "provider_reported" else None
                    )
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
                        # Credits do not establish a currency price. The
                        # ops budget ledger independently reserves the known
                        # rate-card estimate; P4 records money as UNKNOWN.
                        actual_cost=None,
                        money_basis="unpriced",
                        cost_currency=None,
                        actual_credits=recorded_credits,
                        credit_basis=credit_basis,
                        credit_receipt_ref=credit_ref,
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
                        actual_amount=None,
                        currency=None,
                        money_basis="unpriced",
                        pricing_version="unpriced",
                        credits_actual=completed_attempt.actual_credits,
                        credit_basis=completed_attempt.credit_basis,
                        credit_receipt_ref=completed_attempt.credit_receipt_ref,
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
                        provider_job_id=gen_res.provider_job_id.strip(),
                        attempt_type=attempt.attempt_type,
                        transport_retry_of=attempt.transport_retry_of,
                        request_hash=attempt.request_hash,
                        request_payload_hash=attempt.request_payload_hash,
                        status=AttemptStatus.FAILED,
                        # The provider accepted a billable request but
                        # returned no usable audio. An empty job ID must
                        # not turn this into a retryable no-charge refusal
                        # after restart (the ledger already committed).
                        failure_code=(
                            _CONFIRMED_NO_SUBMISSION_FAILURE
                            if mode is ProviderMode.REAL and gen_res.confirmed_unsubmitted
                            else _ACCEPTED_JOB_ID_MISSING_FAILURE
                            if mode is ProviderMode.REAL
                            and gen_res.status is AttemptStatus.SUCCEEDED
                            and gen_res.audio_data
                            and not gen_res.provider_job_id.strip()
                            else _ACCEPTED_OUTPUT_MISSING_FAILURE
                            if mode is ProviderMode.REAL
                            and gen_res.status is AttemptStatus.SUCCEEDED
                            else _UNKNOWN_SUBMISSION_FAILURE
                            if mode is ProviderMode.REAL and not accepted
                            else (
                                gen_res.error_message[:64]
                                if gen_res.error_message else "UNKNOWN_ERROR"
                            )
                        ),
                        started_at=attempt.started_at,
                        submitted_at=attempt.submitted_at,
                        completed_at=self.clock.now(),
                    )
                    insert_generation_attempt(session, failed_attempt)
                    attempts.append(failed_attempt)
                    errors.append(gen_res.error_message or "Unknown failure")
                    if mode is ProviderMode.REAL and not gen_res.confirmed_unsubmitted:
                        # An accepted/charged job OR an ambiguous response
                        # without a job ID needs reconciliation. The latter
                        # keeps the independent ledger reservation held.
                        # Neither can automatically buy another variant.
                        unresolved_live_job = True
                        errors.append("provider_job_reconciliation_required")
                        break

                    transport_parent = attempt.attempt_id
                    retry_count += 1

        # A plan is complete only when it fulfilled the target from its
        # immutable P3 brief. Preserve any usable partial candidates, but do
        # not falsely report COMPLETED merely because one attempt succeeded.
        if candidate_count < plan.candidate_target:
            errors.append(
                f"candidate_target_not_met: {candidate_count}/{plan.candidate_target}"
            )
        final_status = (
            PlanStatus.COMPLETED
            if stopped is None and candidate_count >= plan.candidate_target
            else PlanStatus.FAILED
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
            stopped_reason=None if stopped is None else stopped.reason,
            stopped_decision=stopped,
            write_decisions=decisions,
        )
