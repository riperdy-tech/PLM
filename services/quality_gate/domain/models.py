"""Domain models for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .enums import (
    CheckSeverity,
    CheckStatus,
    EvaluationStatus,
    GateCategory,
    GateDisposition,
    QuarantineStatus,
    ResolutionCode,
)
from .ids import (
    check_id,
    evaluation_id,
    evidence_id,
    gate_decision_id,
    p5_manifest_id,
    quarantine_case_id,
    risk_finding_id,
    snapshot_id,
)

__all__ = [
    "CandidateEvaluation",
    "EvaluationSnapshot",
    "EvidenceItem",
    "GateDecision",
    "LyricCheck",
    "MusicalCheck",
    "PolicyCheck",
    "ProvenanceCheck",
    "QuarantineCase",
    "ReleaseManifest",
    "RightsCheck",
    "RiskFinding",
    "TechnicalCheck",
]


@dataclass(frozen=True)
class EvaluationSnapshot:
    """Immutable snapshot of the facts evaluated by P5 (P5 §10, §83)."""

    id: str
    evaluation_id: str
    candidate_id: str
    artifact_id: str
    candidate_snapshot: dict[str, Any]
    artifact_snapshot: dict[str, Any]
    provider_snapshot: dict[str, Any]
    license_snapshot: dict[str, Any]
    p3_snapshot_ref: str
    p4_snapshot_ref: str
    policy_snapshot_ref: str
    catalog_snapshot_ref: str
    snapshot_hash: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        candidate_id: str,
        artifact_id: str,
        candidate_snapshot: dict[str, Any],
        artifact_snapshot: dict[str, Any],
        provider_snapshot: dict[str, Any],
        license_snapshot: dict[str, Any],
        p3_snapshot_ref: str = "",
        p4_snapshot_ref: str = "",
        policy_snapshot_ref: str = "",
        catalog_snapshot_ref: str = "",
    ) -> EvaluationSnapshot:
        content = json.dumps(
            {
                "candidate": candidate_snapshot,
                "artifact": artifact_snapshot,
                "provider": provider_snapshot,
                "license": license_snapshot,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        s_hash = hashlib.sha256(content).hexdigest()
        sid = snapshot_id(evaluation_id_str)
        return cls(
            id=sid,
            evaluation_id=evaluation_id_str,
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            candidate_snapshot=candidate_snapshot,
            artifact_snapshot=artifact_snapshot,
            provider_snapshot=provider_snapshot,
            license_snapshot=license_snapshot,
            p3_snapshot_ref=p3_snapshot_ref,
            p4_snapshot_ref=p4_snapshot_ref,
            policy_snapshot_ref=policy_snapshot_ref,
            catalog_snapshot_ref=catalog_snapshot_ref,
            snapshot_hash=s_hash,
        )


@dataclass(frozen=True)
class TechnicalCheck:
    """A deterministic technical/media validation (P5 §11-§15, §84)."""

    id: str
    evaluation_id: str
    check_type: str
    status: CheckStatus
    severity: CheckSeverity
    measured_value: Any
    expected_value: Any
    unit: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    tool_name: str = "internal_audio_validator"
    tool_version: str = "1.0"
    config_version: str = "1.0"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        check_type: str,
        status: CheckStatus,
        severity: CheckSeverity,
        measured_value: Any,
        expected_value: Any,
        unit: str,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
    ) -> TechnicalCheck:
        cid = check_id(evaluation_id_str, "tech", check_type)
        return cls(
            id=cid,
            evaluation_id=evaluation_id_str,
            check_type=check_type,
            status=status,
            severity=severity,
            measured_value=measured_value,
            expected_value=expected_value,
            unit=unit,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )


@dataclass(frozen=True)
class MusicalCheck:
    """A musical/audio analysis signal (P5 §15-§20, §85)."""

    id: str
    evaluation_id: str
    check_type: str
    score: float
    confidence: float
    severity: CheckSeverity
    method: str
    model_version: str
    segment_data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        check_type: str,
        score: float,
        confidence: float,
        severity: CheckSeverity,
        method: str = "heuristic_analyzer",
        model_version: str = "1.0",
        segment_data: dict[str, Any] | None = None,
    ) -> MusicalCheck:
        cid = check_id(evaluation_id_str, "music", check_type)
        return cls(
            id=cid,
            evaluation_id=evaluation_id_str,
            check_type=check_type,
            score=score,
            confidence=confidence,
            severity=severity,
            method=method,
            model_version=model_version,
            segment_data=segment_data or {},
        )


@dataclass(frozen=True)
class LyricCheck:
    """A lyric integrity check (P5 §21-§25)."""

    id: str
    evaluation_id: str
    check_type: str
    status: CheckStatus
    severity: CheckSeverity
    details: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        check_type: str,
        status: CheckStatus,
        severity: CheckSeverity,
        details: dict[str, Any] | None = None,
    ) -> LyricCheck:
        cid = check_id(evaluation_id_str, "lyric", check_type)
        return cls(
            id=cid,
            evaluation_id=evaluation_id_str,
            check_type=check_type,
            status=status,
            severity=severity,
            details=details or {},
        )


@dataclass(frozen=True)
class ProvenanceCheck:
    """A provenance integrity check (P5 §26-§30)."""

    id: str
    evaluation_id: str
    check_type: str
    status: CheckStatus
    severity: CheckSeverity
    details: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        check_type: str,
        status: CheckStatus,
        severity: CheckSeverity,
        details: dict[str, Any] | None = None,
    ) -> ProvenanceCheck:
        cid = check_id(evaluation_id_str, "prov", check_type)
        return cls(
            id=cid,
            evaluation_id=evaluation_id_str,
            check_type=check_type,
            status=status,
            severity=severity,
            details=details or {},
        )


@dataclass(frozen=True)
class RightsCheck:
    """A rights/license check (P5 §31-§35, §86)."""

    id: str
    evaluation_id: str
    check_type: str
    status: CheckStatus
    risk_level: str
    confidence: float
    source_type: str
    source_ref: str
    matched_reference_id: str | None = None
    similarity_score: float = 0.0
    evidence_data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        check_type: str,
        status: CheckStatus,
        risk_level: str,
        confidence: float,
        source_type: str,
        source_ref: str,
        matched_reference_id: str | None = None,
        similarity_score: float = 0.0,
        evidence_data: dict[str, Any] | None = None,
    ) -> RightsCheck:
        cid = check_id(evaluation_id_str, "rights", check_type)
        return cls(
            id=cid,
            evaluation_id=evaluation_id_str,
            check_type=check_type,
            status=status,
            risk_level=risk_level,
            confidence=confidence,
            source_type=source_type,
            source_ref=source_ref,
            matched_reference_id=matched_reference_id,
            similarity_score=similarity_score,
            evidence_data=evidence_data or {},
        )


@dataclass(frozen=True)
class PolicyCheck:
    """An internal or platform policy check (P5 §36-§40, §87)."""

    id: str
    evaluation_id: str
    policy_namespace: str
    rule_id: str
    rule_version: str
    status: CheckStatus
    severity: CheckSeverity
    evidence_data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        policy_namespace: str,
        rule_id: str,
        rule_version: str,
        status: CheckStatus,
        severity: CheckSeverity,
        evidence_data: dict[str, Any] | None = None,
    ) -> PolicyCheck:
        cid = check_id(evaluation_id_str, policy_namespace, rule_id)
        return cls(
            id=cid,
            evaluation_id=evaluation_id_str,
            policy_namespace=policy_namespace,
            rule_id=rule_id,
            rule_version=rule_version,
            status=status,
            severity=severity,
            evidence_data=evidence_data or {},
        )


@dataclass(frozen=True)
class EvidenceItem:
    """A piece of supporting evidence for evaluation results (P5 §89)."""

    id: str
    evaluation_id: str
    evidence_type: str
    source: str
    source_reference: str
    content_hash: str
    summary: str
    raw_pointer: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        evidence_type: str,
        source: str,
        source_reference: str,
        content_hash: str,
        summary: str,
        raw_pointer: str | None = None,
    ) -> EvidenceItem:
        eid = evidence_id(evaluation_id_str, evidence_type, content_hash)
        return cls(
            id=eid,
            evaluation_id=evaluation_id_str,
            evidence_type=evidence_type,
            source=source,
            source_reference=source_reference,
            content_hash=content_hash,
            summary=summary,
            raw_pointer=raw_pointer,
        )


@dataclass(frozen=True)
class RiskFinding:
    """A finding indicating risk or defect (P5 §45, §88)."""

    id: str
    evaluation_id: str
    finding_code: str
    category: GateCategory
    severity: CheckSeverity
    status: str
    confidence: float
    message: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    evidence_item_ids: list[str] = field(default_factory=list)
    blocking: bool = False
    repairability: str = "UNKNOWN"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        finding_code: str,
        category: GateCategory,
        severity: CheckSeverity,
        status: str,
        confidence: float,
        message: str,
        blocking: bool = False,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        evidence_item_ids: list[str] | None = None,
        repairability: str = "UNKNOWN",
    ) -> RiskFinding:
        fid = risk_finding_id(evaluation_id_str, finding_code)
        return cls(
            id=fid,
            evaluation_id=evaluation_id_str,
            finding_code=finding_code,
            category=category,
            severity=severity,
            status=status,
            confidence=confidence,
            message=message,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            evidence_item_ids=evidence_item_ids or [],
            blocking=blocking,
            repairability=repairability,
        )


@dataclass(frozen=True)
class GateDecision:
    """Final deterministic release gate decision (P5 §51, §90)."""

    id: str
    evaluation_id: str
    disposition: GateDisposition
    gate_results: dict[str, Any]
    blocking_finding_ids: list[str]
    warning_finding_ids: list[str]
    decision_version: str
    decision_hash: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        evaluation_id_str: str,
        disposition: GateDisposition,
        gate_results: dict[str, Any],
        blocking_finding_ids: list[str],
        warning_finding_ids: list[str],
        decision_version: str = "v1.0",
    ) -> GateDecision:
        content = json.dumps(
            {
                "disposition": disposition.value,
                "gate_results": gate_results,
                "blocking": sorted(blocking_finding_ids),
                "warnings": sorted(warning_finding_ids),
                "version": decision_version,
            },
            sort_keys=True,
        ).encode("utf-8")
        d_hash = hashlib.sha256(content).hexdigest()
        did = gate_decision_id(evaluation_id_str, decision_version)
        return cls(
            id=did,
            evaluation_id=evaluation_id_str,
            disposition=disposition,
            gate_results=gate_results,
            blocking_finding_ids=blocking_finding_ids,
            warning_finding_ids=warning_finding_ids,
            decision_version=decision_version,
            decision_hash=d_hash,
        )


@dataclass(frozen=True)
class QuarantineCase:
    """A case requiring review before release (P5 §49, §91)."""

    id: str
    candidate_id: str
    evaluation_id: str
    reason_codes: list[str]
    priority: str
    status: QuarantineStatus
    assigned_to: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolution: ResolutionCode | None = None
    resolution_notes: str | None = None

    @classmethod
    def create(
        cls,
        candidate_id: str,
        evaluation_id_str: str,
        reason_codes: list[str],
        priority: str = "NORMAL",
    ) -> QuarantineCase:
        qid = quarantine_case_id(candidate_id, evaluation_id_str)
        return cls(
            id=qid,
            candidate_id=candidate_id,
            evaluation_id=evaluation_id_str,
            reason_codes=reason_codes,
            priority=priority,
            status=QuarantineStatus.OPEN,
        )

    def resolve(
        self,
        resolution: ResolutionCode,
        auditor_id: str,
        notes: str = "",
    ) -> QuarantineCase:
        return QuarantineCase(
            id=self.id,
            candidate_id=self.candidate_id,
            evaluation_id=self.evaluation_id,
            reason_codes=self.reason_codes,
            priority=self.priority,
            status=QuarantineStatus.RESOLVED,
            assigned_to=auditor_id,
            created_at=self.created_at,
            resolved_at=datetime.now(UTC),
            resolution=resolution,
            resolution_notes=notes,
        )


@dataclass(frozen=True)
class ReleaseManifest:
    """Downstream package contract for P6 (Seam C5, Ruling R7, R8, P5 §77, §92)."""

    p5_manifest_id: str
    release_manifest_version: str
    candidate_id: str
    canonical_artifact_id: str
    experiment_id: str
    track_spec_id: str
    content_arm_id: str
    disposition: GateDisposition
    technical_status: str
    commercial_use_status: str
    provenance_status: str
    ai_disclosure_required: bool
    rights_risk: str
    policy_status: str
    evaluation_id: str
    evaluation_policy_version: str
    warning_codes: list[str]
    evidence_bundle_id: str
    manifest_hash: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        candidate_id: str,
        canonical_artifact_id: str,
        experiment_id: str,
        track_spec_id: str,
        content_arm_id: str,
        disposition: GateDisposition,
        technical_status: str,
        commercial_use_status: str,
        provenance_status: str,
        ai_disclosure_required: bool,
        rights_risk: str,
        policy_status: str,
        evaluation_id_str: str,
        evaluation_policy_version: str,
        warning_codes: list[str],
        evidence_bundle_id: str,
        version: str = "1.0",
    ) -> ReleaseManifest:
        content = json.dumps(
            {
                "candidate_id": candidate_id,
                "canonical_artifact_id": canonical_artifact_id,
                "experiment_id": experiment_id,
                "track_spec_id": track_spec_id,
                "content_arm_id": content_arm_id,
                "disposition": disposition.value,
                "technical_status": technical_status,
                "commercial_use_status": commercial_use_status,
                "provenance_status": provenance_status,
                "ai_disclosure_required": ai_disclosure_required,
                "rights_risk": rights_risk,
                "policy_status": policy_status,
                "evaluation_id": evaluation_id_str,
                "evaluation_policy_version": evaluation_policy_version,
                "warning_codes": sorted(warning_codes),
                "evidence_bundle_id": evidence_bundle_id,
                "version": version,
            },
            sort_keys=True,
        ).encode("utf-8")
        m_hash = hashlib.sha256(content).hexdigest()
        mid = p5_manifest_id(candidate_id, evaluation_id_str, version)
        return cls(
            p5_manifest_id=mid,
            release_manifest_version=version,
            candidate_id=candidate_id,
            canonical_artifact_id=canonical_artifact_id,
            experiment_id=experiment_id,
            track_spec_id=track_spec_id,
            content_arm_id=content_arm_id,
            disposition=disposition,
            technical_status=technical_status,
            commercial_use_status=commercial_use_status,
            provenance_status=provenance_status,
            ai_disclosure_required=ai_disclosure_required,
            rights_risk=rights_risk,
            policy_status=policy_status,
            evaluation_id=evaluation_id_str,
            evaluation_policy_version=evaluation_policy_version,
            warning_codes=warning_codes,
            evidence_bundle_id=evidence_bundle_id,
            manifest_hash=m_hash,
        )


@dataclass
class CandidateEvaluation:
    """Aggregate root for candidate evaluation in P5 (P5 §6.1, §82)."""

    id: str
    candidate_id: str
    artifact_id: str
    evaluation_policy_version: str
    trigger: str
    status: EvaluationStatus
    disposition: GateDisposition | None = None
    technical_status: str = "PENDING"
    quality_status: str = "PENDING"
    provenance_status: str = "PENDING"
    rights_status: str = "PENDING"
    policy_status: str = "PENDING"
    composite_quality_score: float | None = None
    composite_quality_confidence: float | None = None
    snapshot: EvaluationSnapshot | None = None
    technical_checks: list[TechnicalCheck] = field(default_factory=list)
    musical_checks: list[MusicalCheck] = field(default_factory=list)
    lyric_checks: list[LyricCheck] = field(default_factory=list)
    provenance_checks: list[ProvenanceCheck] = field(default_factory=list)
    rights_checks: list[RightsCheck] = field(default_factory=list)
    policy_checks: list[PolicyCheck] = field(default_factory=list)
    risk_findings: list[RiskFinding] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    gate_decision: GateDecision | None = None
    quarantine_case: QuarantineCase | None = None
    release_manifest: ReleaseManifest | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        candidate_id: str,
        artifact_id: str,
        evaluation_policy_version: str = "p5-v1",
        trigger: str = "candidate_ready",
    ) -> CandidateEvaluation:
        eid = evaluation_id(candidate_id, evaluation_policy_version, trigger)
        return cls(
            id=eid,
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            evaluation_policy_version=evaluation_policy_version,
            trigger=trigger,
            status=EvaluationStatus.PENDING,
        )
