"""Quarantine resolution workflow and audit service for Phase P5 (P5 §49, §79, §91, Ruling R8)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.enums import (
    GateDisposition,
    ResolutionCode,
)
from ..domain.models import QuarantineCase, ReleaseManifest
from ..storage.repositories import (
    get_candidate_evaluation,
    get_evaluation_snapshot,
    get_quarantine_case,
    insert_release_manifest,
    resolve_quarantine_case,
    update_candidate_evaluation_status,
)
from .manifest import build_release_manifest

__all__ = ["resolve_quarantine"]


def resolve_quarantine(
    session: Session,
    case_id: str,
    resolution: ResolutionCode,
    auditor_id: str,
    notes: str,
) -> tuple[QuarantineCase, ReleaseManifest | None]:
    """Resolve a quarantine case and update candidate disposition accordingly."""
    case = get_quarantine_case(session, case_id)
    if not case:
        raise ValueError(f"Quarantine case {case_id} not found.")

    resolve_quarantine_case(session, case_id, resolution, auditor_id, notes)
    updated_case = case.resolve(resolution, auditor_id, notes)

    manifest: ReleaseManifest | None = None
    eval_record = get_candidate_evaluation(session, case.evaluation_id)

    if resolution == ResolutionCode.CLEARED_BY_OVERRIDE:
        # Override disposition: candidate cleared by audited override (P5 §79, Ruling R8)
        new_disposition = GateDisposition.CLEARED_BY_OVERRIDE
        snapshot = get_evaluation_snapshot(session, case.evaluation_id)

        update_candidate_evaluation_status(
            session=session,
            evaluation_id=case.evaluation_id,
            disposition=new_disposition,
        )

        cand_snap = snapshot.candidate_snapshot if snapshot else {}
        manifest = build_release_manifest(
            candidate_id=case.candidate_id,
            canonical_artifact_id=eval_record.artifact_id if eval_record else "art_unknown",
            experiment_id=cand_snap.get("experiment_id", "exp_unknown"),
            track_spec_id=cand_snap.get("track_spec_id", "spec_unknown"),
            content_arm_id=cand_snap.get("content_arm_id", "arm_unknown"),
            disposition=new_disposition,
            technical_status=eval_record.technical_status if eval_record else "OVERRIDDEN",
            commercial_use_status=eval_record.rights_status if eval_record else "OVERRIDDEN",
            provenance_status=eval_record.provenance_status if eval_record else "OVERRIDDEN",
            ai_disclosure_required=True,
            rights_risk="OVERRIDDEN",
            policy_status="OVERRIDDEN",
            evaluation_id_str=case.evaluation_id,
            evaluation_policy_version=eval_record.evaluation_policy_version if eval_record else "p5-v1",
            warning_codes=case.reason_codes,
            evidence_bundle_id=f"audit_override_{auditor_id}",
        )
        insert_release_manifest(session, manifest)

    elif resolution == ResolutionCode.CONFIRMED_REJECTED:
        update_candidate_evaluation_status(
            session=session,
            evaluation_id=case.evaluation_id,
            disposition=GateDisposition.REJECTED,
        )

    return updated_case, manifest
