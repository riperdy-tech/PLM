"""P4's producer for Seam C4: ``music_candidate_ready_for_p5`` (R6, ADR-0025, ADR-0027).

The event is assembled only from facts P4 stored when it generated the candidate:

* the plan's C3 spec (lineage, vocal mode, genre, moods, requested duration and its
  declared duration range, ADR-0032);
* the candidate's own producing attempt and its creative variant, from which the prompt
  is rebuilt deterministically;
* the candidate's single full-mix artifact;
* the rights snapshot the provider declared for that attempt, or ``None``.

A missing fact refuses the export with a reason code. It is never filled in: the
placeholder arm id and ``provenance_complete=True`` that used to stand in are gone.
"""

from __future__ import annotations

from plm_contracts import ErrorShape
from plm_contracts.seams.c4 import (
    CandidateReadyPayload,
    GenerationProvenancePayload,
    MeasuredAudio,
    RightsSnapshotPayload,
)
from sqlalchemy.orm import Session

from ..domain.enums import ArtifactType, AttemptStatus, PlanStatus
from ..generation.prompts import build_generation_prompt
from ..ingestion.p3_adapter import TrackSpecValidationError, adapt_track_spec
from ..providers.flow_music_manual import MANUAL_MODEL_ID as FLOW_MUSIC_MODEL_ID
from ..providers.suno_manual import MANUAL_MODEL_ID
from ..storage.repositories import (
    get_candidate,
    get_generation_attempt,
    get_generation_plan,
    get_rights_snapshot_for_attempt,
    list_artifacts_for_candidate,
    list_creative_variants,
)

__all__ = ["CandidateExportError", "build_candidate_ready_event"]

EVENT_TYPE = "music_candidate_ready_for_p5"


class CandidateExportError(ValueError):
    """The candidate cannot be exported to P5; ``error`` names the missing fact."""

    def __init__(self, code: str, message: str, field_paths: list[str] | None = None) -> None:
        self.error = ErrorShape(code=code, field_paths=field_paths or [], message=message)
        super().__init__(f"{code}: {message}")



HUMAN_CONTRIBUTION = "none: fully automated generation; no human edits in P4"
"""P28 human_contribution for P4 output today: the pipeline has no human editing step."""

MANUAL_HUMAN_CONTRIBUTION = (
    "owner made the song on suno.com from the PLM prompt, chose one of Suno's clips and "
    "downloaded it unedited; no human edits in P4"
)
"""P28 human_contribution of an imported owner-made Suno song (model ``suno-web-manual``)."""

FLOW_MUSIC_HUMAN_CONTRIBUTION = (
    "owner made the song in Google Flow Music from the PLM prompt, chose one of its "
    "songs and downloaded it unedited; no human edits in P4"
)
"""P28 human_contribution of an imported owner-made Flow Music song."""

_MANUAL_CONTRIBUTIONS = {
    MANUAL_MODEL_ID: MANUAL_HUMAN_CONTRIBUTION,
    FLOW_MUSIC_MODEL_ID: FLOW_MUSIC_HUMAN_CONTRIBUTION,
}

