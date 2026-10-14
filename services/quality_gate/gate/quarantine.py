"""Quarantine resolution workflow and audit service for Phase P5 (P5 §49, §79, §91, R8).

An audited override issues a release manifest whose ``disposition`` stays the automated
decision (``QUARANTINED`` or ``REJECTED``) and whose ``override`` records who cleared it,
when, and for which case (ADR-0028). Every manifest fact is read from the evaluation's
immutable snapshot of the C4 event. An evaluation stored before lineage existed cannot be
cleared into a manifest, and nothing is filled in with ``*_unknown`` or ``OVERRIDDEN``.

Whether a hold may be cleared at all, and by whom, is the platform exception queue's
decision (R3, :mod:`plm_platform.exceptions`). Every reason code on a P5 quarantine case
is raised into ``ops`` as its own hold (:func:`raise_quarantine_holds`), classified by the
shipped hold-class policy. An override clears the P5 case only when
:meth:`~plm_platform.exceptions.ExceptionQueue.authorize_resume` allows every hold: a hard
stop is refused for any role, and a reviewable quarantine needs the reviewer role and
every required evidence item. A refused override writes nothing in ``qa`` and raises
:class:`OverrideRefusedError` carrying the refusal reason code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from plm_contracts import GateOverrideStatus
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c5 import GateOverrideRecord
from plm_platform.exceptions import (
    CaseStatus,
    ExceptionCase,
    ExceptionQueue,
    ResumeDecision,
    ReviewDecision,
    most_severe_refusal,
)
from sqlalchemy.orm import Session

from ..domain.enums import GateDisposition, ResolutionCode
from ..domain.models import QuarantineCase, ReleaseManifest
from ..storage.repositories import (
    get_candidate_evaluation,
    get_evaluation_snapshot,
    get_quarantine_case,
    insert_release_manifest,
    list_evidence_items,
    resolve_quarantine_case,
    update_candidate_evaluation_status,
)
from .manifest import build_release_manifest

__all__ = [
    "HOLD_ORIGIN",
    "UNSPECIFIED_QUARANTINE_REASON",
    "OverrideNotPossibleError",
    "OverrideRefusedError",
    "QuarantineAlreadyResolvedError",
    "hold_subject_ref",
    "quarantine_holds",
    "raise_quarantine_holds",
    "resolve_quarantine",
]

_BLOCKED = (GateDisposition.QUARANTINED, GateDisposition.REJECTED)

HOLD_ORIGIN = "P5"
"""Origin phase of every hold P5 raises into the platform exception queue."""

HOLD_RAISED_BY = "quality_gate"
"""The system actor recorded as raising P5's holds."""

UNSPECIFIED_QUARANTINE_REASON = "QUARANTINE_REVIEW_REQUIRED"
"""Raised when a case carries no reason code. The policy does not map it, so the queue
classifies it HARD_STOP: a quarantine without a stated reason never becomes reviewable."""


class OverrideNotPossibleError(ValueError):
    """The case cannot be cleared into a manifest because a required fact is missing."""


class QuarantineAlreadyResolvedError(ValueError):
    """The case was already resolved; a second resolution would issue a second manifest."""


class OverrideRefusedError(PermissionError):
    """The exception queue refused to resume at least one of the case's holds.

    ``refusal`` is the most severe refusal (its ``code`` is the API reason code);
    ``decisions`` holds the verdict for every hold.
    """

    def __init__(self, refusal: ResumeDecision, decisions: Sequence[ResumeDecision]) -> None:
        refused = [d for d in decisions if not d.allowed]
        super().__init__("; ".join(f"{d.code.value}: {d.message}" for d in refused))
        self.refusal = refusal
        self.decisions = tuple(decisions)

    @property
    def missing_evidence(self) -> tuple[str, ...]:
        return tuple(
            sorted({item for d in self.decisions if not d.allowed for item in d.missing_evidence})
        )


