"""Persist one gate evaluation in full (P5 §82).

The API route and the CLI used to repeat the same sequence of inserts; this is the one
copy. It does not commit: the caller owns the transaction.

A quarantine case raises its holds into the platform exception queue (``ops``, R3) in the
same transaction, so a P5 case never exists without its holds.
"""

from __future__ import annotations

from plm_platform.exceptions import ExceptionQueue
from sqlalchemy.orm import Session

from ..storage.detector_catalog import insert_candidate_fingerprint
from ..storage.repositories import (
    insert_candidate_evaluation,
    insert_evaluation_snapshot,
    insert_evidence_item,
    insert_gate_decision,
    insert_lyric_check,
    insert_musical_check,
    insert_policy_check,
    insert_provenance_check,
    insert_quarantine_case,
    insert_release_manifest,
    insert_rights_check,
    insert_risk_finding,
    insert_technical_check,
)
from .engine import GateEngineResult
from .quarantine import raise_quarantine_holds

__all__ = ["persist_gate_result"]


def persist_gate_result(
    session: Session, result: GateEngineResult, *, queue: ExceptionQueue | None = None
) -> None:
    """Insert the evaluation, its snapshot, every check, the decision and its outputs.

    ``queue`` is used only to raise holds, which needs no reviewer directory; the default
    queue uses the shipped hold-class policy.
    """
    evaluation = result.evaluation
    insert_candidate_evaluation(session, evaluation)
    insert_evaluation_snapshot(session, result.snapshot)
    for tc in evaluation.technical_checks:
        insert_technical_check(session, tc)
    for mc in evaluation.musical_checks:
        insert_musical_check(session, mc)
    for lc in evaluation.lyric_checks:
        insert_lyric_check(session, lc)
    for pc in evaluation.provenance_checks:
        insert_provenance_check(session, pc)
    for rc in evaluation.rights_checks:
        insert_rights_check(session, rc)
    for pol in evaluation.policy_checks:
        insert_policy_check(session, pol)
    for rf in evaluation.risk_findings:
        insert_risk_finding(session, rf)
    for ev in evaluation.evidence_items:
        insert_evidence_item(session, ev)
    insert_gate_decision(session, result.decision)
    if result.candidate_fingerprint is not None:
        # Joins the internal catalog once a manifest exists for this evaluation (R4).
        insert_candidate_fingerprint(
            session,
            evaluation_id=evaluation.id,
            candidate_id=evaluation.candidate_id,
            artifact_sha256=str(result.snapshot.artifact_snapshot["artifact_sha256"]),
            fingerprint=result.candidate_fingerprint,
            now=evaluation.created_at,
        )
    if result.quarantine_case:
        insert_quarantine_case(session, result.quarantine_case)
        raise_quarantine_holds(
            session,
            queue if queue is not None else ExceptionQueue(),
            result.quarantine_case,
            now=result.quarantine_case.created_at,
        )
    if result.release_manifest:
        insert_release_manifest(session, result.release_manifest)
