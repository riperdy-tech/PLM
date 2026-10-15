"""Gate policy engine for Phase P5 (P5 §45-§51, §90, R8; rights detectors R4).

Evaluates hard release gates vs soft signals and renders deterministic
GateDecision, QuarantineCase, and downstream ReleaseManifest.

Disposition (fail closed):

* any blocking finding: ``REJECTED``;
* otherwise any hold from the rights, provenance, policy or detector checks (the codes
  in :data:`~quality_gate.detectors.reason_codes.P5_HOLD_REASON_CODES`), or commercial
  use not declared eligible: ``QUARANTINED``, with one hold per reason code raised into
  the platform exception queue on persistence;
* otherwise ``PASSED_WITH_WARNINGS`` or ``PASSED``.

A detector that is not configured, cannot read its source or cannot read the audio
returns ``UNKNOWN``, which is a hold: an unrun check never clears a candidate.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from plm_contracts import ProvenanceStatus
from plm_contracts.seams.c4 import CandidateReadyPayload
from plm_platform.clock import Clock

from ..detectors.fingerprint import AudioFingerprint
from ..detectors.outcome import DetectorResult
from ..detectors.reason_codes import P5_HOLD_REASON_CODES
from ..detectors.suite import RightsDetectors, detect_audio, detect_lyrics
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
from ..evaluators.lyrics import evaluate_lyric_similarity, evaluate_lyrics
from ..evaluators.musical import evaluate_musical_qualities
from ..evaluators.policy import screen_policy_and_risk
from ..evaluators.rights import (
    evaluate_audio_fingerprint,
    map_p28_provenance,
    validate_p28_provenance,
    validate_provenance_and_rights,
)
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
        candidate_fingerprint: AudioFingerprint | None = None,
        detector_results: tuple[DetectorResult, ...] = (),
    ) -> None:
        self.evaluation = evaluation
        self.snapshot = snapshot
        self.decision = decision
        self.quarantine_case = quarantine_case
        self.release_manifest = release_manifest
        self.candidate_fingerprint = candidate_fingerprint
        """Persisted with the evaluation; joins the internal catalog once a manifest exists."""
        self.detector_results = detector_results

    @property
    def disposition(self) -> GateDisposition:
        return self.decision.disposition


def run_candidate_evaluation(
    *,
    event: CandidateReadyPayload,
    audio_path: str | Path,
    clock: Clock,
    detectors: RightsDetectors | None = None,
    evaluation_policy_version: str = "p5-v1",
    trigger: str = "candidate_ready",
) -> GateEngineResult:
    """Evaluate one candidate described by its Seam C4 event (ADR-0027, ADR-0034).

    Every candidate fact comes from the event: lineage, declared duration range, vocal mode,
    genre tags, prompt, lyrics and the declared rights. ``evaluation_policy_version`` and
    ``trigger`` are P5's own settings. ``audio_path`` is where P5 can read the artifact.
    ``clock`` stamps the start instant shared by every record of this evaluation, and the
    finish instant.

    ``detectors`` injects the internal catalog, reference audio set, lyric corpus,
    protected-artist policy and thresholds. ``None`` means none is configured: every
    detector is ``UNKNOWN`` and the candidate is quarantined, never cleared.

    All twelve P28 provenance facts come from the event (C4 3.0, ADR-0035); a fact P4
    declares as unknown quarantines with ``INCOMPLETE_P28_PROVENANCE``.
    """
    now = clock.now()
    candidate_id = event.candidate_id
    artifact_id = event.artifact_id
    track_spec_id = event.track_spec_id
    experiment_id = event.lineage.experiment_id
    content_arm_id = event.lineage.content_arm_id
    duration_range = event.requested_duration_range
    vocal_mode = event.vocal_mode
    lyrics_text = event.lyrics_text
    prompt_text = event.prompt_text
    genre_tags = list(event.genre_tags)
    suite = detectors if detectors is not None else RightsDetectors.unconfigured()

    # 0. Rights detectors read their sources first, so the snapshot names what was read.
    audio_detection = detect_audio(audio_path, candidate_id=candidate_id, detectors=suite)
    lyric_result = detect_lyrics(lyrics_text, vocal_mode=vocal_mode, detectors=suite)
    detector_results = (*audio_detection.results, lyric_result)
    catalog_ref = "det:" + hashlib.sha256(
        "|".join(
            f"{r.detector}/{r.source}={r.source_snapshot_ref or 'unread'}"
            for r in detector_results
        ).encode("utf-8")
    ).hexdigest()

    # 1. Initialize Aggregate Root
    evaluation = CandidateEvaluation.create(
        candidate_id=candidate_id,
        artifact_id=artifact_id,
        evaluation_policy_version=evaluation_policy_version,
        trigger=trigger,
        now=now,
    )

    # 2. Immutable Fact Snapshot: the C4 event as received, lineage included.
    snapshot = EvaluationSnapshot.create(
        evaluation_id_str=evaluation.id,
        candidate_id=candidate_id,
        artifact_id=artifact_id,
        candidate_snapshot={
            "track_spec_id": track_spec_id,
            "lineage": event.lineage.model_dump(mode="json"),
            "experiment_id": experiment_id,
            "content_arm_id": content_arm_id,
            "vocal_mode": vocal_mode,
            "prompt_text": prompt_text,
            "genre_tags": genre_tags,
            "generation_provenance": event.generation_provenance.model_dump(mode="json"),
        },
        artifact_snapshot={
            "artifact_uri": event.artifact_uri,
            "artifact_sha256": event.artifact_sha256,
            "requested_duration_seconds": event.requested_duration_seconds,
            "requested_duration_range": duration_range.model_dump(mode="json"),
            "measured_audio": event.measured_audio.model_dump(mode="json"),
        },
        provider_snapshot={"provider_id": event.provider_id, "model_id": event.model_id},
        license_snapshot=event.rights.model_dump(mode="json") if event.rights else {},
        p3_snapshot_ref=track_spec_id,
        p4_snapshot_ref=candidate_id,
        policy_snapshot_ref=evaluation_policy_version,
        catalog_snapshot_ref=catalog_ref,
        now=now,
    )
    evaluation.snapshot = snapshot

    # 3. Evaluators Execution
    tech_res = validate_technical_audio(
        evaluation_id=evaluation.id,
        audio_path=audio_path,
        allowed_duration=duration_range,
        now=now,
    )
    evaluation.technical_checks.extend(tech_res.checks)
    evaluation.risk_findings.extend(tech_res.findings)
    evaluation.evidence_items.extend(tech_res.evidence)

    musical_res = evaluate_musical_qualities(
        evaluation_id=evaluation.id,
        audio_path=audio_path,
        now=now,
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
        now=now,
    )
    evaluation.lyric_checks.extend(lyric_res.checks)
    evaluation.risk_findings.extend(lyric_res.findings)

    rights_res = validate_provenance_and_rights(
        evaluation_id=evaluation.id,
        rights=event.rights,
        now=now,
    )
    evaluation.rights_checks.extend(rights_res.rights_checks)
    evaluation.provenance_checks.extend(rights_res.provenance_checks)
    evaluation.risk_findings.extend(rights_res.findings)
    evaluation.evidence_items.extend(rights_res.evidence)

    p28_res = validate_p28_provenance(
        evaluation.id, map_p28_provenance(event), now=now
    )
    evaluation.provenance_checks.extend(p28_res.checks)
    evaluation.risk_findings.extend(p28_res.findings)
    evaluation.evidence_items.extend(p28_res.evidence)

    audio_res = evaluate_audio_fingerprint(evaluation.id, audio_detection, now=now)
    lyric_sim_res = evaluate_lyric_similarity(evaluation.id, lyric_result, now=now)
    for detector_res in (audio_res, lyric_sim_res):
        evaluation.rights_checks.extend(detector_res.checks)
        evaluation.risk_findings.extend(detector_res.findings)
        evaluation.evidence_items.extend(detector_res.evidence)

    policy_res = screen_policy_and_risk(
        evaluation_id=evaluation.id,
        prompt_text=prompt_text,
        genre_tags=genre_tags,
        is_ai_generated=True,
        policy_version=evaluation_policy_version,
        now=now,
        lyrics_text=lyrics_text,
        protected_artists=suite.protected_artists,
    )
    evaluation.policy_checks.extend(policy_res.checks)
    evaluation.risk_findings.extend(policy_res.findings)

    # 4. Status determinations
    has_blocking_tech = any(
        f.blocking for f in tech_res.findings
    )
    evaluation.technical_status = "FAILED" if has_blocking_tech else "PASSED"
    evaluation.quality_status = "PASSED" if (musical_res.composite_score >= 0.5) else "MARGINAL"
    if rights_res.provenance_status is ProvenanceStatus.MISSING:
        provenance_status = ProvenanceStatus.MISSING
    elif p28_res.status is not ProvenanceStatus.COMPLETE:
        provenance_status = ProvenanceStatus.PARTIAL
    else:
        provenance_status = rights_res.provenance_status
    evaluation.provenance_status = provenance_status.value
    evaluation.rights_status = rights_res.commercial_use_status.value
    evaluation.policy_status = policy_res.policy_status

    # 5. Deterministic Gate Decision logic (P5 §45-§51)
    blocking_findings = [f for f in evaluation.risk_findings if f.blocking]
    warning_findings = [f for f in evaluation.risk_findings if not f.blocking and f.severity in (CheckSeverity.LOW, CheckSeverity.MEDIUM, CheckSeverity.HIGH)]

    hold_codes = {
        f.finding_code
        for f in evaluation.risk_findings
        if not f.blocking and f.finding_code in P5_HOLD_REASON_CODES
    }

    quarantine_case: QuarantineCase | None = None
    release_manifest: ReleaseManifest | None = None

    if blocking_findings:
        disposition = GateDisposition.REJECTED
    elif hold_codes or rights_res.requires_quarantine:
        disposition = GateDisposition.QUARANTINED
        reasons = sorted(
            hold_codes
            | {
                f.finding_code
                for f in evaluation.risk_findings
                if not f.blocking and f.severity == CheckSeverity.HIGH
            }
        )
        quarantine_case = QuarantineCase.create(
            candidate_id=candidate_id,
            evaluation_id_str=evaluation.id,
            reason_codes=reasons or ["QUARANTINE_REVIEW_REQUIRED"],
            now=now,
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
        now=now,
    )
    evaluation.gate_decision = decision
    evaluation.disposition = disposition
    evaluation.quarantine_case = quarantine_case

    # 6. Release Manifest Generation (Only if passed or passed with warnings)
    if disposition in (GateDisposition.PASSED, GateDisposition.PASSED_WITH_WARNINGS):
        # The rights evaluator always records the declaration (or its absence).
        primary_evidence_id = evaluation.evidence_items[0].id
        release_manifest = build_release_manifest(
            candidate_id=candidate_id,
            canonical_artifact_id=artifact_id,
            artifact_uri=event.artifact_uri,
            artifact_sha256=event.artifact_sha256,
            measured_duration_seconds=event.measured_audio.duration_seconds,
            track_spec_id=track_spec_id,
            lineage=event.lineage,
            disposition=disposition,
            override=None,
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
            now=now,
        )
        evaluation.release_manifest = release_manifest

    evaluation.status = EvaluationStatus.COMPLETED
    evaluation.finished_at = clock.now()

    return GateEngineResult(
        evaluation=evaluation,
        snapshot=snapshot,
        decision=decision,
        quarantine_case=quarantine_case,
        release_manifest=release_manifest,
        candidate_fingerprint=audio_detection.fingerprint,
        detector_results=detector_results,
    )
