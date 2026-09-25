"""Domain models for Phase P4: Music Generation Factory (§65, R6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ArtifactType,
    AttemptStatus,
    AttemptType,
    CandidateStatus,
    CommercialUseStatus,
    CostType,
    PlanStatus,
)

__all__ = [
    "AudioArtifact",
    "CostRecord",
    "CreativeVariant",
    "GeneratedCandidate",
    "GenerationAttempt",
    "GenerationPlan",
    "GenerationRequest",
    "GenerationResponse",
    "MeasuredAudio",
    "ProviderCapabilities",
    "RightsSnapshot",
]


class MeasuredAudio(BaseModel):
    """Audio physical measurement properties (§68, Seam C4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    duration_seconds: float = Field(ge=0.0)
    sample_rate: int = Field(ge=8000)
    channels: int = Field(ge=1, le=8)
    codec: str = "pcm_s16le"
    byte_size: int = Field(default=0, ge=0)
    sha256: str = ""
    mime_type: str = "audio/wav"
    probe_version: str = "probe-v1"


class ProviderCapabilities(BaseModel):
    """Capabilities discovered for a music generation provider/model (§4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    model_id: str
    max_duration_seconds: int = 300
    supports_stems: bool = True
    supports_lyrics: bool = True
    supports_instrumental: bool = True
    supported_genres: tuple[str, ...] = ("rnb", "pop", "hiphop", "electronic", "lofi")
    supported_vocal_modes: tuple[str, ...] = ("vocal", "instrumental", "mixed")


class GenerationRequest(BaseModel):
    """Normalized internal generation request sent to an audio provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    track_spec_id: str
    experiment_id: str
    prompt: str
    negative_prompt: str = ""
    duration_seconds: int = 180
    bpm: int | None = None
    vocal_mode: str = "vocal"
    language: str = "english"
    genre: str = "rnb"
    style_tags: tuple[str, ...] = ()
    seed: int = 0
    provider_id: str = "mock"
    model_id: str = "mock-v1"


class GenerationResponse(BaseModel):
    """Provider-agnostic response returned after generation submission or execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_job_id: str
    status: AttemptStatus
    audio_data: bytes | None = None
    mime_type: str = "audio/wav"
    actual_duration_seconds: float | None = None
    cost_credits: float = 0.0
    error_message: str | None = None


class CreativeVariant(BaseModel):
    """A distinct creative iteration path within a generation plan (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_id: str
    generation_plan_id: str
    variant_code: str
    parent_variant_id: str | None = None
    changed_dimensions: dict[str, Any] = Field(default_factory=dict)
    change_reason: str = "initial_seed"
    prompt_delta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GenerationPlan(BaseModel):
    """Batch plan coordinating multiple attempts to produce target candidates (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    track_spec_id: str
    experiment_id: str
    candidate_target: int = Field(default=2, ge=1)
    max_attempts: int = Field(default=5, ge=1)
    budget_limit: float = Field(default=50.0, ge=0.0)
    credit_limit: float = Field(default=100.0, ge=0.0)
    policy_version: str = "p4-v1"
    status: PlanStatus = PlanStatus.PLANNED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None


class GenerationAttempt(BaseModel):
    """One single generation call to a music provider (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str
    generation_plan_id: str
    creative_variant_id: str
    provider_id: str
    model_id: str
    provider_job_id: str
    attempt_type: AttemptType = AttemptType.INITIAL
    status: AttemptStatus = AttemptStatus.SUBMITTED
    request_hash: str
    request_payload_hash: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    cost_estimate: float = 0.0
    actual_cost: float = 0.0
    credit_estimate: float = 0.0
    actual_credits: float = 0.0
    transport_retry_of: str | None = None
    failure_code: str | None = None


class GeneratedCandidate(BaseModel):
    """A generated audio track candidate ready for evaluation or downstream QA (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    track_spec_id: str
    experiment_id: str
    generation_plan_id: str
    status: CandidateStatus = CandidateStatus.GENERATED
    provider_id: str
    model_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    parent_candidate_id: str | None = None


class AudioArtifact(BaseModel):
    """Stored audio file artifact with media probes and checksum (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    candidate_id: str
    artifact_type: ArtifactType = ArtifactType.FULL_MIX
    object_uri: str
    sha256: str
    mime_type: str = "audio/wav"
    byte_size: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    sample_rate: int = 44100
    channels: int = 2
    codec: str = "pcm_s16le"
    probe_version: str = "probe-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RightsSnapshot(BaseModel):
    """Commercial rights and license snapshot (§65, R6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    attempt_id: str
    provider_id: str
    commercial_use_status: CommercialUseStatus = CommercialUseStatus.PERMITTED
    basis: str = "paid_commercial_tier"
    license_reference: str
    policy_version: str = "p4-lic-v1"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Fields required by Ruling R6:
    plan_or_tier: str = "enterprise_commercial"
    account_identity_ref: str = "acct_plm_prod"
    terms_version: str = "terms_2026_01"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    downloaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CostRecord(BaseModel):
    """Cost bookkeeping record for generation attempts (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cost_record_id: str
    attempt_id: str
    cost_type: CostType = CostType.API_CREDIT
    estimated_amount: float = 0.0
    actual_amount: float = 0.0
    currency: str = "USD"
    credits_estimated: float = 0.0
    credits_actual: float = 0.0
    pricing_version: str = "price-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