def build_candidate_ready_event(session: Session, candidate_id: str) -> CandidateReadyPayload:
    """Assemble the C4 event for one candidate from stored facts, or refuse."""
    cand = get_candidate(session, candidate_id)
    if cand is None:
        raise CandidateExportError("candidate_not_found", f"no candidate {candidate_id!r}")

    plan = get_generation_plan(session, cand.generation_plan_id)
    if plan is None or plan.track_spec_wire is None:
        raise CandidateExportError(
            "plan_without_track_spec",
            f"candidate {candidate_id!r} comes from a plan with no stored C3 spec; "
            "its lineage and generation facts are unknown",
            ["generation_plan_id"],
        )
    if plan.status is not PlanStatus.COMPLETED:
        # The public C4 endpoint is independently callable; do not allow it
        # to bypass the pipeline's completed-plan requirement. An individual
        # candidate may exist even when P3's target was not met.
        raise CandidateExportError(
            "generation_plan_not_completed",
            f"candidate {candidate_id!r} belongs to a {plan.status.value} generation plan",
            ["generation_plan_id", "status"],
        )
    try:
        spec = adapt_track_spec(plan.track_spec_wire)
    except TrackSpecValidationError as exc:
        raise CandidateExportError(
            "stored_track_spec_invalid", exc.error.message, exc.error.field_paths
        ) from exc

    if (
        cand.track_spec_id != plan.track_spec_id
        or cand.experiment_id != plan.experiment_id
        or spec.track_spec_id != plan.track_spec_id
        or spec.experiment_id != plan.experiment_id
    ):
        raise CandidateExportError(
            "candidate_plan_identity_mismatch",
            "candidate, generation plan and stored C3 spec identify different source work",
            ["track_spec_id", "experiment_id"],
        )

    if cand.source_attempt_id is None:
        raise CandidateExportError(
            "candidate_without_attempt",
            f"candidate {candidate_id!r} does not record its producing attempt",
            ["source_attempt_id"],
        )
    attempt = get_generation_attempt(session, cand.source_attempt_id)
    if attempt is None:
        raise CandidateExportError(
            "attempt_not_found", f"attempt {cand.source_attempt_id!r} is missing"
        )
    if attempt.generation_plan_id != plan.plan_id:
        raise CandidateExportError(
            "attempt_plan_mismatch", "candidate's producing attempt belongs to another plan",
            ["source_attempt_id", "generation_plan_id"],
        )
    if attempt.status is not AttemptStatus.SUCCEEDED:
        raise CandidateExportError(
            "attempt_not_succeeded", "candidate's producing attempt did not succeed",
            ["source_attempt_id", "status"],
        )
    if not attempt.provider_job_id.strip():
        # Historical rows may hold artifacts and even rights despite having
        # no originating provider job identity. Do not export them as C4 facts.
        raise CandidateExportError(
            "provider_job_id_missing",
            "candidate's producing attempt has no provider job identifier",
            ["source_attempt_id", "provider_job_id"],
        )
    if attempt.provider_id != cand.provider_id or attempt.model_id != cand.model_id:
        raise CandidateExportError(
            "candidate_attempt_provider_mismatch",
            "candidate provider/model does not match its producing attempt",
            ["provider_id", "model_id"],
        )
    variant = next(
        (
            v
            for v in list_creative_variants(session, plan.plan_id)
            if v.variant_id == attempt.creative_variant_id
        ),
        None,
    )
    if variant is None:
        raise CandidateExportError(
            "variant_not_found",
            f"creative variant {attempt.creative_variant_id!r} is missing",
        )

    full_mixes = [
        a
        for a in list_artifacts_for_candidate(session, candidate_id)
        if a.artifact_type == ArtifactType.FULL_MIX
    ]
    if len(full_mixes) != 1:
        raise CandidateExportError(
            "artifact_not_unique",
            f"candidate {candidate_id!r} has {len(full_mixes)} full-mix artifacts; "
            "exactly one is required",
        )
    art = full_mixes[0]

    snapshot = get_rights_snapshot_for_attempt(session, attempt.attempt_id)
    if snapshot is not None and (
        snapshot.attempt_id != attempt.attempt_id
        or snapshot.provider_id != attempt.provider_id
    ):
        raise CandidateExportError(
            "rights_provider_mismatch",
            "rights snapshot does not identify the producing attempt and its provider",
            ["rights", "source_attempt_id"],
        )
    rights = (
        RightsSnapshotPayload(
            commercial_use_status=snapshot.commercial_use_status,
            basis=snapshot.basis,
            license_reference=snapshot.license_reference,
            plan_or_tier=snapshot.plan_or_tier,
            account_identity_ref=snapshot.account_identity_ref,
            terms_version=snapshot.terms_version,
            attempt_id=snapshot.attempt_id,
            provider_id=snapshot.provider_id,
            policy_version=snapshot.policy_version,
            checked_at=snapshot.checked_at,
            downloaded_at=snapshot.downloaded_at,
        )
        if snapshot is not None
        else None
    )

    prompt = build_generation_prompt(spec, variant_delta=variant.prompt_delta)
    return CandidateReadyPayload.build(
        event_type=EVENT_TYPE,
        candidate_id=cand.candidate_id,
        artifact_id=art.artifact_id,
        artifact_uri=art.object_uri,
        artifact_sha256=art.sha256,
        track_spec_id=spec.track_spec_id,
        lineage=spec.lineage,
        provider_id=cand.provider_id,
        model_id=cand.model_id,
        measured_audio=MeasuredAudio(
            duration_seconds=art.duration_seconds,
            sample_rate=art.sample_rate,
            channels=art.channels,
        ),
        requested_duration_seconds=spec.requested_duration,
        requested_duration_range=spec.duration_range,
        vocal_mode=spec.vocal_mode,
        genre_tags=(spec.genre, *spec.moods),
        prompt_text=prompt.prompt,
        lyrics_text=attempt.lyrics_text,
        rights=rights,
        generation_provenance=GenerationProvenancePayload(
            model_version=attempt.model_version,
            generation_timestamp=attempt.completed_at,
            # P4 sends providers a prompt and settings only: no samples, stems, voice or
            # melody inputs. That is a declared fact, not a default.
            source_inputs=(),
            human_contribution=_MANUAL_CONTRIBUTIONS.get(attempt.model_id, HUMAN_CONTRIBUTION),
        ),
    )