def hold_subject_ref(case_id: str) -> str:
    """The exception-queue subject for one P5 quarantine case (one per evaluation)."""
    return f"qa.quarantine_case/{case_id}"


def raise_quarantine_holds(
    session: Session, queue: ExceptionQueue, case: QuarantineCase, *, now: datetime
) -> list[ExceptionCase]:
    """Raise one hold per reason code on ``case`` (idempotent) and return them all."""
    reason_codes = sorted(set(case.reason_codes)) or [UNSPECIFIED_QUARANTINE_REASON]
    return [
        queue.raise_exception(
            session,
            origin_phase=HOLD_ORIGIN,
            subject_ref=hold_subject_ref(case.id),
            reason_code=reason_code,
            raised_by=HOLD_RAISED_BY,
            now=now,
            details={
                "candidate_id": case.candidate_id,
                "evaluation_id": case.evaluation_id,
                "quarantine_case_id": case.id,
            },
        ).case
        for reason_code in reason_codes
    ]


def quarantine_holds(session: Session, queue: ExceptionQueue, case_id: str) -> list[ExceptionCase]:
    """The holds already raised for one P5 quarantine case (read only)."""
    return queue.cases_for_subject(
        session, origin_phase=HOLD_ORIGIN, subject_ref=hold_subject_ref(case_id)
    )


def resolve_quarantine(
    session: Session,
    case_id: str,
    resolution: ResolutionCode,
    auditor_id: str,
    notes: str,
    *,
    now: datetime,
    queue: ExceptionQueue,
    reviewer_role: str | None = None,
    evidence: Mapping[str, str] | None = None,
) -> tuple[QuarantineCase, ReleaseManifest | None]:
    """Resolve a quarantine case and, on an audited override, issue its manifest.

    ``now`` is the resolution instant: the case's ``resolved_at``, the override record's
    ``resolved_at`` and the override manifest's ``created_at``.

    The case's holds are raised first (idempotent, so a case stored before R3 gains its
    holds here). ``CLEARED_BY_OVERRIDE`` asks ``queue`` to authorize a resume of every
    hold as ``auditor_id`` acting in ``reviewer_role`` with ``evidence``. Any refusal
    raises :class:`OverrideRefusedError` before anything in ``qa`` changes; the refusals
    are recorded in the queue. ``CONFIRMED_REJECTED`` rejects the open holds and
    ``RE_EVALUATE`` withdraws them; neither lets the workflow continue.
    """
    case = get_quarantine_case(session, case_id)
    if not case:
        raise ValueError(f"Quarantine case {case_id} not found.")
    if case.resolution is not None:
        raise QuarantineAlreadyResolvedError(
            f"quarantine case {case_id!r} was already resolved as {case.resolution}"
        )

    holds = raise_quarantine_holds(session, queue, case, now=now)

    manifest: ReleaseManifest | None = None
    if resolution == ResolutionCode.CLEARED_BY_OVERRIDE:
        decisions = [
            queue.authorize_resume(
                session, hold.id, actor=auditor_id, role=reviewer_role, evidence=evidence, now=now
            )
            for hold in holds
        ]
        refusal = most_severe_refusal(decisions)
        if refusal is not None:
            raise OverrideRefusedError(refusal, decisions)
        # Build the manifest first: an override that cannot produce a complete manifest
        # must not leave the case marked resolved.
        manifest = _override_manifest(session, case, auditor_id, now=now)
        for hold in holds:
            queue.resolve(
                session,
                hold.id,
                decision=ReviewDecision.RESUME,
                actor=auditor_id,
                role=reviewer_role,
                evidence=evidence,
                notes=notes,
                now=now,
            )
    else:
        closing = (
            ReviewDecision.REJECT
            if resolution == ResolutionCode.CONFIRMED_REJECTED
            else ReviewDecision.WITHDRAW
        )
        for hold in holds:
            if hold.status is CaseStatus.OPEN:
                queue.resolve(
                    session,
                    hold.id,
                    decision=closing,
                    actor=auditor_id,
                    role=reviewer_role,
                    evidence=evidence,
                    notes=notes,
                    now=now,
                )

    resolve_quarantine_case(session, case_id, resolution, auditor_id, notes, resolved_at=now)
    updated_case = case.resolve(resolution, auditor_id, notes, resolved_at=now)

    if manifest is not None:
        update_candidate_evaluation_status(
            session=session,
            evaluation_id=case.evaluation_id,
            disposition=GateDisposition.CLEARED_BY_OVERRIDE,  # P5-internal effective status
        )
        insert_release_manifest(session, manifest)
    elif resolution == ResolutionCode.CONFIRMED_REJECTED:
        update_candidate_evaluation_status(
            session=session,
            evaluation_id=case.evaluation_id,
            disposition=GateDisposition.REJECTED,
        )

    return updated_case, manifest



