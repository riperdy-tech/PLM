"""Provenance, rights and audio-fingerprint validation for Phase P5 (P5 §26-§35, §40, §86,
architecture PART XV P28/P29, Ruling R6, ADR-0027, ADR-0034).

Three checks, all fail-closed:

* **Commercial use** (:func:`validate_provenance_and_rights`) judges the rights snapshot
  P4's provider adapter declared for the producing attempt.
  - No snapshot, or a declared ``unknown``: ``UNKNOWN_COMMERCIAL_LICENSE`` (reviewable:
    the license evidence can be recovered and attached).
  - A declared ``conditional`` or ``expired_or_unverified`` license:
    ``COMMERCIAL_LICENSE_UNCERTAIN`` (P29 hard stop: a license exists but its terms need
    a determination outside PLM).
  - A free/trial tier or a declared ``ineligible``: rejected.
  - A plan-tier label can only take eligibility away, never grant it.
* **P28 provenance** (:func:`validate_p28_provenance`): all twelve P28 fields are required
  for clearance. They are mapped from the C4 event, its rights snapshot and the
  generation facts P4 declares (:class:`GenerationProvenanceFacts`); nothing is
  defaulted. A missing field is ``INCOMPLETE_R6_PROVENANCE`` (the rights snapshot is
  absent) or ``INCOMPLETE_P28_PROVENANCE`` (any other field). A declared source input with
  no license reference is ``SAMPLE_PROVENANCE_UNCERTAIN``, or
  ``UNAUTHORIZED_VOICE_CLONE_DETECTED`` for a voice input.
* **Audio fingerprint** (:func:`evaluate_audio_fingerprint`): the candidate against the
  internal catalog and the reference set. MATCH against a reference is
  ``REFERENCE_AUDIO_MATCH`` (hard stop); against P5's own released catalog,
  ``INTERNAL_CATALOG_AUDIO_MATCH`` (reviewable); UNKNOWN from either is
  ``AUDIO_FINGERPRINT_UNKNOWN`` (hard stop: only a detector run can produce the result).

Every one of these codes is a hold: the gate quarantines and raises it into the platform
exception queue. Commercial-use eligibility stays separate from copyright status (P5 §34).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Final

from plm_contracts import CommercialUseEligibility, ProvenanceStatus
from plm_contracts.seams.c4 import (
    CandidateReadyPayload,
    GenerationProvenancePayload,
    RightsSnapshotPayload,
    SourceInputPayload,
)
from pydantic import StringConstraints

from ..detectors.evidence import DetectorEvaluation, detector_records
from ..detectors.outcome import DetectorOutcome
from ..detectors.reason_codes import (
    AUDIO_FINGERPRINT_UNKNOWN,
    COMMERCIAL_LICENSE_UNCERTAIN,
    INCOMPLETE_P28_PROVENANCE,
    INCOMPLETE_R6_PROVENANCE,
    INTERNAL_CATALOG_AUDIO_MATCH,
    REFERENCE_AUDIO_MATCH,
    SAMPLE_PROVENANCE_UNCERTAIN,
    UNAUTHORIZED_VOICE_CLONE_DETECTED,
    UNKNOWN_COMMERCIAL_LICENSE,
)
from ..detectors.suite import INTERNAL_CATALOG, REFERENCE_SET, AudioDetection
from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import EvidenceItem, ProvenanceCheck, RightsCheck, RiskFinding

__all__ = [
    "NON_COMMERCIAL_TIERS",
    "P28_FIELDS",
    "UNCERTAIN_DECLARATIONS",
    "VOICE_SOURCE_KINDS",
    "GenerationProvenanceFacts",
    "P28Provenance",
    "P28ValidationResult",
    "RightsValidationResult",
    "SourceInput",
    "evaluate_audio_fingerprint",
    "map_p28_provenance",
    "validate_p28_provenance",
    "validate_provenance_and_rights",
]

NON_COMMERCIAL_TIERS: Final[frozenset[str]] = frozenset({"free", "unpaid", "trial_free", "trial"})
"""Tiers whose output is never commercially usable, whatever the declared status."""

UNCERTAIN_DECLARATIONS: Final[frozenset[CommercialUseEligibility]] = frozenset(
    {CommercialUseEligibility.CONDITIONAL, CommercialUseEligibility.EXPIRED_OR_UNVERIFIED}
)
"""A license was declared but its terms are in doubt: P29 'commercial license uncertain'."""

VOICE_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {"voice_reference", "voice_model", "voice_sample", "vocal_stem"}
)
"""Source-input kinds that carry a person's voice; unlicensed, they are a voice clone."""

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# --------------------------------------------------------------------------- P28 facts


