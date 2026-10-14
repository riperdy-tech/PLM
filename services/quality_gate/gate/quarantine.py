"""Quarantine resolution workflow and audit service for Phase P5 (P5 §49, §79, §91, R8).

An audited override issues a release manifest whose ``disposition`` stays the automated
decision (``QUARANTINED`` or ``REJECTED``) and whose ``override`` records who cleared it,
when, and for which case (ADR-0028). Every manifest fact is read from the evaluation's
immutable snapshot of the C4 event. An evaluation stored before lineage existed cannot be
cleared into a manifest, and nothing is filled in with ``*_unknown`` or ``OVERRIDDEN``.

Which holds a reviewer may clear (reviewable quarantine vs hard stop) and who may clear
them is enforced by the exception queue in stage R3. This module only records the
override faithfully.
"""

from __future__ import annotations

from datetime import UTC, datetime

from plm_contracts import GateOverrideStatus
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c5 import GateOverrideRecord
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

__all__ = ["OverrideNotPossibleError", "resolve_quarantine"]

_BLOCKED = (GateDisposition.QUARANTINED, GateDisposition.REJECTED)


class OverrideNotPossibleError(ValueError):
    """The case cannot be cleared into a manifest because a required fact is missing."""


def resolve_quarantine(
    session: Session,
    case_id: str,
    resolution: ResolutionCode,
    auditor_id: str,
    notes: str,
) -> tuple[QuarantineCase, ReleaseManifest | None]:
    """Resolve a quarantine case and, on an audited override, issue its manifest."""
    case = get_quarantine_case(session, case_id)
    if not case:
        raise ValueError(f"Quarantine case {case_id} not found.")

    manifest: ReleaseManifest | None = None
    if resolution == ResolutionCode.CLEARED_BY_OVERRIDE:
        # Build the manifest first: an override that cannot produce a complete manifest
        # must not leave the case marked resolved.
        manifest = _override_manifest(session, case, auditor_id)

    resolve_quarantine_case(session, case_id, resolution, auditor_id, notes)
    updated_case = case.resolve(resolution, auditor_id, notes)

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
    session: Session, case: QuarantineCase, auditor_id: str
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
            resolved_at=datetime.now(UTC),
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
    )


def _rights_risk(commercial_use_status: str) -> str:
    """The risk the rights evaluator assigns to a status (see ``evaluators.rights``)."""
    return {"eligible": "LOW", "ineligible": "CRITICAL"}.get(commercial_use_status, "MEDIUM")
