"""Pydantic schemas for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine).

Includes the frozen Seam C5 release manifest schema (P5 §77, R7, R8).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EvaluationRequest",
    "EvaluationResponse",
    "QuarantineCaseResponse",
    "QuarantineResolutionRequest",
    "ReleaseManifestPayload",
]


class ReleaseManifestPayload(BaseModel):
    """Frozen Seam C5 release manifest payload (P5 §77, Ruling R7, R8)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    p5_manifest_id: str
    release_manifest_version: str
    candidate_id: str
    canonical_artifact_id: str
    experiment_id: str
    track_spec_id: str
    disposition: str
    technical_status: str
    commercial_use_status: str
    provenance_status: str
    ai_disclosure_required: bool
    rights_risk: str
    policy_status: str
    evaluation_id: str
    evaluation_policy_version: str
    warning_codes: list[str] = Field(default_factory=list)
    evidence_bundle_id: str


class EvaluationRequest(BaseModel):
    """Request to initiate candidate evaluation."""

    candidate_id: str
    artifact_id: str
    audio_path: str
    expected_duration_seconds: float
    track_spec_id: str
    experiment_id: str
    content_arm_id: str
    vocal_mode: str = "instrumental"
    lyrics_text: str | None = None
    prompt_text: str | None = None
    genre_tags: list[str] = Field(default_factory=list)
    rights_snapshot: dict[str, Any] = Field(default_factory=dict)
    provenance_metadata: dict[str, Any] = Field(default_factory=dict)
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
    release_manifest_id: str | None = None


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


class QuarantineResolutionRequest(BaseModel):
    """Request to resolve a quarantine case."""

    resolution: str  # "CLEARED_BY_OVERRIDE", "CONFIRMED_REJECTED", "RE_EVALUATE"
    auditor_id: str
    notes: str = ""