SourceInput = SourceInputPayload
"""One generation input besides the prompt; the shared C4 vocabulary (ADR-0035)."""

GenerationProvenanceFacts = GenerationProvenancePayload
"""The P28 facts of the producing attempt, carried on the C4 event (ADR-0035)."""


P28_FIELDS: Final[tuple[str, ...]] = (
    "asset_id",
    "generator",
    "model",
    "model_version",
    "account",
    "subscription_status",
    "prompt",
    "generation_timestamp",
    "source_inputs",
    "license_terms",
    "human_contribution",
    "commercial_use_eligibility",
)
"""docs/architecture.md:883-901 (PART XV P28), in order."""

P28_FROM_RIGHTS_SNAPSHOT: Final[frozenset[str]] = frozenset(
    {"account", "subscription_status", "license_terms", "commercial_use_eligibility"}
)


@dataclass(frozen=True, slots=True)
class P28Provenance:
    """The twelve P28 fields for one candidate; ``None`` is a missing fact."""

    asset_id: str | None
    generator: str | None
    model: str | None
    model_version: str | None
    account: str | None
    subscription_status: str | None
    prompt: str | None
    generation_timestamp: str | None
    source_inputs: tuple[dict[str, Any], ...] | None
    license_terms: dict[str, str] | None
    human_contribution: str | None
    commercial_use_eligibility: str | None

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in P28_FIELDS}

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in P28_FIELDS if getattr(self, name) in (None, ""))


def map_p28_provenance(event: CandidateReadyPayload) -> P28Provenance:
    """Map the C4 event, its declared rights and P4's generation facts onto P28.

    asset_id ← artifact_id; generator ← provider_id; model ← model_id; prompt ←
    prompt_text; account ← rights.account_identity_ref; subscription_status ←
    rights.plan_or_tier; license_terms ← rights.{basis, license_reference,
    terms_version}; commercial_use_eligibility ← rights.commercial_use_status;
    model_version, generation_timestamp, source_inputs, human_contribution ←
    ``event.generation_provenance`` (C4 3.0, ADR-0035).
    Nothing is defaulted: an absent source leaves its fields ``None``.
    """
    rights = event.rights
    facts = event.generation_provenance
    return P28Provenance(
        asset_id=event.artifact_id,
        generator=event.provider_id,
        model=event.model_id,
        model_version=facts.model_version if facts else None,
        account=rights.account_identity_ref if rights else None,
        subscription_status=rights.plan_or_tier if rights else None,
        prompt=event.prompt_text,
        generation_timestamp=(
            facts.generation_timestamp.isoformat()
            if facts and facts.generation_timestamp
            else None
        ),
        source_inputs=(
            tuple(s.model_dump(mode="json") for s in facts.source_inputs)
            if facts and facts.source_inputs is not None
            else None
        ),
        license_terms=(
            {
                "basis": rights.basis,
                "license_reference": rights.license_reference,
                "terms_version": rights.terms_version,
            }
            if rights
            else None
        ),
        human_contribution=facts.human_contribution if facts else None,
        commercial_use_eligibility=rights.commercial_use_status.value if rights else None,
    )


@dataclass(frozen=True, slots=True)
class P28ValidationResult:
    record: P28Provenance
    checks: tuple[ProvenanceCheck, ...]
    findings: tuple[RiskFinding, ...]
    evidence: tuple[EvidenceItem, ...]

    @property
    def status(self) -> ProvenanceStatus:
        missing = self.record.missing
        if len(missing) == len(P28_FIELDS):
            return ProvenanceStatus.MISSING
        return ProvenanceStatus.PARTIAL if missing else ProvenanceStatus.COMPLETE

    @property
    def hold_codes(self) -> tuple[str, ...]:
        return tuple(sorted({f.finding_code for f in self.findings if not f.blocking}))


