"""Pydantic schemas for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine).

Includes the frozen Seam C5 release manifest schema (P5 §77, R7, R8).
"""

from __future__ import annotations

from typing import Any

from plm_contracts.seams.c5 import ReleaseManifestPayload
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EvaluationRequest",
    "EvaluationResponse",
    "HoldResponse",
    "QuarantineCaseResponse",
    "QuarantineResolutionRequest",
    "ReleaseManifestPayload",
]


class EvaluationRequest(BaseModel):
    """Evaluate one candidate from its Seam C4 event alone (R4).

    P5 reads the audio from the artifact store by the event's ``artifact_uri`` and
    verifies it against ``artifact_sha256``. A caller cannot name a file: any extra
    field, ``audio_path`` included, is refused with 422.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_event: dict[str, Any]
    evaluation_policy_version: str = "p5-v1"
    trigger: str = "candidate_ready"


class EvaluationResponse(BaseModel):
    """Response containing evaluation summary."""

    evaluation_id: str
    candidate_id: str
    status: str
    disposition: str | None = None
    technical_status: str
    quality_status: str
    provenance_status: str
    rights_status: str
    policy_status: str
    composite_quality_score: float | None = None
    quarantine_case_id: str | None = None
    quarantine_reason_codes: list[str] = Field(default_factory=list)
    """The quarantine case's reason codes; empty when there is no case."""
    release_manifest_id: str | None = None


class HoldResponse(BaseModel):
    """One hold the platform exception queue keeps on a quarantine case (R3)."""

    hold_id: str
    reason_code: str
    hold_class: str
    severity: str
    override_permitted: bool
    required_evidence: list[str]
    reviewer_role: str
    policy_mapped: bool
    status: str
    resolution: str | None = None


class QuarantineCaseResponse(BaseModel):
    """Response containing quarantine case summary."""

    case_id: str
    candidate_id: str
    evaluation_id: str
    reason_codes: list[str]
    priority: str
    status: str
    assigned_to: str | None = None
    resolved_at: str | None = None
    resolution: str | None = None
    resolution_notes: str | None = None
    holds: list[HoldResponse] = Field(default_factory=list)


class QuarantineResolutionRequest(BaseModel):
    """Request to resolve a quarantine case.

    An override (CLEARED_BY_OVERRIDE) is authorized by the exception queue per hold:
    auditor_id must hold reviewer_role in the injected reviewer directory, and
    evidence must name every item the hold's policy requires (item -> reference).
    A hard stop is refused whatever the role or evidence.
    """

    resolution: str  # "CLEARED_BY_OVERRIDE", "CONFIRMED_REJECTED", "RE_EVALUATE"
    auditor_id: str
    notes: str = ""
    reviewer_role: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)