def _override_manifest(
    session: Session, case: QuarantineCase, auditor_id: str, *, now: datetime
) -> ReleaseManifest:
    evaluation = get_candidate_evaluation(session, case.evaluation_id)
    snapshot = get_evaluation_snapshot(session, case.evaluation_id)
    if evaluation is None or snapshot is None:
        raise OverrideNotPossibleError(
            f"case {case.id!r}: evaluation {case.evaluation_id!r} or its snapshot is missing"
        )
    if evaluation.disposition not in _BLOCKED:
        raise OverrideNotPossibleError(
            f"case {case.id!r}: only a QUARANTINED or REJECTED candidate can be overridden, "
            f"not {evaluation.disposition}"
        )
    cand = snapshot.candidate_snapshot
    art = snapshot.artifact_snapshot
    evidence = list_evidence_items(session, evaluation.id)
    missing = [
        name
        for name, value in (
            ("lineage", cand.get("lineage")),
            ("artifact_uri", art.get("artifact_uri")),
            ("artifact_sha256", art.get("artifact_sha256")),
            ("measured_audio", art.get("measured_audio")),
            ("evidence", evidence),
        )
        if not value
    ]
    if missing:
        raise OverrideNotPossibleError(
            f"case {case.id!r}: the evaluation snapshot predates ADR-0028 and lacks {missing}"
        )

    assert evaluation.disposition is not None  # narrowed by the _BLOCKED check above
    return build_release_manifest(
        candidate_id=case.candidate_id,
        canonical_artifact_id=evaluation.artifact_id,
        artifact_uri=art["artifact_uri"],
        artifact_sha256=art["artifact_sha256"],
        measured_duration_seconds=float(art["measured_audio"]["duration_seconds"]),
        track_spec_id=cand["track_spec_id"],
        lineage=ExperimentLineage.model_validate(cand["lineage"]),
        disposition=evaluation.disposition,
        override=GateOverrideRecord(
            status=GateOverrideStatus.CLEARED_BY_OVERRIDE,
            quarantine_case_id=case.id,
            auditor_id=auditor_id,
            resolved_at=now,
            reason_codes=tuple(case.reason_codes),
        ),
        technical_status=evaluation.technical_status,
        commercial_use_status=evaluation.rights_status,
        provenance_status=evaluation.provenance_status,
        # Every P4 candidate is AI-generated; P5's policy screen always requires disclosure.
        ai_disclosure_required=True,
        rights_risk=_rights_risk(evaluation.rights_status),
        policy_status=evaluation.policy_status,
        evaluation_id_str=evaluation.id,
        evaluation_policy_version=evaluation.evaluation_policy_version,
        warning_codes=list(case.reason_codes),
        evidence_bundle_id=evidence[0].id,
        now=now,
    )


def _rights_risk(commercial_use_status: str) -> str:
    """The risk the rights evaluator assigns to a status (see ``evaluators.rights``)."""
    return {"eligible": "LOW", "ineligible": "CRITICAL"}.get(commercial_use_status, "MEDIUM")