def _hold(
    evaluation_id: str,
    code: str,
    category: GateCategory,
    message: str,
    *,
    hard_stop: bool,
    now: datetime,
) -> RiskFinding:
    """A finding that quarantines (not a rejection, not a warning)."""
    return RiskFinding.create(
        evaluation_id_str=evaluation_id,
        finding_code=code,
        category=category,
        severity=CheckSeverity.CRITICAL if hard_stop else CheckSeverity.HIGH,
        status="FAIL",
        confidence=1.0,
        message=message,
        blocking=False,
        now=now,
    )


def validate_p28_provenance(
    evaluation_id: str, record: P28Provenance, *, now: datetime
) -> P28ValidationResult:
    """Require every P28 field; classify declared source inputs."""
    findings: list[RiskFinding] = []
    missing = record.missing
    # The rights-snapshot fields are reported by INCOMPLETE_R6_PROVENANCE
    # (validate_provenance_and_rights); this code names every other missing field.
    other_missing = [name for name in missing if name not in P28_FROM_RIGHTS_SNAPSHOT]
    if other_missing:
        findings.append(
            _hold(
                evaluation_id,
                INCOMPLETE_P28_PROVENANCE,
                GateCategory.PROVENANCE_GATE,
                f"P28 provenance fields not declared: {', '.join(other_missing)}.",
                hard_stop=False,
                now=now,
            )
        )

    unlicensed = [s for s in (record.source_inputs or ()) if not s.get("license_ref")]
    voices = sorted(s["ref"] for s in unlicensed if s["kind"] in VOICE_SOURCE_KINDS)
    samples = sorted(s["ref"] for s in unlicensed if s["kind"] not in VOICE_SOURCE_KINDS)
    if voices:
        findings.append(
            _hold(
                evaluation_id,
                UNAUTHORIZED_VOICE_CLONE_DETECTED,
                GateCategory.RIGHTS_RISK_GATE,
                f"Voice inputs with no authorization on file: {', '.join(voices)}.",
                hard_stop=True,
                now=now,
            )
        )
    if samples:
        findings.append(
            _hold(
                evaluation_id,
                SAMPLE_PROVENANCE_UNCERTAIN,
                GateCategory.RIGHTS_RISK_GATE,
                f"Source inputs with no license on file: {', '.join(samples)}.",
                hard_stop=True,
                now=now,
            )
        )

    check = ProvenanceCheck.create(
        evaluation_id_str=evaluation_id,
        check_type="p28_provenance_completeness",
        status=CheckStatus.PASS if not findings else CheckStatus.FAIL,
        severity=CheckSeverity.INFO if not findings else CheckSeverity.HIGH,
        details={
            "missing_fields": list(missing),
            "unlicensed_source_inputs": [s["ref"] for s in unlicensed],
            "record": record.as_dict(),
        },
        now=now,
    )
    canonical = json.dumps(record.as_dict(), sort_keys=True, default=str)
    evidence = EvidenceItem.create(
        evaluation_id_str=evaluation_id,
        evidence_type="p28_provenance_record",
        source="c4_event",
        source_reference=record.asset_id or "undeclared",
        content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        summary=canonical,
        now=now,
    )
    return P28ValidationResult(
        record=record, checks=(check,), findings=tuple(findings), evidence=(evidence,)
    )


# ----------------------------------------------------------------------- commercial use


class RightsValidationResult:
    """Aggregated output of rights and provenance validation."""

    def __init__(
        self,
        rights_checks: list[RightsCheck],
        provenance_checks: list[ProvenanceCheck],
        findings: list[RiskFinding],
        evidence: list[EvidenceItem],
        commercial_use_status: CommercialUseEligibility,
        provenance_status: ProvenanceStatus,
        rights_risk: str,
    ) -> None:
        self.rights_checks = rights_checks
        self.provenance_checks = provenance_checks
        self.findings = findings
        self.evidence = evidence
        self.commercial_use_status = commercial_use_status
        self.provenance_status = provenance_status
        self.rights_risk = rights_risk

    @property
    def is_cleared(self) -> bool:
        return (
            self.commercial_use_status is CommercialUseEligibility.ELIGIBLE
            and self.provenance_status is ProvenanceStatus.COMPLETE
            and self.rights_risk in ("LOW", "NONE")
            and not self.findings
        )

    @property
    def requires_quarantine(self) -> bool:
        return bool(self.hold_codes) or (
            self.commercial_use_status is not CommercialUseEligibility.ELIGIBLE
            and not any(f.blocking for f in self.findings)
        )

    @property
    def hold_codes(self) -> tuple[str, ...]:
        return tuple(sorted({f.finding_code for f in self.findings if not f.blocking}))


