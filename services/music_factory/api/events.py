"""P4's producer for Seam C4: ``music_candidate_ready_for_p5`` (R6, ADR-0025, ADR-0027).

The event is assembled only from facts P4 stored when it generated the candidate:

* the plan's C3 spec (lineage, vocal mode, genre, moods, requested duration);
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
    MeasuredAudio,
    RightsSnapshotPayload,
)
from sqlalchemy.orm import Session

from ..domain.enums import ArtifactType
from ..generation.prompts import build_generation_prompt
from ..ingestion.p3_adapter import TrackSpecValidationError, adapt_track_spec
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
    try:
        spec = adapt_track_spec(plan.track_spec_wire)
    except TrackSpecValidationError as exc:
        raise CandidateExportError(
            "stored_track_spec_invalid", exc.error.message, exc.error.field_paths
        ) from exc

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
        vocal_mode=spec.vocal_mode,
        genre_tags=(spec.genre, *spec.moods),
        prompt_text=prompt.prompt,
        lyrics_text=None,
        rights=rights,
    )
