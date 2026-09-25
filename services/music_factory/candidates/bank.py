"""Candidate bank managing candidate lifecycle, queries, and transitions (§4.5, §65, R6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..domain.enums import CandidateStatus
from ..domain.models import (
    AudioArtifact,
    GeneratedCandidate,
    GenerationAttempt,
    RightsSnapshot,
)
from ..storage.models import generated_candidates_table
from ..storage.repositories import (
    get_candidate,
    get_rights_snapshot_for_attempt,
    list_artifacts_for_candidate,
    update_candidate_status,
)

__all__ = [
    "CandidateBankError",
    "CandidateNotFoundError",
    "FullCandidateDetail",
    "InvalidCandidateTransitionError",
    "accept_candidate",
    "get_candidate_detail",
    "list_candidates_by_experiment",
    "list_candidates_by_track",
    "mark_candidate_exported_to_p5",
    "reject_candidate",
]


class CandidateBankError(Exception):
    """Base exception for candidate bank operations."""


class CandidateNotFoundError(CandidateBankError):
    """Raised when a candidate ID does not exist."""


class InvalidCandidateTransitionError(CandidateBankError):
    """Raised when an illegal candidate status transition is attempted."""


@dataclass(frozen=True)
class FullCandidateDetail:
    """Consolidated representation of a candidate with audio, rights, and provenance."""

    candidate: GeneratedCandidate
    artifacts: Sequence[AudioArtifact]
    attempt: GenerationAttempt | None
    rights_snapshot: RightsSnapshot | None


def get_candidate_detail(session: Session, candidate_id: str) -> FullCandidateDetail:
    """Retrieve full candidate detail including artifacts and rights snapshot."""
    cand = get_candidate(session, candidate_id)
    if cand is None:
        raise CandidateNotFoundError(f"Candidate not found: {candidate_id!r}")

    artifacts = list_artifacts_for_candidate(session, candidate_id)

    # In our model, candidate was produced by an attempt. Attempt id can be derived from candidate
    # or candidate_id convention: cand_{plan_id}_{attempt_id}_{idx}
    attempt: GenerationAttempt | None = None
    rights: RightsSnapshot | None = None

    from ..storage.repositories import list_generation_attempts

    attempts = list_generation_attempts(session, cand.generation_plan_id)
    if attempts:
        attempt = attempts[0]
        rights = get_rights_snapshot_for_attempt(session, attempt.attempt_id)

    return FullCandidateDetail(
        candidate=cand,
        artifacts=artifacts,
        attempt=attempt,
        rights_snapshot=rights,
    )


def accept_candidate(session: Session, candidate_id: str) -> GeneratedCandidate:
    """Transition candidate from GENERATED to ACCEPTED."""
    cand = get_candidate(session, candidate_id)
    if cand is None:
        raise CandidateNotFoundError(f"Candidate not found: {candidate_id!r}")

    if cand.status not in (CandidateStatus.GENERATED, CandidateStatus.ACCEPTED):
        raise InvalidCandidateTransitionError(
            f"Cannot accept candidate in status {cand.status.value!r}"
        )

    update_candidate_status(session, candidate_id, CandidateStatus.ACCEPTED)
    updated = get_candidate(session, candidate_id)
    assert updated is not None
    return updated


def reject_candidate(
    session: Session,
    candidate_id: str,
    *,
    reason: str | None = None,
) -> GeneratedCandidate:
    """Transition candidate to REJECTED."""
    cand = get_candidate(session, candidate_id)
    if cand is None:
        raise CandidateNotFoundError(f"Candidate not found: {candidate_id!r}")

    if cand.status == CandidateStatus.EXPORTED_TO_P5:
        raise InvalidCandidateTransitionError(
            "Cannot reject a candidate that has already been exported to P5"
        )

    update_candidate_status(session, candidate_id, CandidateStatus.REJECTED)
    updated = get_candidate(session, candidate_id)
    assert updated is not None
    return updated


def mark_candidate_exported_to_p5(session: Session, candidate_id: str) -> GeneratedCandidate:
    """Transition candidate to EXPORTED_TO_P5."""
    cand = get_candidate(session, candidate_id)
    if cand is None:
        raise CandidateNotFoundError(f"Candidate not found: {candidate_id!r}")

    if cand.status not in (CandidateStatus.GENERATED, CandidateStatus.ACCEPTED):
        raise InvalidCandidateTransitionError(
            f"Cannot export candidate to P5 from status {cand.status.value!r}"
        )

    update_candidate_status(session, candidate_id, CandidateStatus.EXPORTED_TO_P5)
    updated = get_candidate(session, candidate_id)
    assert updated is not None
    return updated


def list_candidates_by_experiment(
    session: Session,
    experiment_id: str,
    *,
    status: CandidateStatus | None = None,
) -> list[GeneratedCandidate]:
    """List all candidates associated with an experiment."""
    stmt = sa.select(generated_candidates_table).where(
        generated_candidates_table.c.experiment_id == experiment_id
    )
    if status is not None:
        stmt = stmt.where(generated_candidates_table.c.status == status.value)
    stmt = stmt.order_by(generated_candidates_table.c.created_at)

    rows = session.execute(stmt).mappings().all()
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


def list_candidates_by_track(
    session: Session,
    track_spec_id: str,
) -> list[GeneratedCandidate]:
    """List all candidates generated for a given track specification."""
    from ..storage.repositories import list_candidates_by_track as repo_list

    return repo_list(session, track_spec_id)