def validate_provenance_and_rights(
    evaluation_id: str,
    rights: RightsSnapshotPayload | None,
    *,
    now: datetime,
) -> RightsValidationResult:
    """Judge the declared rights snapshot (or its absence) for one candidate."""
    rights_checks: list[RightsCheck] = []
    provenance_checks: list[ProvenanceCheck] = []
    findings: list[RiskFinding] = []
    evidence: list[EvidenceItem] = []

    # 1. Provenance: the R6 snapshot exists (its fields are guaranteed by the seam model).
    if rights is None:
        provenance_status = ProvenanceStatus.MISSING
        findings.append(
            _hold(
                evaluation_id,
                INCOMPLETE_R6_PROVENANCE,
                GateCategory.PROVENANCE_GATE,
                "The provider declared no license terms for the producing attempt "
                "(P28 account, subscription_status, license_terms and "
                "commercial_use_eligibility are unknown).",
                hard_stop=False,
                now=now,
            )
        )
        provenance_checks.append(
            ProvenanceCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="r6_provenance_completeness",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.HIGH,
                details={"rights_snapshot": None},
                now=now,
            )
        )
    else:
        provenance_status = ProvenanceStatus.COMPLETE
        provenance_checks.append(
            ProvenanceCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="r6_provenance_completeness",
                status=CheckStatus.PASS,
                severity=CheckSeverity.INFO,
                details={"attempt_id": rights.attempt_id, "terms_version": rights.terms_version},
                now=now,
            )
        )

    # 2. Commercial-use eligibility: declared status, which a tier can only downgrade.
    declared = rights.commercial_use_status if rights else CommercialUseEligibility.UNKNOWN
    tier = rights.plan_or_tier.strip().lower() if rights else None
    source_ref = rights.provider_id if rights else "undeclared"

    if tier in NON_COMMERCIAL_TIERS or declared is CommercialUseEligibility.INELIGIBLE:
        commercial_use_status = CommercialUseEligibility.INELIGIBLE
        rights_risk = "CRITICAL"
        code = (
            "COMMERCIAL_USE_INELIGIBLE_TIER"
            if tier in NON_COMMERCIAL_TIERS
            else "COMMERCIAL_USE_DECLARED_INELIGIBLE"
        )
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code=code,
                category=GateCategory.COMMERCIAL_USE_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=1.0,
                message=f"Output is not commercially usable (tier={tier!r}, declared={declared}).",
                blocking=True,
                now=now,
            )
        )
        check_status, risk_level, confidence = CheckStatus.FAIL, "CRITICAL", 1.0
    elif declared is CommercialUseEligibility.ELIGIBLE:
        commercial_use_status = CommercialUseEligibility.ELIGIBLE
        rights_risk = "LOW"
        check_status, risk_level, confidence = CheckStatus.PASS, "LOW", 0.95
    elif declared in UNCERTAIN_DECLARATIONS:
        # A license was declared, but conditional or expired: a determination outside
        # PLM is needed (P29 'commercial license uncertain', hard stop).
        commercial_use_status = declared
        rights_risk = "HIGH"
        findings.append(
            _hold(
                evaluation_id,
                COMMERCIAL_LICENSE_UNCERTAIN,
                GateCategory.COMMERCIAL_USE_GATE,
                f"The declared commercial-use license is {declared.value!r}: its terms "
                "need a determination before release.",
                hard_stop=True,
                now=now,
            )
        )
        check_status, risk_level, confidence = CheckStatus.FAIL, "HIGH", 0.9
    else:
        # No snapshot, or the provider declared 'unknown': no usable declaration yet.
        commercial_use_status = CommercialUseEligibility.UNKNOWN
        rights_risk = "MEDIUM"
        findings.append(
            _hold(
                evaluation_id,
                UNKNOWN_COMMERCIAL_LICENSE,
                GateCategory.COMMERCIAL_USE_GATE,
                "No commercial-use declaration is on file "
                f"({'no rights snapshot' if rights is None else 'declared unknown'}).",
                hard_stop=False,
                now=now,
            )
        )
        check_status, risk_level, confidence = CheckStatus.WARN, "MEDIUM", 0.5

    rights_checks.append(
        RightsCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="commercial_use_eligibility",
            status=check_status,
            risk_level=risk_level,
            confidence=confidence,
            source_type="provider_license_snapshot",
            source_ref=source_ref,
            evidence_data={"plan_or_tier": tier, "status": commercial_use_status.value},
            now=now,
        )
    )

    # 3. Evidence for the rights audit: the declaration itself, or its absence.
    evidence.append(
        EvidenceItem.create(
            evaluation_id_str=evaluation_id,
            evidence_type="rights_license_snapshot",
            source="music_factory_r6",
            source_reference=rights.account_identity_ref if rights else "undeclared",
            content_hash=rights.terms_version if rights else "undeclared",
            summary=(
                f"Commercial status: {commercial_use_status.value}, "
                f"Provenance: {provenance_status.value}"
            ),
            now=now,
        )
    )

    return RightsValidationResult(
        rights_checks=rights_checks,
        provenance_checks=provenance_checks,
        findings=findings,
        evidence=evidence,
        commercial_use_status=commercial_use_status,
        provenance_status=provenance_status,
        rights_risk=rights_risk,
    )


