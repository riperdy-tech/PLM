"""Repository access functions for the `qa` schema (PHASE-P5 §82-§92, R7, R8)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..domain.enums import (
    CheckSeverity,
    CheckStatus,
    EvaluationStatus,
    GateCategory,
    GateDisposition,
    QuarantineStatus,
    ResolutionCode,
)
from ..domain.models import (
    CandidateEvaluation,
    EvaluationSnapshot,
    EvidenceItem,
    GateDecision,
    LyricCheck,
    MusicalCheck,
    PolicyCheck,
    ProvenanceCheck,
    QuarantineCase,
    ReleaseManifest,
    RightsCheck,
    RiskFinding,
    TechnicalCheck,
)
from .models import (
    candidate_evaluations_table,
    evaluation_snapshots_table,
    evidence_items_table,
    gate_decisions_table,
    lyric_checks_table,
    musical_checks_table,
    policy_checks_table,
    provenance_checks_table,
    quarantine_cases_table,
    release_manifests_table,
    rights_checks_table,
    risk_findings_table,
    technical_checks_table,
)

__all__ = [
    "get_candidate_evaluation",
    "get_evaluation_snapshot",
    "get_gate_decision",
    "get_quarantine_case",
    "get_release_manifest_by_candidate",
    "get_release_manifest_by_id",
    "insert_candidate_evaluation",
    "insert_evaluation_snapshot",
    "insert_evidence_item",
    "insert_gate_decision",
    "insert_lyric_check",
    "insert_musical_check",
    "insert_policy_check",
    "insert_provenance_check",
    "insert_quarantine_case",
    "insert_release_manifest",
    "insert_rights_check",
    "insert_risk_finding",
    "insert_technical_check",
    "list_candidate_evaluations",
    "list_evidence_items",
    "list_open_quarantine_cases",
    "list_risk_findings",
    "list_technical_checks",
    "resolve_quarantine_case",
    "update_candidate_evaluation_status",
]


# 1. Candidate Evaluations
def insert_candidate_evaluation(session: Session, evaluation: CandidateEvaluation) -> None:
    session.execute(
        candidate_evaluations_table.insert().values(
            id=evaluation.id,
            candidate_id=evaluation.candidate_id,
            artifact_id=evaluation.artifact_id,
            evaluation_policy_version=evaluation.evaluation_policy_version,
            trigger=evaluation.trigger,
            started_at=evaluation.started_at,
            finished_at=evaluation.finished_at,
            status=evaluation.status.value,
            technical_status=evaluation.technical_status,
            quality_status=evaluation.quality_status,
            provenance_status=evaluation.provenance_status,
            rights_status=evaluation.rights_status,
            policy_status=evaluation.policy_status,
            disposition=evaluation.disposition.value if evaluation.disposition else None,
            composite_quality_score=evaluation.composite_quality_score,
            composite_quality_confidence=evaluation.composite_quality_confidence,
            created_at=evaluation.created_at,
        )
    )


def update_candidate_evaluation_status(
    session: Session,
    evaluation_id: str,
    status: EvaluationStatus | None = None,
    disposition: GateDisposition | None = None,
    technical_status: str | None = None,
    quality_status: str | None = None,
    provenance_status: str | None = None,
    rights_status: str | None = None,
    policy_status: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {}
    if status is not None:
        values["status"] = status.value
    if disposition is not None:
        values["disposition"] = disposition.value
    if technical_status:
        values["technical_status"] = technical_status
    if quality_status:
        values["quality_status"] = quality_status
    if provenance_status:
        values["provenance_status"] = provenance_status
    if rights_status:
        values["rights_status"] = rights_status
    if policy_status:
        values["policy_status"] = policy_status
    if finished_at:
        values["finished_at"] = finished_at

    session.execute(
        candidate_evaluations_table.update()
        .where(candidate_evaluations_table.c.id == evaluation_id)
        .values(**values)
    )


def get_candidate_evaluation(session: Session, evaluation_id: str) -> CandidateEvaluation | None:
    row = session.execute(
        candidate_evaluations_table.select().where(candidate_evaluations_table.c.id == evaluation_id)
    ).mappings().first()
    if not row:
        return None
    return CandidateEvaluation(
        id=row["id"],
        candidate_id=row["candidate_id"],
        artifact_id=row["artifact_id"],
        evaluation_policy_version=row["evaluation_policy_version"],
        trigger=row["trigger"],
        status=EvaluationStatus(row["status"]),
        disposition=GateDisposition(row["disposition"]) if row["disposition"] else None,
        technical_status=row["technical_status"],
        quality_status=row["quality_status"],
        provenance_status=row["provenance_status"],
        rights_status=row["rights_status"],
        policy_status=row["policy_status"],
        composite_quality_score=row["composite_quality_score"],
        composite_quality_confidence=row["composite_quality_confidence"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
    )


def list_candidate_evaluations(session: Session, candidate_id: str) -> list[CandidateEvaluation]:
    rows = session.execute(
        candidate_evaluations_table.select()
        .where(candidate_evaluations_table.c.candidate_id == candidate_id)
        .order_by(candidate_evaluations_table.c.created_at.desc())
    ).mappings().all()
    return [
        CandidateEvaluation(
            id=r["id"],
            candidate_id=r["candidate_id"],
            artifact_id=r["artifact_id"],
            evaluation_policy_version=r["evaluation_policy_version"],
            trigger=r["trigger"],
            status=EvaluationStatus(r["status"]),
            disposition=GateDisposition(r["disposition"]) if r["disposition"] else None,
            technical_status=r["technical_status"],
            quality_status=r["quality_status"],
            provenance_status=r["provenance_status"],
            rights_status=r["rights_status"],
            policy_status=r["policy_status"],
            composite_quality_score=r["composite_quality_score"],
            composite_quality_confidence=r["composite_quality_confidence"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# 2. Evaluation Snapshots
def insert_evaluation_snapshot(session: Session, snapshot: EvaluationSnapshot) -> None:
    session.execute(
        evaluation_snapshots_table.insert().values(
            id=snapshot.id,
            evaluation_id=snapshot.evaluation_id,
            candidate_id=snapshot.candidate_id,
            artifact_id=snapshot.artifact_id,
            candidate_snapshot_json=json.dumps(snapshot.candidate_snapshot, sort_keys=True, default=str),
            artifact_snapshot_json=json.dumps(snapshot.artifact_snapshot, sort_keys=True, default=str),
            provider_snapshot_json=json.dumps(snapshot.provider_snapshot, sort_keys=True, default=str),
            license_snapshot_json=json.dumps(snapshot.license_snapshot, sort_keys=True, default=str),
            p3_snapshot_ref=snapshot.p3_snapshot_ref,
            p4_snapshot_ref=snapshot.p4_snapshot_ref,
            policy_snapshot_ref=snapshot.policy_snapshot_ref,
            catalog_snapshot_ref=snapshot.catalog_snapshot_ref,
            snapshot_hash=snapshot.snapshot_hash,
            created_at=snapshot.created_at,
        )
    )


def get_evaluation_snapshot(session: Session, evaluation_id: str) -> EvaluationSnapshot | None:
    row = session.execute(
        evaluation_snapshots_table.select().where(evaluation_snapshots_table.c.evaluation_id == evaluation_id)
    ).mappings().first()
    if not row:
        return None
    return EvaluationSnapshot(
        id=row["id"],
        evaluation_id=row["evaluation_id"],
        candidate_id=row["candidate_id"],
        artifact_id=row["artifact_id"],
        candidate_snapshot=json.loads(row["candidate_snapshot_json"]),
        artifact_snapshot=json.loads(row["artifact_snapshot_json"]),
        provider_snapshot=json.loads(row["provider_snapshot_json"]),
        license_snapshot=json.loads(row["license_snapshot_json"]),
        p3_snapshot_ref=row["p3_snapshot_ref"],
        p4_snapshot_ref=row["p4_snapshot_ref"],
        policy_snapshot_ref=row["policy_snapshot_ref"],
        catalog_snapshot_ref=row["catalog_snapshot_ref"],
        snapshot_hash=row["snapshot_hash"],
        created_at=row["created_at"],
    )


# 3. Technical Checks
def insert_technical_check(session: Session, check: TechnicalCheck) -> None:
    session.execute(
        technical_checks_table.insert().values(
            id=check.id,
            evaluation_id=check.evaluation_id,
            check_type=check.check_type,
            status=check.status.value,
            severity=check.severity.value,
            measured_value=str(check.measured_value),
            expected_value=str(check.expected_value),
            unit=check.unit,
            start_seconds=check.start_seconds,
            end_seconds=check.end_seconds,
            tool_name=check.tool_name,
            tool_version=check.tool_version,
            config_version=check.config_version,
            created_at=check.created_at,
        )
    )


def list_technical_checks(session: Session, evaluation_id: str) -> list[TechnicalCheck]:
    rows = session.execute(
        technical_checks_table.select().where(technical_checks_table.c.evaluation_id == evaluation_id)
    ).mappings().all()
    return [
        TechnicalCheck(
            id=r["id"],
            evaluation_id=r["evaluation_id"],
            check_type=r["check_type"],
            status=CheckStatus(r["status"]),
            severity=CheckSeverity(r["severity"]),
            measured_value=r["measured_value"],
            expected_value=r["expected_value"],
            unit=r["unit"],
            start_seconds=r["start_seconds"],
            end_seconds=r["end_seconds"],
            tool_name=r["tool_name"],
            tool_version=r["tool_version"],
            config_version=r["config_version"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# 4. Musical Checks
def insert_musical_check(session: Session, check: MusicalCheck) -> None:
    session.execute(
        musical_checks_table.insert().values(
            id=check.id,
            evaluation_id=check.evaluation_id,
            check_type=check.check_type,
            score=check.score,
            confidence=check.confidence,
            severity=check.severity.value,
            method=check.method,
            model_version=check.model_version,
            segment_json=json.dumps(check.segment_data, sort_keys=True, default=str),
            created_at=check.created_at,
        )
    )


# 5. Lyric Checks
def insert_lyric_check(session: Session, check: LyricCheck) -> None:
    session.execute(
        lyric_checks_table.insert().values(
            id=check.id,
            evaluation_id=check.evaluation_id,
            check_type=check.check_type,
            status=check.status.value,
            severity=check.severity.value,
            details_json=json.dumps(check.details, sort_keys=True, default=str),
            created_at=check.created_at,
        )
    )


# 6. Provenance Checks
def insert_provenance_check(session: Session, check: ProvenanceCheck) -> None:
    session.execute(
        provenance_checks_table.insert().values(
            id=check.id,
            evaluation_id=check.evaluation_id,
            check_type=check.check_type,
            status=check.status.value,
            severity=check.severity.value,
            details_json=json.dumps(check.details, sort_keys=True, default=str),
            created_at=check.created_at,
        )
    )


# 7. Rights Checks
def insert_rights_check(session: Session, check: RightsCheck) -> None:
    session.execute(
        rights_checks_table.insert().values(
            id=check.id,
            evaluation_id=check.evaluation_id,
            check_type=check.check_type,
            status=check.status.value,
            risk_level=check.risk_level,
            confidence=check.confidence,
            source_type=check.source_type,
            source_ref=check.source_ref,
            matched_reference_id=check.matched_reference_id,
            similarity_score=check.similarity_score,
            evidence_json=json.dumps(check.evidence_data, sort_keys=True, default=str),
            created_at=check.created_at,
        )
    )


# 8. Policy Checks
def insert_policy_check(session: Session, check: PolicyCheck) -> None:
    session.execute(
        policy_checks_table.insert().values(
            id=check.id,
            evaluation_id=check.evaluation_id,
            policy_namespace=check.policy_namespace,
            rule_id=check.rule_id,
            rule_version=check.rule_version,
            status=check.status.value,
            severity=check.severity.value,
            evidence_json=json.dumps(check.evidence_data, sort_keys=True, default=str),
            created_at=check.created_at,
        )
    )


# 9. Risk Findings
def insert_risk_finding(session: Session, finding: RiskFinding) -> None:
    session.execute(
        risk_findings_table.insert().values(
            id=finding.id,
            evaluation_id=finding.evaluation_id,
            finding_code=finding.finding_code,
            category=finding.category.value,
            severity=finding.severity.value,
            status=finding.status,
            confidence=finding.confidence,
            message=finding.message,
            start_seconds=finding.start_seconds,
            end_seconds=finding.end_seconds,
            evidence_item_ids_json=json.dumps(finding.evidence_item_ids),
            blocking=finding.blocking,
            repairability=finding.repairability,
            created_at=finding.created_at,
        )
    )


def list_risk_findings(session: Session, evaluation_id: str) -> list[RiskFinding]:
    rows = session.execute(
        risk_findings_table.select().where(risk_findings_table.c.evaluation_id == evaluation_id)
    ).mappings().all()
    return [
        RiskFinding(
            id=r["id"],
            evaluation_id=r["evaluation_id"],
            finding_code=r["finding_code"],
            category=GateCategory(r["category"]),
            severity=CheckSeverity(r["severity"]),
            status=r["status"],
            confidence=r["confidence"],
            message=r["message"],
            start_seconds=r["start_seconds"],
            end_seconds=r["end_seconds"],
            evidence_item_ids=json.loads(r["evidence_item_ids_json"]),
            blocking=r["blocking"],
            repairability=r["repairability"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# 10. Evidence Items
def insert_evidence_item(session: Session, evidence: EvidenceItem) -> None:
    session.execute(
        evidence_items_table.insert().values(
            id=evidence.id,
            evaluation_id=evidence.evaluation_id,
            evidence_type=evidence.evidence_type,
            source=evidence.source,
            source_reference=evidence.source_reference,
            content_hash=evidence.content_hash,
            summary=evidence.summary,
            raw_pointer=evidence.raw_pointer,
            created_at=evidence.created_at,
        )
    )


def list_evidence_items(session: Session, evaluation_id: str) -> list[EvidenceItem]:
    rows = session.execute(
        evidence_items_table.select().where(evidence_items_table.c.evaluation_id == evaluation_id)
    ).mappings().all()
    return [
        EvidenceItem(
            id=r["id"],
            evaluation_id=r["evaluation_id"],
            evidence_type=r["evidence_type"],
            source=r["source"],
            source_reference=r["source_reference"],
            content_hash=r["content_hash"],
            summary=r["summary"],
            raw_pointer=r["raw_pointer"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# 11. Gate Decisions
def insert_gate_decision(session: Session, decision: GateDecision) -> None:
    session.execute(
        gate_decisions_table.insert().values(
            id=decision.id,
            evaluation_id=decision.evaluation_id,
            disposition=decision.disposition.value,
            gate_results_json=json.dumps(decision.gate_results, sort_keys=True, default=str),
            blocking_finding_ids_json=json.dumps(decision.blocking_finding_ids),
            warning_finding_ids_json=json.dumps(decision.warning_finding_ids),
            decision_version=decision.decision_version,
            decision_hash=decision.decision_hash,
            created_at=decision.created_at,
        )
    )


def get_gate_decision(session: Session, evaluation_id: str) -> GateDecision | None:
    row = session.execute(
        gate_decisions_table.select().where(gate_decisions_table.c.evaluation_id == evaluation_id)
    ).mappings().first()
    if not row:
        return None
    return GateDecision(
        id=row["id"],
        evaluation_id=row["evaluation_id"],
        disposition=GateDisposition(row["disposition"]),
        gate_results=json.loads(row["gate_results_json"]),
        blocking_finding_ids=json.loads(row["blocking_finding_ids_json"]),
        warning_finding_ids=json.loads(row["warning_finding_ids_json"]),
        decision_version=row["decision_version"],
        decision_hash=row["decision_hash"],
        created_at=row["created_at"],
    )


# 12. Quarantine Cases
def insert_quarantine_case(session: Session, case: QuarantineCase) -> None:
    session.execute(
        quarantine_cases_table.insert().values(
            id=case.id,
            candidate_id=case.candidate_id,
            evaluation_id=case.evaluation_id,
            reason_codes_json=json.dumps(case.reason_codes),
            priority=case.priority,
            status=case.status.value,
            assigned_to=case.assigned_to,
            created_at=case.created_at,
            resolved_at=case.resolved_at,
            resolution=case.resolution.value if case.resolution else None,
            resolution_notes=case.resolution_notes,
        )
    )


def get_quarantine_case(session: Session, case_id: str) -> QuarantineCase | None:
    row = session.execute(
        quarantine_cases_table.select().where(quarantine_cases_table.c.id == case_id)
    ).mappings().first()
    if not row:
        return None
    return QuarantineCase(
        id=row["id"],
        candidate_id=row["candidate_id"],
        evaluation_id=row["evaluation_id"],
        reason_codes=json.loads(row["reason_codes_json"]),
        priority=row["priority"],
        status=QuarantineStatus(row["status"]),
        assigned_to=row["assigned_to"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        resolution=ResolutionCode(row["resolution"]) if row["resolution"] else None,
        resolution_notes=row["resolution_notes"],
    )


def list_open_quarantine_cases(session: Session) -> list[QuarantineCase]:
    rows = session.execute(
        quarantine_cases_table.select()
        .where(quarantine_cases_table.c.status == QuarantineStatus.OPEN.value)
        .order_by(quarantine_cases_table.c.created_at.asc())
    ).mappings().all()
    return [
        QuarantineCase(
            id=r["id"],
            candidate_id=r["candidate_id"],
            evaluation_id=r["evaluation_id"],
            reason_codes=json.loads(r["reason_codes_json"]),
            priority=r["priority"],
            status=QuarantineStatus(r["status"]),
            assigned_to=r["assigned_to"],
            created_at=r["created_at"],
            resolved_at=r["resolved_at"],
            resolution=ResolutionCode(r["resolution"]) if r["resolution"] else None,
            resolution_notes=r["resolution_notes"],
        )
        for r in rows
    ]


def resolve_quarantine_case(
    session: Session,
    case_id: str,
    resolution: ResolutionCode,
    auditor_id: str,
    notes: str,
) -> None:
    session.execute(
        quarantine_cases_table.update()
        .where(quarantine_cases_table.c.id == case_id)
        .values(
            status=QuarantineStatus.RESOLVED.value,
            resolution=resolution.value,
            assigned_to=auditor_id,
            resolved_at=datetime.now(UTC),
            resolution_notes=notes,
        )
    )


# 13. Release Manifests
def insert_release_manifest(session: Session, manifest: ReleaseManifest) -> None:
    session.execute(
        release_manifests_table.insert().values(
            id=manifest.p5_manifest_id,  # Use stable p5_manifest_id as primary key
            p5_manifest_id=manifest.p5_manifest_id,
            release_manifest_version=manifest.release_manifest_version,
            candidate_id=manifest.candidate_id,
            canonical_artifact_id=manifest.canonical_artifact_id,
            experiment_id=manifest.experiment_id,
            track_spec_id=manifest.track_spec_id,
            content_arm_id=manifest.content_arm_id,
            disposition=manifest.disposition.value,
            technical_status=manifest.technical_status,
            commercial_use_status=manifest.commercial_use_status,
            provenance_status=manifest.provenance_status,
            ai_disclosure_required=manifest.ai_disclosure_required,
            rights_risk=manifest.rights_risk,
            policy_status=manifest.policy_status,
            evaluation_id=manifest.evaluation_id,
            evaluation_policy_version=manifest.evaluation_policy_version,
            warning_codes_json=json.dumps(manifest.warning_codes),
            evidence_bundle_id=manifest.evidence_bundle_id,
            manifest_hash=manifest.manifest_hash,
            created_at=manifest.created_at,
        )
    )


def get_release_manifest_by_candidate(session: Session, candidate_id: str) -> ReleaseManifest | None:
    row = session.execute(
        release_manifests_table.select()
        .where(release_manifests_table.c.candidate_id == candidate_id)
        .order_by(release_manifests_table.c.created_at.desc())
    ).mappings().first()
    if not row:
        return None
    return ReleaseManifest(
        p5_manifest_id=row["p5_manifest_id"],
        release_manifest_version=row["release_manifest_version"],
        candidate_id=row["candidate_id"],
        canonical_artifact_id=row["canonical_artifact_id"],
        experiment_id=row["experiment_id"],
        track_spec_id=row["track_spec_id"],
        content_arm_id=row["content_arm_id"],
        disposition=GateDisposition(row["disposition"]),
        technical_status=row["technical_status"],
        commercial_use_status=row["commercial_use_status"],
        provenance_status=row["provenance_status"],
        ai_disclosure_required=row["ai_disclosure_required"],
        rights_risk=row["rights_risk"],
        policy_status=row["policy_status"],
        evaluation_id=row["evaluation_id"],
        evaluation_policy_version=row["evaluation_policy_version"],
        warning_codes=json.loads(row["warning_codes_json"]),
        evidence_bundle_id=row["evidence_bundle_id"],
        manifest_hash=row["manifest_hash"],
        created_at=row["created_at"],
    )


def get_release_manifest_by_id(session: Session, manifest_id: str) -> ReleaseManifest | None:
    row = session.execute(
        release_manifests_table.select().where(release_manifests_table.c.p5_manifest_id == manifest_id)
    ).mappings().first()
    if not row:
        return None
    return ReleaseManifest(
        p5_manifest_id=row["p5_manifest_id"],
        release_manifest_version=row["release_manifest_version"],
        candidate_id=row["candidate_id"],
        canonical_artifact_id=row["canonical_artifact_id"],
        experiment_id=row["experiment_id"],
        track_spec_id=row["track_spec_id"],
        content_arm_id=row["content_arm_id"],
        disposition=GateDisposition(row["disposition"]),
        technical_status=row["technical_status"],
        commercial_use_status=row["commercial_use_status"],
        provenance_status=row["provenance_status"],
        ai_disclosure_required=row["ai_disclosure_required"],
        rights_risk=row["rights_risk"],
        policy_status=row["policy_status"],
        evaluation_id=row["evaluation_id"],
        evaluation_policy_version=row["evaluation_policy_version"],
        warning_codes=json.loads(row["warning_codes_json"]),
        evidence_bundle_id=row["evidence_bundle_id"],
        manifest_hash=row["manifest_hash"],
        created_at=row["created_at"],
    )
