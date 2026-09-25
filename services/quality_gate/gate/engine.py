"""Gate policy engine for Phase P5 (P5 §45-§51, §90, R8).

Evaluates hard release gates vs soft signals and renders deterministic
GateDecision, QuarantineCase, and downstream ReleaseManifest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain.enums import (
    CheckSeverity,
    EvaluationStatus,
    GateDisposition,
)
from ..domain.models import (
    CandidateEvaluation,
    EvaluationSnapshot,
    GateDecision,
    QuarantineCase,
    ReleaseManifest,
)
from ..evaluators.lyrics import evaluate_lyrics
from ..evaluators.musical import evaluate_musical_qualities
from ..evaluators.policy import screen_policy_and_risk
from ..evaluators.rights import validate_provenance_and_rights
from ..evaluators.technical import validate_technical_audio
from .manifest import build_release_manifest

__all__ = ["GateEngineResult", "run_candidate_evaluation"]


class GateEngineResult:
    """Complete evaluation outcome containing all domain objects."""

    def __init__(
        self,
        evaluation: CandidateEvaluation,
        snapshot: EvaluationSnapshot,
        decision: GateDecision,
        quarantine_case: QuarantineCase | None = None,
        release_manifest: ReleaseManifest | None = None,
    ) -> None:
        self.evaluation = evaluation
        self.snapshot = snapshot
        self.decision = decision
        self.quarantine_case = quarantine_case
        self.release_manifest = release_manifest

    @property
    def disposition(self) -> GateDisposition:
        return self.decision.disposition


def run_candidate_evaluation(
    *,
    candidate_id: str,
    artifact_id: str,
    audio_path: str | Path,
    expected_duration_seconds: float,
    track_spec_id: str,
    experiment_id: str,
    content_arm_id: str,
    vocal_mode: str = "instrumental",
    lyrics_text: str | None = None,
    prompt_text: str | None = None,
    genre_tags: list[str] | None = None,
    rights_snapshot: dict[str, Any] | None = None,
    provenance_metadata: dict[str, Any] | None = None,
    evaluation_policy_version: str = "p5-v1",
    trigger: str = "candidate_ready",
) -> GateEngineResult:
    """Execute full evaluation pipeline against a candidate."""
    # 1. Initialize Aggregate Root
    evaluation = CandidateEvaluation.create(
        candidate_id=candidate_id,
        artifact_id=artifact_id,
        evaluation_policy_version=evaluation_policy_version,
        trigger=trigger,
    )

    # 2. Immutable Fact Snapshot
    snapshot = EvaluationSnapshot.create(
        evaluation_id_str=evaluation.id,
        candidate_id=candidate_id,
        artifact_id=artifact_id,
        candidate_snapshot={
            "track_spec_id": track_spec_id,
            "experiment_id": experiment_id,
            "content_arm_id": content_arm_id,
            "vocal_mode": vocal_mode,
            "prompt_text": prompt_text,
            "genre_tags": genre_tags or [],
        },
        artifact_snapshot={
            "audio_path": str(audio_path),
            "expected_duration_seconds": expected_duration_seconds,
        },
        provider_snapshot=provenance_metadata or {},
        license_snapshot=rights_snapshot or {},
        p3_snapshot_ref=track_spec_id,
        p4_snapshot_ref=candidate_id,
        policy_snapshot_ref=evaluation_policy_version,
    )
    evaluation.snapshot = snapshot

    # 3. Evaluators Execution
    tech_res = validate_technical_audio(
        evaluation_id=evaluation.id,
        audio_path=audio_path,
        expected_duration_seconds=expected_duration_seconds,
    )
    evaluation.technical_checks.extend(tech_res.checks)
    evaluation.risk_findings.extend(tech_res.findings)
    evaluation.evidence_items.extend(tech_res.evidence)

    musical_res = evaluate_musical_qualities(
        evaluation_id=evaluation.id,
        audio_path=audio_path,
    )
    evaluation.musical_checks.extend(musical_res.checks)
    evaluation.risk_findings.extend(musical_res.findings)
    evaluation.composite_quality_score = musical_res.composite_score
    evaluation.composite_quality_confidence = musical_res.confidence

    lyric_res = evaluate_lyrics(
        evaluation_id=evaluation.id,
        vocal_mode=vocal_mode,
        lyrics_text=lyrics_text,
        prompt_text=prompt_text,
    )
    evaluation.lyric_checks.extend(lyric_res.checks)
    evaluation.risk_findings.extend(lyric_res.findings)

    rights_res = validate_provenance_and_rights(
        evaluation_id=evaluation.id,
        rights_snapshot=rights_snapshot,
        provenance_metadata=provenance_metadata,
    )
    evaluation.rights_checks.extend(rights_res.rights_checks)
    evaluation.provenance_checks.extend(rights_res.provenance_checks)
    evaluation.risk_findings.extend(rights_res.findings)
    evaluation.evidence_items.extend(rights_res.evidence)

    policy_res = screen_policy_and_risk(
        evaluation_id=evaluation.id,
        prompt_text=prompt_text,
        genre_tags=genre_tags,
        is_ai_generated=True,
        policy_version=evaluation_policy_version,
    )
    evaluation.policy_checks.extend(policy_res.checks)
    evaluation.risk_findings.extend(policy_res.findings)

    # 4. Status determinations
    has_blocking_tech = any(
        f.blocking for f in tech_res.findings
    )
    evaluation.technical_status = "FAILED" if has_blocking_tech else "PASSED"
    evaluation.quality_status = "PASSED" if (musical_res.composite_score >= 0.5) else "MARGINAL"
    evaluation.provenance_status = rights_res.provenance_status
    evaluation.rights_status = rights_res.commercial_use_status
    evaluation.policy_status = policy_res.policy_status

    # 5. Deterministic Gate Decision logic (P5 §45-§51)
    blocking_findings = [f for f in evaluation.risk_findings if f.blocking]
    warning_findings = [f for f in evaluation.risk_findings if not f.blocking and f.severity in (CheckSeverity.LOW, CheckSeverity.MEDIUM, CheckSeverity.HIGH)]

    quarantine_codes = {"UNKNOWN_COMMERCIAL_LICENSE", "INCOMPLETE_R6_PROVENANCE"}
    quarantine_needed = any(f.finding_code in quarantine_codes for f in evaluation.risk_findings)

    quarantine_case: QuarantineCase | None = None
    release_manifest: ReleaseManifest | None = None

    if blocking_findings:
        disposition = GateDisposition.REJECTED
    elif quarantine_needed or rights_res.requires_quarantine:
        disposition = GateDisposition.QUARANTINED
        reasons = [f.finding_code for f in evaluation.risk_findings if f.finding_code in quarantine_codes or f.severity == CheckSeverity.HIGH]
        quarantine_case = QuarantineCase.create(
            candidate_id=candidate_id,
            evaluation_id_str=evaluation.id,
            reason_codes=reasons or ["QUARANTINE_REVIEW_REQUIRED"],
        )
    elif warning_findings:
        disposition = GateDisposition.PASSED_WITH_WARNINGS
    else:
        disposition = GateDisposition.PASSED

    gate_results = {
        "technical_status": evaluation.technical_status,
        "quality_status": evaluation.quality_status,
        "provenance_status": evaluation.provenance_status,
        "rights_status": evaluation.rights_status,
        "policy_status": evaluation.policy_status,
        "disposition": disposition.value,
        "total_checks": (
            len(evaluation.technical_checks)
            + len(evaluation.musical_checks)
            + len(evaluation.lyric_checks)
            + len(evaluation.provenance_checks)
            + len(evaluation.rights_checks)
            + len(evaluation.policy_checks)
        ),
        "total_findings": len(evaluation.risk_findings),
    }

    decision = GateDecision.create(
        evaluation_id_str=evaluation.id,
        disposition=disposition,
        gate_results=gate_results,
        blocking_finding_ids=[f.id for f in blocking_findings],
        warning_finding_ids=[f.id for f in warning_findings],
        decision_version="v1.0",
    )
    evaluation.gate_decision = decision
    evaluation.disposition = disposition
    evaluation.quarantine_case = quarantine_case

    # 6. Release Manifest Generation (Only if passed or passed with warnings)
    if disposition in (GateDisposition.PASSED, GateDisposition.PASSED_WITH_WARNINGS):
        primary_evidence_id = evaluation.evidence_items[0].id if evaluation.evidence_items else "no_evidence"
        release_manifest = build_release_manifest(
            candidate_id=candidate_id,
            canonical_artifact_id=artifact_id,
            experiment_id=experiment_id,
            track_spec_id=track_spec_id,
            content_arm_id=content_arm_id,
            disposition=disposition,
            technical_status=evaluation.technical_status,
            commercial_use_status=evaluation.rights_status,
            provenance_status=evaluation.provenance_status,
            ai_disclosure_required=policy_res.ai_disclosure_required,
            rights_risk=rights_res.rights_risk,
            policy_status=evaluation.policy_status,
            evaluation_id_str=evaluation.id,
            evaluation_policy_version=evaluation_policy_version,
            warning_codes=[f.finding_code for f in warning_findings],
            evidence_bundle_id=primary_evidence_id,
        )
        evaluation.release_manifest = release_manifest

    evaluation.status = EvaluationStatus.COMPLETED
    evaluation.finished_at = datetime.now(UTC)

    return GateEngineResult(
        evaluation=evaluation,
        snapshot=snapshot,
        decision=decision,
        quarantine_case=quarantine_case,
        release_manifest=release_manifest,
    )