# ----------------------------------------------------------------- audio fingerprint

_AUDIO_MATCH_CODES: Final[dict[str, tuple[str, bool]]] = {
    REFERENCE_SET: (REFERENCE_AUDIO_MATCH, True),
    INTERNAL_CATALOG: (INTERNAL_CATALOG_AUDIO_MATCH, False),
}


def evaluate_audio_fingerprint(
    evaluation_id: str, detection: AudioDetection, *, now: datetime
) -> DetectorEvaluation:
    """Record each fingerprint result and raise a hold for every MATCH or UNKNOWN."""
    checks: list[RightsCheck] = []
    evidence: list[EvidenceItem] = []
    findings: list[RiskFinding] = []
    unknown: list[str] = []
    for result in detection.results:
        check, item = detector_records(
            evaluation_id, result, check_type=f"audio_fingerprint_{result.source}", now=now
        )
        checks.append(check)
        evidence.append(item)
        if result.outcome is DetectorOutcome.MATCH:
            code, hard_stop = _AUDIO_MATCH_CODES[result.source]
            findings.append(
                RiskFinding.create(
                    evaluation_id_str=evaluation_id,
                    finding_code=code,
                    category=GateCategory.RIGHTS_RISK_GATE,
                    severity=CheckSeverity.CRITICAL if hard_stop else CheckSeverity.HIGH,
                    status="FAIL",
                    confidence=result.score or 0.0,
                    message=(
                        f"Audio matches {result.source} entry {result.matched_ref!r} "
                        f"(score {result.score}, {result.metrics.get('aligned_hashes')} "
                        f"aligned hashes at {result.metrics.get('offset_seconds')} s)."
                    ),
                    blocking=False,
                    evidence_item_ids=[item.id],
                    now=now,
                )
            )
        elif result.outcome is DetectorOutcome.UNKNOWN:
            unknown.append(f"{result.source}: {result.reason}")
    if unknown:
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code=AUDIO_FINGERPRINT_UNKNOWN,
                category=GateCategory.RIGHTS_RISK_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=0.0,
                message=(
                    "Audio fingerprint check could not conclude, which is not clearance: "
                    + "; ".join(unknown)
                ),
                blocking=False,
                evidence_item_ids=[e.id for e in evidence],
                now=now,
            )
        )
    return DetectorEvaluation(
        results=detection.results,
        checks=tuple(checks),
        findings=tuple(findings),
        evidence=tuple(evidence),
    )
